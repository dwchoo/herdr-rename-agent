import asyncio
from copy import deepcopy
from threading import Event

import pytest

from herdr_agent_rename.controller import Controller, UNCERTAIN_MESSAGE
from herdr_agent_rename.models import HerdrError, Invocation, Snapshot


INVOCATION = Invocation("pane-1", "workspace-1", "tab-1")


def snapshot(name="reviewer", label="backend-api", session="session-1"):
    location = {
        "pane_id": "pane-1", "terminal_id": "terminal-1",
        "workspace_id": "workspace-1", "tab_id": "tab-1",
    }
    return Snapshot(
        agent={
            **location, "agent": "codex", "name": name, "agent_status": "working",
            "agent_session": {"agent": "codex", "kind": "id", "source": "env", "value": session}
            if session is not None else None,
            "revision": 1,
        },
        pane={**location, "label": label, "revision": 1},
        process={
            "pane_id": "pane-1",
            "foreground_processes": [
                {"pid": 101, "name": "codex", "argv": ["codex"], "cwd": "/old"}
            ],
        },
    )


class Client:
    def __init__(self, initial, lookups=(), rename_error=None, apply_on_error=False):
        self.current = deepcopy(initial)
        self.lookups = list(lookups)
        self.rename_error = rename_error
        self.apply_on_error = apply_on_error
        self.reads = []
        self.writes = []

    def get_snapshot(self, invocation):
        self.reads.append(invocation)
        result = self.lookups.pop(0) if self.lookups else self.current
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)

    def rename(self, pane_id, name):
        self.writes.append((pane_id, name))
        if self.rename_error is None or self.apply_on_error:
            self.current.agent["name"] = name
        if self.rename_error:
            raise self.rename_error
        return deepcopy(self.current.agent)

    def clear_name(self, pane_id):
        self.writes.append((pane_id, None))
        if self.rename_error is None or self.apply_on_error:
            self.current.agent.pop("name", None)
        if self.rename_error:
            raise self.rename_error
        return deepcopy(self.current.agent)


def controller(mode="open", initial=None, **client_options):
    initial = initial or snapshot()
    client = Client(initial, **client_options)
    return Controller(client, INVOCATION, initial, mode), client


def submit(subject, name=None):
    return asyncio.run(subject.submit(name))


def test_rename_reads_before_and_after_one_write_to_the_frozen_target():
    subject, client = controller()
    assert submit(subject, "new-agent")
    assert subject.state == "closed"
    assert client.writes == [("pane-1", "new-agent")]
    assert client.reads == [INVOCATION, INVOCATION]


def test_cancel_closes_without_read_or_write():
    subject, client = controller()
    assert subject.cancel()
    assert subject.state == "closed"
    assert not submit(subject, "new-agent")
    assert client.reads == client.writes == []


@pytest.mark.parametrize("name", ["Bad", "new agent", "new-agent🙂", "a\x1fb"])
def test_invalid_input_is_retained_and_never_queries_or_writes(name):
    subject, client = controller()
    assert not submit(subject, name)
    assert subject.proposed_name == name
    assert subject.state == "error" and subject.can_submit
    assert client.reads == client.writes == []
    assert submit(subject, "corrected")


def test_unnamed_live_agent_can_be_renamed():
    initial = snapshot(name=None)
    subject, client = controller(initial=initial)
    assert subject.initial_agent_name == "" and subject.can_submit
    assert "no name" in subject.message
    assert submit(subject, "named")


def test_same_name_revalidates_but_never_writes():
    subject, client = controller(initial=snapshot(name="backend-api"))
    assert submit(subject, "backend-api")
    assert client.reads == [INVOCATION]
    assert client.writes == []


def test_duplicate_name_retains_input_and_allows_explicit_retry():
    error = HerdrError("This agent name is already in use.", code="duplicate_name")
    subject, client = controller(rename_error=error)
    assert not submit(subject, "taken-name")
    assert subject.proposed_name == "taken-name"
    assert subject.message == str(error) and subject.can_submit
    assert len(client.writes) == 1
    client.rename_error = None
    assert submit(subject, "available")
    assert client.writes == [("pane-1", "taken-name"), ("pane-1", "available")]


