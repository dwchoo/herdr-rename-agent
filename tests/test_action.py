"""Action-level checks across initial capture and the popup handoff."""

import asyncio
from copy import deepcopy
import json

import pytest

from herdr_agent_rename import action, ui
from herdr_agent_rename.context import HANDOFF_KEY, MISSING_CONTEXT, decode_handoff
from herdr_agent_rename.models import HerdrError, Invocation, Snapshot


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


@pytest.mark.parametrize("mode", ["open", "sync"])
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


def test_dialog_uses_frozen_invocation_and_original_connection_despite_changed_popup_context(monkeypatch):
    clients, events = install_client_factory(monkeypatch)
    action.launch("sync", original_env())
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
        assert subject.proposed_name == "backend-api"
        assert subject.cancel()
        return False

    monkeypatch.setattr(ui, "run_dialog", cancel_dialog)
    assert not asyncio.run(action.dialog("sync", popup_env))

    assert len(seen) == 1 and len(clients) == 2
    assert clients[1].env["HERDR_SOCKET_PATH"] == original_env()["HERDR_SOCKET_PATH"]
    assert clients[1].env["HERDR_BIN_PATH"] == original_env()["HERDR_BIN_PATH"]
    assert clients[1].env["HERDR_CONFIG_PATH"] == original_env()["HERDR_CONFIG_PATH"]
    assert clients[1].env["HERDR_PLUGIN_CONTEXT_JSON"] == popup_env["HERDR_PLUGIN_CONTEXT_JSON"]
    assert sum(event[0] == "lookup" for event in events) == 1
    assert all(client.writes == [] for client in clients)


def test_failed_initial_agent_lookup_opens_a_disabled_but_cancellable_dialog(monkeypatch):
    message = "No running agent was found in this pane."
    clients, events = install_client_factory(monkeypatch, lookup_error=HerdrError(message))
    action.launch("sync", original_env())
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
    assert not asyncio.run(action.dialog("sync", {**original_env(), HANDOFF_KEY: handoff}))
    assert sum(event[0] == "lookup" for event in events) == 1
    assert all(client.writes == [] for client in clients)


def test_missing_handoff_never_recaptures_focused_pane_and_still_allows_cancel(monkeypatch):
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
