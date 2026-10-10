"""Action-level checks across initial capture and the popup handoff."""

import asyncio
from copy import deepcopy
import json

import pytest

import sys
import types

from herdr_agent_rename import action
from herdr_agent_rename.context import HANDOFF_KEY, MISSING_CONTEXT, decode_handoff
from herdr_agent_rename.models import HerdrError, Invocation, Snapshot


@pytest.fixture(autouse=True)
def ui(monkeypatch):
    """The dialogs live in the UI package; these tests only need its entry points."""
    stub = types.ModuleType("herdr_agent_rename.ui")
    stub.run_dialog = stub.run_sync_dialog = None
    monkeypatch.setitem(sys.modules, "herdr_agent_rename.ui", stub)
    import herdr_agent_rename
    monkeypatch.setattr(herdr_agent_rename, "ui", stub, raising=False)
    return stub


ORIGINAL = Invocation("w1:p2", "w1", "w1:t1")


def original_env():
    return {
        "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({
            "focused_pane_id": ORIGINAL.pane_id,
            "workspace_id": ORIGINAL.workspace_id,
            "tab_id": ORIGINAL.tab_id,
        }),
        "HERDR_SOCKET_PATH": "/tmp/original-herdr.sock",
        "HERDR_BIN_PATH": "/opt/original/herdr",
        "HERDR_CONFIG_PATH": "/tmp/original-herdr.toml",
        "PATH": "/usr/bin:/bin",
    }


def original_snapshot():
    location = {
        "pane_id": ORIGINAL.pane_id, "workspace_id": ORIGINAL.workspace_id,
        "tab_id": ORIGINAL.tab_id, "terminal_id": "term-1",
    }
    return Snapshot(
        agent={**location, "agent": "codex", "name": "reviewer", "agent_session": None},
        pane={**location, "agent": "codex", "label": "backend-api", "agent_session": None},
        process={"pane_id": ORIGINAL.pane_id},
    )


class RecordingClient:
    def __init__(self, env, events, initial, lookup_error=None):
        self.env = dict(env)
        self.events = events
        self.initial = initial
        self.lookup_error = lookup_error
        self.popup = None
        self.writes = []

    def verify_support(self):
        self.events.append(("support",))

    def get_snapshot(self, invocation):
        self.events.append(("lookup", invocation))
        if self.lookup_error:
            raise self.lookup_error
        return deepcopy(self.initial)

    def open_popup(self, invocation, mode, handoff):
        self.events.append(("popup", invocation, mode))
        self.popup = (invocation, mode, handoff)

    def rename(self, pane_id, name):
        self.writes.append((pane_id, name))
        raise AssertionError("These action tests never authorize a rename.")

    def notify(self, title):
        raise AssertionError("Cancellation cannot notify success.")


def install_client_factory(monkeypatch, *, initial=None, lookup_error=None):
    clients, events = [], []
    initial = initial or original_snapshot()

    def factory(env):
        client = RecordingClient(env, events, initial, lookup_error)
        clients.append(client)
        return client

    monkeypatch.setattr(action, "HerdrClient", factory)
    return clients, events


@pytest.mark.parametrize("context", [None, "{", "{}", "null", '{"focused_pane_id":""}'])
def test_launch_with_missing_context_never_queries_another_pane(monkeypatch, context):
    clients, events = install_client_factory(monkeypatch)
    env = original_env()
    if context is None:
        del env["HERDR_PLUGIN_CONTEXT_JSON"]
    else:
        env["HERDR_PLUGIN_CONTEXT_JSON"] = context
    env["HERDR_PANE_ID"] = "w9:p9"  # Popup/focus variables are not a fallback.

    action.launch("open", env)

    assert not any(event[0] == "lookup" for event in events)
    invocation, mode, handoff = clients[0].popup
    assert invocation == Invocation("") and mode == "open"
    captured, initial, error, _ = decode_handoff({HANDOFF_KEY: handoff})
    assert captured == Invocation("") and initial is None and error == MISSING_CONTEXT
    assert clients[0].writes == []