@pytest.mark.parametrize("section,key,value", [
    ("pane", "pane_id", "pane-2"),
    ("pane", "workspace_id", "workspace-2"),
    ("pane", "tab_id", "tab-2"),
    ("agent", "pane_id", "pane-2"),
    ("agent", "terminal_id", "terminal-2"),
    ("agent", "agent", "claude"),
    ("agent", "agent_session", None),
    ("agent", "agent", None),
    ("process", "pane_id", "pane-2"),
])
def test_exit_replacement_and_alias_movement_stop_without_writing(section, key, value):
    changed = snapshot()
    getattr(changed, section)[key] = value
    subject, client = controller(lookups=[changed])
    assert not submit(subject, "new-name")
    assert subject.state == "error"
    assert client.writes == []


def test_changed_session_value_stops_without_writing():
    subject, client = controller(lookups=[snapshot(session="new-session")])
    assert not submit(subject, "new-name")
    assert "target agent has changed" in subject.message
    assert client.writes == []


def test_status_revision_focus_and_cwd_changes_are_not_replacement():
    changed = snapshot()
    changed.agent.update(agent_status="idle", revision=99, focused=False)
    changed.pane.update(revision=99, focused=False)
    changed.process["foreground_processes"][0]["cwd"] = "/new"
    subject, client = controller(lookups=[changed])
    assert submit(subject, "new-name")
    assert len(client.writes) == 1


@pytest.mark.parametrize("processes", [
    [],
    [{"pid": 999, "name": "codex", "argv": ["codex"]}],
    [{"pid": 101, "name": "bash", "argv": ["bash"]}],
])
def test_foreground_replacement_is_detected_when_public_process_data_is_available(processes):
    changed = snapshot(session=None)
    changed.process["foreground_processes"] = processes
    subject, client = controller(initial=snapshot(session=None), lookups=[changed])
    assert not submit(subject, "new-name")
    assert client.writes == []


def test_child_process_churn_preserves_a_common_agent_process():
    changed = snapshot()
    changed.process["foreground_processes"].append({"pid": 200, "name": "rg"})
    subject, client = controller(lookups=[changed])
    assert submit(subject, "new-name")


def test_unavailable_process_data_does_not_invent_a_process_identity():
    initial = snapshot(session=None)
    initial.process.clear()
    subject, client = controller(initial=initial)
    assert submit(subject, "new-name")


def test_initial_capture_is_defensive_against_mutating_the_callers_dictionaries():
    initial = snapshot()
    subject, client = controller(initial=initial)
    initial.agent["agent_session"]["value"] = "mutation"
    initial.pane["label"] = "mutation"
    assert submit(subject, "new-name")


def test_initial_context_location_mismatch_disables_writes():
    initial = snapshot()
    initial.pane["workspace_id"] = "moved-workspace"
    subject, client = controller(initial=initial)
    assert not subject.can_submit
    assert not submit(subject, "new-name") and client.writes == []


def test_missing_capture_and_initial_error_offer_only_cancellation():
    client = Client(snapshot())
    subject = Controller(client, INVOCATION, None, "open", "Could not connect to Herdr.")
    assert subject.message == "Could not connect to Herdr."
    assert not subject.can_submit and not submit(subject, "new-name")
    assert subject.cancel()


def test_lookup_failure_retains_input_and_explicit_retry_rechecks():
    subject, client = controller(lookups=[HerdrError("Could not connect to Herdr.")])
    assert not submit(subject, "new-name")
    assert subject.proposed_name == "new-name" and subject.can_submit
    assert client.writes == []
    assert submit(subject, "new-name")
    assert client.reads == [INVOCATION, INVOCATION, INVOCATION]


@pytest.mark.parametrize("code", [
    "agent_not_found", "pane_not_found", "method_not_found", "unknown_method", "unsupported",
])
@pytest.mark.parametrize("stage", ["lookup", "rename"])
def test_fatal_public_error_codes_require_reopening_even_if_later_lookup_would_match(code, stage):
    # A restart may reuse a public session reference and process signature. Once
    # an exit or missing API is observed, do not let a retry accept that occupant.
    error = HerdrError("Target unavailable.", code=code)
    options = {"lookups": [error]} if stage == "lookup" else {"rename_error": error}
    subject, client = controller(**options)
    assert not submit(subject, "new-name")
    assert subject.state == "error" and not subject.can_submit
    assert not submit(subject, "new-name")
    assert client.reads == [INVOCATION]
    assert client.writes == ([] if stage == "lookup" else [("pane-1", "new-name")])
    assert subject.cancel()


