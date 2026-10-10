"""Workspace sync planner and executor against fixtures shaped like Herdr 0.9.3."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from herdr_agent_rename.models import HerdrError
from sync_support import Fake, agent, observed, pane, prepared, snapshot_of, world
from herdr_agent_rename.sync import (
    RowOutcome, RowStatus, SyncRunner, build_plan, collect_plan, plan_from_dict, plan_to_dict,
)

REAL = json.loads((Path(__file__).parent / "fixtures/sync-0.9.3.json").read_text())
UNCERTAIN = "The rename result could not be confirmed. Check the current name before retrying."


def statuses(plan):
    return {row.pane_id: row.status for row in plan.rows}


# --- planner ---------------------------------------------------------------

def test_real_fixture_classification_order_and_tab_info():
    panes = REAL["pane_list_ws1"]["result"]["panes"]
    agents = REAL["agent_list"]["result"]["agents"]
    tabs = REAL["tab_list"]["result"]["tabs"]
    plan = build_plan("w1", panes, agents, tabs, None, "agent-rename")
    assert [r.pane_id for r in plan.rows] == ["w1:p1", "w1:p3", "w1:p2", "w1:p4", "w1:p5"]
    assert [r.status for r in plan.rows] == [
        RowStatus.ELIGIBLE, RowStatus.NO_AGENT, RowStatus.NO_PANE_NAME, RowStatus.NO_PANE_NAME, RowStatus.NO_AGENT]
    first = plan.rows[0]
    assert (first.label, first.agent_name, first.tab_label, first.tab_number) == ("tester", "alpha", "1", 1)
    assert plan.rows[3].tab_label == "second" and plan.rows[3].agent_name == "beta"
    assert plan.workspace_label == "agent-rename" and plan.mode == "confirm"
    assert plan.counts[RowStatus.ELIGIBLE] == 1


def test_other_workspaces_are_excluded_even_if_returned():
    panes = REAL["pane_list_all"]["result"]["panes"]
    plan = build_plan("w2", panes, REAL["agent_list"]["result"]["agents"])
    assert [r.pane_id for r in plan.rows] == ["w2:p1"]


def test_unnamed_agent_is_eligible_and_matching_is_a_noop_row():
    panes = [pane("w1:p1", label="alpha"), pane("w1:p2", label="beta")]
    agents = [agent("w1:p1"), agent("w1:p2", "beta")]
    plan = build_plan("w1", panes, agents)
    assert statuses(plan) == {"w1:p1": RowStatus.ELIGIBLE, "w1:p2": RowStatus.MATCHING}
    assert plan.rows[0].agent_name is None


def test_rows_follow_tab_number_then_pane_order():
    panes = [pane("w1:p1", "t2", "bb"), pane("w1:p2", "t1", "aa"), pane("w1:p3", "t2", "cc")]
    tabs = [{"tab_id": "w1:t2", "number": 2, "label": "two", "workspace_id": "w1"},
            {"tab_id": "w1:t1", "number": 1, "label": "one", "workspace_id": "w1"}]
    agents = [agent(p["pane_id"]) for p in panes]
    assert [r.pane_id for r in build_plan("w1", panes, agents, tabs).rows] == ["w1:p2", "w1:p1", "w1:p3"]
    # Without tab info, first appearance orders tabs and pane order is kept.
    assert [r.pane_id for r in build_plan("w1", panes, agents).rows] == ["w1:p1", "w1:p3", "w1:p2"]


def test_duplicate_desired_names_conflict_for_every_involved_row_with_no_winner():
    panes = [pane("w1:p1", label="same"), pane("w1:p2", "t2", "same"), pane("w1:p3", label="other")]
    agents = [agent("w1:p1"), agent("w1:p2", tab="t2"), agent("w1:p3")]
    plan = build_plan("w1", panes, agents)
    assert statuses(plan) == {"w1:p1": RowStatus.CONFLICT, "w1:p2": RowStatus.CONFLICT, "w1:p3": RowStatus.ELIGIBLE}
    assert plan.eligible[0].pane_id == "w1:p3"


def test_name_held_by_an_agent_in_another_workspace_conflicts():
    panes = [pane("w1:p1", label="gamma")]
    agents = [agent("w1:p1"), agent("w2:p1", "gamma", workspace="w2")]
    plan = build_plan("w1", panes, agents)
    assert plan.rows[0].status is RowStatus.CONFLICT
    assert "w2:p1" in plan.rows[0].detail and plan.mode == "info"


def test_swap_is_a_conflict_for_both_rows_even_though_each_holder_is_rescheduled():
    panes = [pane("w1:p1", label="b"), pane("w1:p2", label="a")]
    agents = [agent("w1:p1", "a"), agent("w1:p2", "b")]
    plan = build_plan("w1", panes, agents)
    assert set(statuses(plan).values()) == {RowStatus.CONFLICT}


def test_chain_where_the_holder_will_be_renamed_still_conflicts():
    panes = [pane("w1:p1", label="b"), pane("w1:p2", label="c")]
    agents = [agent("w1:p1", "a"), agent("w1:p2", "b")]
    plan = build_plan("w1", panes, agents)
    assert statuses(plan) == {"w1:p1": RowStatus.CONFLICT, "w1:p2": RowStatus.ELIGIBLE}


def test_matching_row_keeps_its_name_against_any_other_row():
    panes = [pane("w1:p1", label="keep"), pane("w1:p2", label="keep")]
    agents = [agent("w1:p1", "keep"), agent("w1:p2", "old")]
    assert statuses(build_plan("w1", panes, agents)) == {"w1:p1": RowStatus.MATCHING, "w1:p2": RowStatus.CONFLICT}


def test_name_held_by_a_pane_without_a_live_agent_still_conflicts():
    panes = [pane("w1:p1", label="ghost")]
    agents = [agent("w1:p1"), agent("w1:p9", "ghost", kind=None)]
    assert build_plan("w1", panes, agents).rows[0].status is RowStatus.CONFLICT


@pytest.mark.parametrize("label", ["Upper", "has space", "1abc", "x" * 33, " padded", "a\n"])
def test_invalid_labels(label):
    plan = build_plan("w1", [pane("w1:p1", label=label)], [agent("w1:p1")])
    assert plan.rows[0].status is RowStatus.INVALID_LABEL and plan.mode == "info"


def test_label_without_agent_and_agent_without_label_are_skips_not_problems():
    panes = [pane("w1:p1", label="Bad Name"), pane("w1:p2"), pane("w1:p3", label="x")]
    agents = [agent("w1:p2", "named"), agent("w1:p3", kind=None)]
    plan = build_plan("w1", panes, agents)
    assert statuses(plan) == {"w1:p1": RowStatus.NO_AGENT, "w1:p2": RowStatus.NO_PANE_NAME, "w1:p3": RowStatus.NO_AGENT}
    assert plan.mode == "noop"


def test_mismatched_or_duplicate_agent_record_is_a_lookup_failure():
    panes = [pane("w1:p1", label="x"), pane("w1:p2", label="y")]
    wrong = agent("w1:p1")
    wrong["terminal_id"] = "other"
    agents = [wrong, agent("w1:p2"), agent("w1:p2")]
    assert set(statuses(build_plan("w1", panes, agents)).values()) == {RowStatus.LOOKUP_FAILED}


def test_observation_errors_and_stale_observations_become_lookup_failed():
    panes = [pane("w1:p1", label="x"), pane("w1:p2", label="y")]
    agents = [agent("w1:p1"), agent("w1:p2")]
    stale = snapshot_of(panes, agents, "w1:p2")
    stale.pane["label"] = "different"
    plan = build_plan("w1", panes, agents, observations={"w1:p1": HerdrError("Lookup failed."), "w1:p2": stale})
    assert statuses(plan) == {"w1:p1": RowStatus.LOOKUP_FAILED, "w1:p2": RowStatus.LOOKUP_FAILED}
    assert plan.rows[0].detail == "Lookup failed." and plan.mode == "info" and not plan.snapshots


def test_modes():
    assert build_plan("w1", [], []).mode == "noop"
    assert build_plan("w1", [pane("w1:p1", label="a")], [agent("w1:p1", "a")]).mode == "noop"


def test_plan_roundtrip_and_validation():
    panes = [pane("w1:p1", label="x"), pane("w1:p2", label="Bad")]
    agents = [agent("w1:p1"), agent("w1:p2")]
    plan = build_plan("w1", panes, agents, observations={"w1:p1": snapshot_of(panes, agents, "w1:p1")})
    again = plan_from_dict(json.loads(json.dumps(plan_to_dict(plan))))
    assert again == plan and again.snapshots == plan.snapshots
    raw = plan_to_dict(plan)
    raw["snapshots"] = {}
    with pytest.raises(ValueError):
        plan_from_dict(raw)
    raw = plan_to_dict(plan)
    raw["rows"][0]["status"] = "bogus"
    with pytest.raises(ValueError):
        plan_from_dict(raw)
    raw = plan_to_dict(plan)
    raw["rows"][1]["workspace_id"] = "w9"
    with pytest.raises(ValueError):
        plan_from_dict(raw)


# --- executor --------------------------------------------------------------

def run(fake, plan, progress=None, runner=None):
    runner = runner or SyncRunner(fake, plan)
    return runner, asyncio.run(runner.run(progress))


def test_sequential_one_write_per_eligible_row_with_progress_events():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "bb"), ("w1:p3", "cc", None, "t2"), ("w1:p4", None, "q"), ("w1:p5", "dd", "-"))
    events = []
    runner, report = run(fake, plan, events.append)
    assert fake.writes == [("w1:p1", "aa"), ("w1:p3", "cc")]
    # Rows run in tab order (t1 panes, then t2), then pane order.
    assert [r.row.pane_id for r in report.results] == ["w1:p1", "w1:p2", "w1:p4", "w1:p5", "w1:p3"]
    assert [r.outcome for r in report.results] == [
        RowOutcome.SYNCED, RowOutcome.MATCHING, RowOutcome.SKIPPED, RowOutcome.SKIPPED, RowOutcome.SYNCED]
    assert report.summary() == "2 synced, 1 already matching, 2 skipped"
    assert runner.state == "done" and runner.report is report and not report.canceled
    assert [(e.index, e.result is None) for e in events][:4] == [(0, True), (0, False), (1, True), (1, False)]
    assert len(events) == 10 and all(e.total == 5 for e in events)
    assert events[1].result.outcome is RowOutcome.SYNCED
    # Each write is preceded by its rechecks and followed by a verification read.
    first = fake.calls.index("rename")
    assert fake.calls[:first] == ["pane_list", "agent_list", "get_snapshot"]
    assert fake.calls[first:first + 2] == ["rename", "get_snapshot"]


@pytest.mark.parametrize("change,detail", [
    (lambda f: f.panes[0].update(label="renamed"), "pane name has changed"),
    (lambda f: f.panes[0].pop("label"), "removed"),
    (lambda f: f.panes[0].update(tab_id="w1:t9"), "no longer in this workspace"),
    (lambda f: f.panes.pop(0), "no longer in this workspace"),
    (lambda f: f.agents[0].update(name="changed"), "agent name has changed"),
    (lambda f: f.agents.pop(0), "No running agent"),
    (lambda f: f.agents.append(agent("w2:p1", "aa", workspace="w2")), "now used"),
    (lambda f: f.agents[0].update(terminal_id="term-new"), "No running agent"),
])
def test_recheck_before_write_skips_changed_rows_without_writing(change, detail):
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    change(fake)
    _, report = run(fake, plan)
    assert report.results[0].outcome is RowOutcome.SKIPPED and detail in report.results[0].detail
    assert ("w1:p1", "aa") not in fake.writes
    assert report.results[1].outcome is RowOutcome.SYNCED


def test_replaced_agent_process_is_detected_by_the_target_recheck():
    fake, plan = prepared(("w1:p1", "aa", "x"))
    plan.snapshots["w1:p1"].agent["agent_session"] = {"agent": "codex", "kind": "id", "source": "s", "value": "1"}
    _, report = run(fake, plan)
    assert report.results[0].outcome is RowOutcome.SKIPPED and fake.writes == []


def test_definite_server_failure_continues_independent_rows():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    real = Fake.apply

    def hook(f, pane_id, name):
        if pane_id == "w1:p1":
            # The name was taken between recheck and write: the server decides.
            raise HerdrError("This agent name is already in use.", code="agent_name_taken")
        return real(f, pane_id, name)
    fake.rename_hook = hook
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.FAILED, RowOutcome.SYNCED]
    assert "already in use" in report.results[0].detail


def test_connection_failure_stops_the_queue_and_reports_the_rest_not_attempted():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"), ("w1:p3", "cc", "z"))
    fake.fail["agent_list"] = HerdrError("Could not connect to Herdr.", code="connection")
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.FAILED, RowOutcome.NOT_ATTEMPTED, RowOutcome.NOT_ATTEMPTED]
    assert report.stopped and fake.writes == []


def test_capability_failure_stops_the_queue():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    fake.fail["pane_list"] = HerdrError("This Herdr version is not supported.", code="unsupported")
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.FAILED, RowOutcome.NOT_ATTEMPTED]


def test_lookup_error_without_connection_code_is_a_row_failure_only():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    fake.fail["pane_list"] = HerdrError("Could not connect to Herdr. odd", code="ui_busy")
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.FAILED, RowOutcome.SYNCED]


def test_uncertain_write_that_did_not_apply_is_recorded_and_never_retried():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    real = Fake.apply

    def hook(f, pane_id, name):
        if pane_id == "w1:p1":
            raise HerdrError(UNCERTAIN, uncertain=True)
        return real(f, pane_id, name)
    fake.rename_hook = hook
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.UNCERTAIN, RowOutcome.SYNCED]
    assert fake.writes.count(("w1:p1", "aa")) == 1 and "could not be confirmed" in report.results[0].detail


def test_uncertain_write_that_applied_is_reconciled_by_one_read():
    fake, plan = prepared(("w1:p1", "aa", "x"))
    real = Fake.apply

    def hook(f, pane_id, name):
        real(f, pane_id, name)
        raise HerdrError(UNCERTAIN, uncertain=True)
    fake.rename_hook = hook
    _, report = run(fake, plan)
    assert report.results[0].outcome is RowOutcome.SYNCED and fake.writes == [("w1:p1", "aa")]


def test_unverified_success_response_is_uncertain():
    fake, plan = prepared(("w1:p1", "aa", "x"))
    fake.rename_hook = lambda f, pane_id, name: {}
    _, report = run(fake, plan)
    assert report.results[0].outcome is RowOutcome.UNCERTAIN and fake.writes == [("w1:p1", "aa")]


def test_post_write_connection_loss_is_uncertain_and_stops():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    real = Fake.apply

    def hook(f, pane_id, name):
        result = real(f, pane_id, name)
        f.fail["get_snapshot"] = HerdrError("Could not connect to Herdr.", code="connection")
        return result
    fake.rename_hook = hook
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.UNCERTAIN, RowOutcome.NOT_ATTEMPTED]


def test_cancel_between_rows_finishes_the_in_flight_row_then_stops():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"), ("w1:p3", "cc", "z"))
    runner = SyncRunner(fake, plan)

    real = Fake.apply

    def hook(f, pane_id, name):
        assert runner.request_cancel()  # cancel arrives while the row is in flight
        return real(f, pane_id, name)
    fake.rename_hook = hook
    _, report = run(fake, plan, None, runner)
    assert [r.outcome for r in report.results] == [RowOutcome.SYNCED, RowOutcome.NOT_ATTEMPTED, RowOutcome.NOT_ATTEMPTED]
    assert report.canceled and fake.writes == [("w1:p1", "aa")]
    assert not runner.request_cancel()


def test_repeated_run_calls_cannot_start_a_second_batch():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    runner = SyncRunner(fake, plan)

    async def main():
        return await asyncio.gather(runner.run(), runner.run(), runner.run())
    results = asyncio.run(main())
    assert sum(r is not None for r in results) == 1
    assert fake.writes == [("w1:p1", "aa"), ("w1:p2", "bb")]
    assert asyncio.run(runner.run()) is None and len(fake.writes) == 2


def test_info_and_error_modes_never_write():
    panes, agents = world(("w1:p1", "Bad", "x"))
    plan = build_plan("w1", panes, agents)
    fake = Fake(panes, agents)
    info = SyncRunner(fake, plan)
    assert info.mode == "info" and asyncio.run(info.run()) is None
    failed = SyncRunner(fake, None, "Boom")
    assert failed.mode == "error" and asyncio.run(failed.run()) is None
    assert fake.writes == [] and fake.calls == []


def test_progress_callback_errors_do_not_break_the_batch():
    fake, plan = prepared(("w1:p1", "aa", "x"))

    def boom(event):
        raise RuntimeError("ui bug")
    _, report = run(fake, plan, boom)
    assert report.results[0].outcome is RowOutcome.SYNCED


def test_worker_runs_off_the_event_loop_thread():
    import threading
    fake, plan = prepared(("w1:p1", "aa", "x"))
    threads = []
    real = fake.pane_list
    fake.pane_list = lambda ws: threads.append(threading.get_ident()) or real(ws)
    run(fake, plan)
    assert threads and threads[0] != threading.get_ident()


def test_missing_snapshot_fails_the_row_without_writing():
    fake, plan = prepared(("w1:p1", "aa", "x"))
    plan.snapshots.clear()
    _, report = run(fake, plan)
    assert report.results[0].outcome is RowOutcome.FAILED and fake.writes == []


def test_collect_plan_gathers_lists_then_observations_in_one_pass():
    panes, agents = world(("w1:p1", "aa", "x"), ("w1:p2", "bb", "bb"), ("w1:p3", "Bad", "q"))
    fake = Fake(panes, agents)
    plan = collect_plan(fake, "w1")
    assert statuses(plan) == {"w1:p1": RowStatus.ELIGIBLE, "w1:p2": RowStatus.MATCHING, "w1:p3": RowStatus.INVALID_LABEL}
    assert set(plan.snapshots) == {"w1:p1"}
    assert fake.calls == ["pane_list", "agent_list", "get_snapshot"] and fake.writes == []


def test_collect_plan_lookup_failure_marks_the_row_and_connection_loss_aborts():
    panes, agents = world(("w1:p1", "aa", "x"))
    fake = Fake(panes, agents)
    fake.fail["get_snapshot"] = HerdrError("Gone.", code="pane_not_found")
    assert collect_plan(fake, "w1").rows[0].status is RowStatus.LOOKUP_FAILED
    fake.fail["get_snapshot"] = HerdrError("Could not connect to Herdr.", code="connection")
    with pytest.raises(HerdrError):
        collect_plan(fake, "w1")


# --- review regressions ----------------------------------------------------

def test_per_pane_unsupported_becomes_a_lookup_row_and_other_rows_survive():
    panes, agents = world(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    fake = Fake(panes, agents)
    real = fake.get_snapshot

    def snap(invocation):
        if invocation.pane_id == "w1:p1":
            raise HerdrError("This Herdr version is not supported.", code="unsupported")
        return real(invocation)
    fake.get_snapshot = snap
    plan = collect_plan(fake, "w1")
    assert statuses(plan) == {"w1:p1": RowStatus.LOOKUP_FAILED, "w1:p2": RowStatus.ELIGIBLE}
    assert "could not be read" in plan.rows[0].detail and plan.mode == "confirm"
    fake.get_snapshot = lambda invocation: (_ for _ in ()).throw(HerdrError("Gone", code="method_not_found"))
    with pytest.raises(HerdrError):
        collect_plan(fake, "w1")


def test_target_changed_is_a_row_problem_only():
    panes, agents = world(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    fake = Fake(panes, agents)
    fake.fail["get_snapshot"] = HerdrError("The target agent has changed.", code="target_changed")
    plan = collect_plan(fake, "w1")
    assert statuses(plan) == {"w1:p1": RowStatus.LOOKUP_FAILED, "w1:p2": RowStatus.ELIGIBLE}


def test_label_less_pane_is_no_pane_name_even_with_inconsistent_agent_records():
    panes = [pane("w1:p1"), pane("w1:p2")]
    wrong = agent("w1:p1", "named")
    wrong["terminal_id"] = "other"
    agents = [wrong, agent("w1:p2", "a"), agent("w1:p2", "b")]
    plan = build_plan("w1", panes, agents)
    assert set(statuses(plan).values()) == {RowStatus.NO_PANE_NAME} and plan.mode == "noop"


def test_transport_failure_of_a_write_stops_the_queue():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"), ("w1:p3", "cc", "z"))

    def hook(f, pane_id, name):
        raise HerdrError(UNCERTAIN, uncertain=True, code="transport")
    fake.rename_hook = hook
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.UNCERTAIN, RowOutcome.NOT_ATTEMPTED, RowOutcome.NOT_ATTEMPTED]
    assert fake.writes == [("w1:p1", "aa")] and report.stopped


def test_transport_failure_that_reconciles_does_not_stop():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    real = Fake.apply

    def hook(f, pane_id, name):
        if pane_id == "w1:p1":
            real(f, pane_id, name)
            raise HerdrError(UNCERTAIN, uncertain=True, code="transport")
        return real(f, pane_id, name)
    fake.rename_hook = hook
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.SYNCED, RowOutcome.SYNCED]


def test_definite_rejection_still_continues():
    fake, plan = prepared(("w1:p1", "aa", "x"), ("w1:p2", "bb", "y"))
    real = Fake.apply

    def hook(f, pane_id, name):
        if pane_id == "w1:p1":
            raise HerdrError("This agent name is already in use.", code="agent_name_taken")
        return real(f, pane_id, name)
    fake.rename_hook = hook
    _, report = run(fake, plan)
    assert [r.outcome for r in report.results] == [RowOutcome.FAILED, RowOutcome.SYNCED]


def test_invalid_label_never_reaches_the_client_write():
    fake, plan = prepared(("w1:p1", "aa", "x"))
    from dataclasses import replace
    bad = replace(plan.rows[0], label="--clear")
    plan = replace(plan, rows=(bad,))
    plan.snapshots["w1:p1"].pane["label"] = "--clear"
    fake.panes[0]["label"] = "--clear"
    _, report = run(fake, plan)
    assert report.results[0].outcome is RowOutcome.SKIPPED and fake.writes == []


def test_decoding_rejects_eligible_rows_with_invalid_labels():
    panes, agents = world(("w1:p1", "aa", "x"))
    plan = build_plan("w1", panes, agents, observations=observed(panes, agents, build_plan("w1", panes, agents)))
    for label in ("--clear", "Upper", None, ""):
        raw = plan_to_dict(plan)
        raw["rows"][0]["label"] = label
        with pytest.raises(ValueError):
            plan_from_dict(raw)