@pytest.mark.parametrize("mode", ["open"])
def test_initial_lookup_finishes_before_popup_and_handoff_contains_snapshot(monkeypatch, mode):
    clients, events = install_client_factory(monkeypatch)
    action.launch(mode, original_env())

    assert events == [("support",), ("lookup", ORIGINAL), ("popup", ORIGINAL, mode)]
    invocation, captured, error, connection = decode_handoff({HANDOFF_KEY: clients[0].popup[2]})
    assert invocation == ORIGINAL and captured == original_snapshot() and error == ""
    assert connection == {
        key: original_env()[key]
        for key in ("HERDR_SOCKET_PATH", "HERDR_BIN_PATH", "HERDR_CONFIG_PATH")
    }
    assert clients[0].writes == []


def test_dialog_uses_frozen_invocation_and_original_connection_despite_changed_popup_context(monkeypatch, ui):
    clients, events = install_client_factory(monkeypatch)
    action.launch("open", original_env())
    popup_env = {
        **original_env(), HANDOFF_KEY: clients[0].popup[2],
        "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({
            "focused_pane_id": "w9:p9", "workspace_id": "w9", "tab_id": "w9:t9",
        }),
        "HERDR_PANE_ID": "w9:p9",
        "HERDR_SOCKET_PATH": "/tmp/popup-other-server.sock",
        "HERDR_BIN_PATH": "/opt/popup/herdr",
        "HERDR_CONFIG_PATH": "/tmp/popup-other-config.toml",
    }
    seen = []

    async def cancel_dialog(subject):
        seen.append(subject)
        assert subject.invocation == ORIGINAL
        assert subject.snapshot == original_snapshot()
        assert subject.initial_agent_name == "reviewer"
        assert subject.proposed_name == "reviewer"
        assert subject.cancel()
        return False

    monkeypatch.setattr(ui, "run_dialog", cancel_dialog)
    assert not asyncio.run(action.dialog("open", popup_env))

    assert len(seen) == 1 and len(clients) == 2
    assert clients[1].env["HERDR_SOCKET_PATH"] == original_env()["HERDR_SOCKET_PATH"]
    assert clients[1].env["HERDR_BIN_PATH"] == original_env()["HERDR_BIN_PATH"]
    assert clients[1].env["HERDR_CONFIG_PATH"] == original_env()["HERDR_CONFIG_PATH"]
    assert clients[1].env["HERDR_PLUGIN_CONTEXT_JSON"] == popup_env["HERDR_PLUGIN_CONTEXT_JSON"]
    assert sum(event[0] == "lookup" for event in events) == 1
    assert all(client.writes == [] for client in clients)


def test_failed_initial_agent_lookup_opens_a_disabled_but_cancellable_dialog(monkeypatch, ui):
    message = "No running agent was found in this pane."
    clients, events = install_client_factory(monkeypatch, lookup_error=HerdrError(message))
    action.launch("open", original_env())
    handoff = clients[0].popup[2]
    invocation, captured, error, _ = decode_handoff({HANDOFF_KEY: handoff})
    assert invocation == ORIGINAL and captured is None and error == message

    async def verify_error_dialog(subject):
        assert subject.state == "error" and subject.message == message
        assert not subject.can_submit
        assert not await subject.submit()
        assert subject.cancel() and subject.state == "closed"
        return False

    monkeypatch.setattr(ui, "run_dialog", verify_error_dialog)
    assert not asyncio.run(action.dialog("open", {**original_env(), HANDOFF_KEY: handoff}))
    assert sum(event[0] == "lookup" for event in events) == 1
    assert all(client.writes == [] for client in clients)


def test_missing_handoff_never_recaptures_focused_pane_and_still_allows_cancel(monkeypatch, ui):
    clients, events = install_client_factory(monkeypatch)

    async def verify_error_dialog(subject):
        assert subject.invocation == Invocation("") and subject.snapshot is None
        assert subject.message == MISSING_CONTEXT and not subject.can_submit
        assert subject.cancel()
        return False

    monkeypatch.setattr(ui, "run_dialog", verify_error_dialog)
    assert not asyncio.run(action.dialog("open", original_env()))
    assert events == [("support",)] and clients[0].writes == []