def test_uncertain_applied_write_reconciles_on_user_retry_without_another_write():
    error = HerdrError(UNCERTAIN_MESSAGE, uncertain=True)
    subject, client = controller(rename_error=error, apply_on_error=True)
    name = "new-name"
    assert not submit(subject, name)
    assert subject.state == "error" and subject.can_submit
    assert len(client.writes) == 1
    assert submit(subject, name)
    assert len(client.writes) == 1 and subject.state == "closed"


def test_uncertain_unapplied_write_requires_reopening_after_a_fresh_lookup():
    subject, client = controller(rename_error=HerdrError(UNCERTAIN_MESSAGE, uncertain=True))
    assert not submit(subject, "new-name")
    assert not submit(subject, "new-name")
    assert "Reopen the dialog" in subject.message and not subject.can_submit
    assert len(client.writes) == 1 and len(client.reads) == 2


def test_success_envelope_without_verified_name_does_not_close_or_repeat_write():
    initial = snapshot()
    subject, client = controller(lookups=[initial, initial])
    assert not submit(subject, "new-name")
    assert subject.state == "error" and UNCERTAIN_MESSAGE in subject.message
    assert len(client.writes) == 1
    # The subsequent fresh read reveals that the first write did apply.
    assert submit(subject, "new-name")
    assert len(client.writes) == 1


def test_post_write_lookup_failure_is_uncertain_and_then_reconciles():
    initial = snapshot()
    subject, client = controller(
        lookups=[initial, HerdrError("Could not connect to Herdr.")]
    )
    assert not submit(subject, "new-name")
    assert UNCERTAIN_MESSAGE in subject.message
    assert "Could not connect to Herdr." in subject.message
    assert submit(subject, "new-name")
    assert len(client.writes) == 1


def test_post_write_identity_change_is_not_accepted_as_success():
    changed = snapshot(name="new-name", session="new-session")
    subject, client = controller(lookups=[snapshot(), changed])
    assert not submit(subject, "new-name")
    assert subject.state == "error" and not subject.can_submit
    assert "target agent has changed" in subject.message
    assert UNCERTAIN_MESSAGE in subject.message
    assert len(client.writes) == 1


def test_simultaneous_submits_are_locked_before_await_and_cancel_is_disabled():
    subject, client = controller()
    started, release = Event(), Event()
    original_get = client.get_snapshot

    def delayed_get(invocation):
        started.set()
        if not release.wait(3):
            raise RuntimeError("Test lookup did not resume.")
        return original_get(invocation)

    client.get_snapshot = delayed_get

    async def run():
        task = asyncio.create_task(subject.submit("new-name"))
        assert await asyncio.to_thread(started.wait, 3)
        try:
            assert subject.busy and subject.state == "validating"
            assert not subject.can_submit and not subject.cancel()
            assert not await subject.submit("other-name")
        finally:
            release.set()
        assert await task

    asyncio.run(run())
    assert len(client.writes) == 1 and client.writes[0][1] == "new-name"
    assert not subject.busy


def test_external_task_cancel_waits_for_preflight_then_closes_without_a_write():
    subject, client = controller()
    started, release = Event(), Event()
    original_get = client.get_snapshot

    def delayed_get(invocation):
        started.set()
        if not release.wait(3):
            raise RuntimeError("Test lookup did not resume.")
        return original_get(invocation)

    client.get_snapshot = delayed_get

    async def run():
        task = asyncio.create_task(subject.submit("new-name"))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        try:
            assert subject.busy and not task.done()
            assert not subject.can_submit and not subject.cancel()
        finally:
            release.set()
        assert not await task

    asyncio.run(run())
    assert subject.state == "closed" and not subject.busy
    assert client.writes == []


def test_external_task_cancel_during_mutation_finishes_and_verifies_that_one_write():
    subject, client = controller()
    started, release = Event(), Event()
    original_rename = client.rename

    def delayed_rename(pane_id, name):
        started.set()
        if not release.wait(3):
            raise RuntimeError("Test rename did not resume.")
        return original_rename(pane_id, name)

    client.rename = delayed_rename

    async def run():
        task = asyncio.create_task(subject.submit("new-name"))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        try:
            assert subject.busy and subject.state == "submitting" and not task.done()
            assert not subject.can_submit and not subject.cancel()
            assert not await subject.submit("second-name")
        finally:
            release.set()
        assert await task

    asyncio.run(run())
    assert subject.state == "closed" and not subject.busy
    assert client.writes == [("pane-1", "new-name")]
    assert client.reads == [INVOCATION, INVOCATION]