def test_movement_during_initial_capture_opens_disabled_handoff_without_redirecting(monkeypatch):
    moved = original_snapshot()
    moved.pane["pane_id"] = "w1:p3"
    moved.agent["pane_id"] = "w1:p3"
    clients, _ = install_client_factory(monkeypatch, initial=moved)
    action.launch("open", original_env())
    invocation, captured, error, _ = decode_handoff({HANDOFF_KEY: clients[0].popup[2]})
    assert invocation == ORIGINAL and captured is None
    assert error == "The target pane has moved. Reopen the dialog."
    assert clients[0].writes == []


def test_failed_popup_launch_removes_private_large_handoff(monkeypatch, tmp_path):
    initial = original_snapshot()
    initial.process["foreground_processes"] = [{"pid": 1, "name": "codex", "argv": ["codex", "x" * 80000], "cmdline": "x" * 80000}]
    install_client_factory(monkeypatch, initial=initial)
    def fail_popup(self, invocation, mode, handoff):
        assert list(tmp_path.glob("agent-rename-handoff-*.json"))
        raise HerdrError("Popup is busy.")
    monkeypatch.setattr(RecordingClient, "open_popup", fail_popup)
    env = {**original_env(), "HERDR_PLUGIN_STATE_DIR": str(tmp_path)}
    with pytest.raises(HerdrError, match="Popup is busy"):
        action.launch("open", env)
    assert not list(tmp_path.iterdir())


# --- workspace sync ---------------------------------------------------------

from sync_support import Fake, world  # noqa: E402
from herdr_agent_rename.context import decode_sync_handoff  # noqa: E402
from herdr_agent_rename.sync import RowStatus  # noqa: E402


def sync_env(**context):
    env = original_env()
    env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(context or {"focused_pane_id": "w1:p1", "workspace_id": "w1", "tab_id": "w1:t1"})
    return env


def install_fake(monkeypatch, *specs):
    fake = Fake(*world(*specs))
    monkeypatch.setattr(action, "HerdrClient", lambda env: fake)
    return fake


def test_sync_with_nothing_to_do_notifies_and_opens_no_popup(monkeypatch):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "aa"), ("w1:p2", None, "q"), ("w1:p3", "zz", "-"))
    action.launch("sync", sync_env())
    assert fake.notifications == ["All agent names already match their pane names."]
    assert fake.popups == [] and fake.writes == []


def test_sync_with_only_problem_rows_opens_the_popup_in_info_mode(monkeypatch, ui):
    fake = install_fake(monkeypatch, ("w1:p1", "Bad", "q"))
    action.launch("sync", sync_env())
    assert fake.notifications == [] and len(fake.popups) == 1 and fake.popups[0][1] == "sync"
    seen = []

    async def show(runner):
        seen.append(runner)
        return False
    ui.run_sync_dialog = show
    assert not asyncio.run(action.dialog("sync", {**sync_env(), HANDOFF_KEY: fake.popups[0][2]}))
    assert seen[0].mode == "info" and seen[0].plan.rows[0].status is RowStatus.INVALID_LABEL
    assert fake.writes == []


def test_sync_with_eligible_rows_opens_confirm_mode_with_the_frozen_plan(monkeypatch, ui):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "x"), ("w1:p2", "bb", "-"), ("w1:p3", "cc", None, "t2"))
    action.launch("sync", sync_env())
    invocation, mode, handoff = fake.popups[0]
    assert invocation == Invocation("w1:p1", "w1", "w1:t1") and mode == "sync"
    frozen, plan, error, connection = decode_sync_handoff({HANDOFF_KEY: handoff})
    assert error == "" and plan.workspace_id == "w1" and len(plan.eligible) == 2
    assert set(plan.snapshots) == {"w1:p1", "w1:p3"}
    assert connection["HERDR_SOCKET_PATH"] == original_env()["HERDR_SOCKET_PATH"]
    modes = []

    async def show(runner):
        modes.append((runner.mode, runner.plan))
        return False
    ui.run_sync_dialog = show
    # A later panes change cannot alter the frozen plan seen by the popup.
    fake.panes.append({**fake.panes[0], "pane_id": "w1:p9", "label": "new"})
    asyncio.run(action.dialog("sync", {**sync_env(), HANDOFF_KEY: handoff}))
    assert modes[0][0] == "confirm" and modes[0][1] == plan and fake.writes == []


def test_sync_workspace_comes_from_context_without_extra_pane_lookup(monkeypatch):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "x"))
    action.launch("sync", sync_env(workspace_id="w1"))
    assert "pane_get" not in fake.calls and fake.popups[0][0] == Invocation("", "w1")


def test_sync_workspace_falls_back_to_the_invoking_panes_canonical_record(monkeypatch):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "x"))
    action.launch("sync", sync_env(focused_pane_id="w1:p1"))
    assert fake.calls.count("pane_get") == 1
    assert fake.popups[0][0] == Invocation("w1:p1", "w1", "w1:t1")


@pytest.mark.parametrize("context", [None, "{", "{}", '{"focused_pane_id":"--x"}'])
def test_sync_without_a_workspace_notifies_and_fails_without_popup(monkeypatch, context):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "x"))
    env = original_env()
    env["HERDR_PANE_ID"] = "w1:p1"  # popup/focus variables are never a fallback
    if context is None:
        del env["HERDR_PLUGIN_CONTEXT_JSON"]
    else:
        env["HERDR_PLUGIN_CONTEXT_JSON"] = context
    with pytest.raises(HerdrError, match="original pane"):
        action.launch("sync", env)
    assert fake.notifications == [MISSING_CONTEXT] and fake.popups == [] and "pane_get" not in fake.calls


def test_sync_fallback_ignores_a_moved_or_missing_pane(monkeypatch):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "x"))
    with pytest.raises(HerdrError):
        action.launch("sync", sync_env(focused_pane_id="w1:p7"))
    assert fake.popups == [] and len(fake.notifications) == 1


def test_sync_unsupported_api_notifies_and_fails(monkeypatch):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "x"))
    fake.verify_support = lambda: (_ for _ in ()).throw(HerdrError("This Herdr version is not supported.", code="unsupported"))
    with pytest.raises(HerdrError, match="not supported"):
        action.launch("sync", sync_env())
    assert fake.notifications == ["This Herdr version is not supported."] and fake.popups == []


def test_failed_sync_popup_launch_removes_the_large_plan_file(monkeypatch, tmp_path):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "x"))
    fake.agents[0]["agent"] = "codex"
    original = fake.get_snapshot

    def big(invocation):
        snapshot = original(invocation)
        snapshot.process["foreground_processes"] = [{"pid": 1, "name": "codex", "argv": ["x" * 80000]}]
        return snapshot
    fake.get_snapshot = big

    def fail(invocation, mode, handoff):
        assert list(tmp_path.glob("agent-rename-handoff-*.json"))
        raise HerdrError("Popup is busy.")
    fake.open_popup = fail
    with pytest.raises(HerdrError, match="busy"):
        action.launch("sync", {**sync_env(), "HERDR_PLUGIN_STATE_DIR": str(tmp_path)})
    assert not list(tmp_path.iterdir())


def test_sync_dialog_with_a_broken_handoff_reports_an_error_runner(monkeypatch, ui):
    fake = install_fake(monkeypatch, ("w1:p1", "aa", "x"))
    runners = []

    async def show(runner):
        runners.append(runner)
        return False
    ui.run_sync_dialog = show
    asyncio.run(action.dialog("sync", {**original_env(), HANDOFF_KEY: "not json"}))
    assert runners[0].mode == "error" and runners[0].error == MISSING_CONTEXT