def test_loop_teardown_cancellation_blocks_retry_while_worker_may_continue():
    subject, client = controller()
    started, release = Event(), Event()
    original_rename = client.rename

    def delayed_rename(pane_id, name):
        started.set()
        if not release.wait(3):
            raise RuntimeError("Test rename did not resume.")
        return original_rename(pane_id, name)

    client.rename = delayed_rename

    async def run():
        task = asyncio.create_task(subject.submit("new-name"))
        assert await asyncio.to_thread(started.wait, 3)
        # Event-loop shutdown cancels inner tasks as well as the dialog task;
        # canceling an asyncio worker task cannot stop its running native thread.
        workers = asyncio.all_tasks() - {asyncio.current_task(), task}
        assert workers
        for worker in workers:
            worker.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert subject.state == "error" and not subject.can_submit
            assert UNCERTAIN_MESSAGE in subject.message
            assert not await subject.submit("new-name")
        finally:
            release.set()

    asyncio.run(run())
    assert client.writes == [("pane-1", "new-name")]
    assert client.reads == [INVOCATION]


@pytest.mark.parametrize("raw", [" new-agent", "new-agent\n", "\t new-agent\u3000", "\u00a0\u2028new-agent\u2029\u0085"])
def test_rust_whitespace_is_trimmed_before_validation_and_write(raw):
    subject, client = controller()
    assert submit(subject, raw)
    assert client.writes == [("pane-1", "new-agent")]


@pytest.mark.parametrize("raw", ["\x1cnew-agent", "new-agent\x1f", "\u200bnew-agent", "\ufeffnew-agent"])
def test_non_rust_whitespace_is_not_trimmed_so_validation_rejects_it(raw):
    subject, client = controller()
    assert not submit(subject, raw)
    assert subject.state == "error" and client.reads == client.writes == []


def test_trim_name_matches_unicode_white_space_exactly():
    from herdr_agent_rename.controller import trim_name
    spaces = "\t\n\v\f\r \x85\xa0\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000"
    assert trim_name(spaces + "x y" + spaces) == "x y"
    assert trim_name("\x1cx\x1f") == "\x1cx\x1f"


@pytest.mark.parametrize("raw", ["", "  \n\t "])
def test_empty_input_clears_the_name_with_one_write_and_verification(raw):
    subject, client = controller()
    assert submit(subject, raw)
    assert client.writes == [("pane-1", None)]
    assert client.reads == [INVOCATION, INVOCATION]
    assert "name" not in client.current.agent and subject.state == "closed"


def test_clear_of_an_already_unnamed_agent_is_a_zero_write_close():
    subject, client = controller(initial=snapshot(name=None))
    assert submit(subject, "")
    assert client.writes == [] and client.reads == [INVOCATION]
    assert subject.state == "closed"


def test_clear_revalidates_and_skips_the_write_when_the_target_changed():
    changed = snapshot()
    changed.agent["terminal_id"] = changed.pane["terminal_id"] = "terminal-2"
    subject, client = controller(lookups=[changed])
    assert not submit(subject, "")
    assert client.writes == [] and not subject.can_submit


def test_uncertain_clear_reconciles_on_retry_without_a_second_write():
    subject, client = controller(rename_error=HerdrError(UNCERTAIN_MESSAGE, uncertain=True), apply_on_error=True)
    assert not submit(subject, "")
    assert subject.state == "error" and client.writes == [("pane-1", None)]
    assert subject.can_submit
    assert submit(subject, "")
    assert client.writes == [("pane-1", None)] and subject.state == "closed"


def test_uncertain_clear_that_did_not_apply_requires_reopening():
    subject, client = controller(rename_error=HerdrError(UNCERTAIN_MESSAGE, uncertain=True))
    assert not submit(subject, "")
    assert not submit(subject, "")
    assert client.writes == [("pane-1", None)] and not subject.can_submit


def test_clear_not_confirmed_after_the_write_is_uncertain():
    initial = snapshot()
    subject, client = controller(initial=initial)
    client.clear_name = lambda pane: client.writes.append((pane, None)) or {}
    assert not submit(subject, "")
    assert "could not be confirmed" in subject.message and client.writes == [("pane-1", None)]
