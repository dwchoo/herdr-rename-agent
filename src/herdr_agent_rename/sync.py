"""Pane-label synchronization: the workspace batch planner and executor.

Public API for the UI (workspace sync)
--------------------------------------
Planning (pure, no I/O):
    ``build_plan(workspace_id, panes, agents, tabs=(), observations=None)``
    returns a frozen ``SyncPlan``. ``collect_plan(client, workspace_id)``
    gathers the lists and per-target observations through a client and then
    calls ``build_plan``; it blocks, so run it off the UI loop.

Data:
    ``RowStatus``   ELIGIBLE, MATCHING, NO_PANE_NAME, NO_AGENT, INVALID_LABEL,
                    CONFLICT, LOOKUP_FAILED (planning-time classification).
    ``SyncRow``     pane_id, terminal_id, tab_id, workspace_id, tab_label,
                    tab_number, agent_name (None = unnamed agent or no agent),
                    label (None = no explicit pane name), status, detail
                    (English reason shown to the user).
    ``SyncPlan``    workspace_id, workspace_label, rows (tab order, then pane
                    order), snapshots (frozen observations of ELIGIBLE rows);
                    ``counts``, ``eligible``, ``problems`` and ``mode``:
                    "noop" (nothing to do, nothing wrong), "info" (no eligible
                    row but conflict/invalid/lookup problems; Close only) or
                    "confirm" (at least one ELIGIBLE row; Yes/No).

Execution:
    ``SyncRunner(client, plan, error="")`` is what the popup receives. Its
    ``mode`` is "confirm", "info" or "error" (``error`` holds the message and
    ``plan`` may be None). ``await runner.run(progress)`` applies the batch off
    the event loop (one worker thread per row, as in ``Controller``); a second
    call returns None, so repeated Enter/Y/clicks cannot start two batches.
    ``progress`` is called on the event loop with a ``SyncProgress`` for the
    start (result None) and the end of every row. ``runner.request_cancel()``
    stops the queue after the in-flight row is reconciled. ``runner.state`` is
    "ready", "running" or "done"; ``runner.report`` holds the ``SyncReport``.
    Per-row ``RowOutcome``: SYNCED, MATCHING, SKIPPED, FAILED, UNCERTAIN,
    NOT_ATTEMPTED, each with an English ``detail``.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from .client import TRANSPORT
from .models import HerdrError, Invocation, Snapshot
from .targets import check_target
from .validation import sanitize_message, validate_name


def agent_name(snapshot: Snapshot) -> str:
    value = snapshot.agent.get("name")
    return value if isinstance(value, str) else ""


def pane_label(snapshot: Snapshot) -> str:
    value = snapshot.pane.get("label")
    return value if isinstance(value, str) else ""


def check_sync_source(initial: Snapshot, current: Snapshot) -> None:
    """Never replace the exact label that the user saw and confirmed."""
    if current.pane.get("label") != initial.pane.get("label"):
        raise HerdrError(
            "The pane name has changed. Reopen the dialog.", code="source_changed"
        )


def check_sync_agent_name(initial: Snapshot, current: Snapshot) -> None:
    if agent_name(current) != agent_name(initial):
        raise HerdrError(
            "The agent name has changed. Reopen the dialog.", code="name_changed"
        )


# ---------------------------------------------------------------------------
# Workspace batch: planning
# ---------------------------------------------------------------------------

NO_NAME = "This pane has no name."
NO_AGENT = "No running agent was found in this pane."
# Server-wide failures: abort the preview (a per-pane "unsupported" is not one).
SERVER_CODES = frozenset({"connection", "method_not_found", "unknown_method"})
# During application a capability failure also stops the queue.
STOP_CODES = SERVER_CODES | {"unsupported"}
# Definite "the world changed since the preview" signals: skip, do not fail.
_CHANGED_CODES = frozenset({
    "target_changed", "target_moved", "source_changed", "name_changed",
    "no_agent", "pane_not_found", "agent_not_found",
})


class RowStatus(str, Enum):
    ELIGIBLE = "eligible"
    MATCHING = "matching"
    NO_PANE_NAME = "no_pane_name"
    NO_AGENT = "no_agent"
    INVALID_LABEL = "invalid_label"
    CONFLICT = "conflict"
    LOOKUP_FAILED = "lookup_failed"


PROBLEM_STATUSES = frozenset({RowStatus.CONFLICT, RowStatus.INVALID_LABEL, RowStatus.LOOKUP_FAILED})


class RowOutcome(str, Enum):
    SYNCED = "synced"
    MATCHING = "matching"
    SKIPPED = "skipped"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    NOT_ATTEMPTED = "not_attempted"


@dataclass(frozen=True)
class SyncRow:
    pane_id: str
    terminal_id: str
    tab_id: str
    workspace_id: str
    agent_name: str | None
    label: str | None
    status: RowStatus
    detail: str = ""
    tab_label: str | None = None
    tab_number: int | None = None


@dataclass(frozen=True)
class SyncPlan:
    workspace_id: str
    rows: tuple[SyncRow, ...]
    workspace_label: str | None = None
    snapshots: Mapping[str, Snapshot] = field(default_factory=dict, compare=False)

    @property
    def counts(self) -> dict[RowStatus, int]:
        result = {status: 0 for status in RowStatus}
        for row in self.rows:
            result[row.status] += 1
        return result

    @property
    def eligible(self) -> tuple[SyncRow, ...]:
        return tuple(row for row in self.rows if row.status is RowStatus.ELIGIBLE)

    @property
    def problems(self) -> tuple[SyncRow, ...]:
        return tuple(row for row in self.rows if row.status in PROBLEM_STATUSES)

    @property
    def mode(self) -> str:
        """"confirm", "info" or "noop"; see the module docstring."""
        if self.eligible:
            return "confirm"
        return "info" if self.problems else "noop"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def build_plan(
    workspace_id: str,
    panes: Sequence[Mapping[str, Any]],
    agents: Sequence[Mapping[str, Any]],
    tabs: Sequence[Mapping[str, Any]] = (),
    observations: Mapping[str, Snapshot | HerdrError] | None = None,
    workspace_label: str | None = None,
) -> SyncPlan:
    """Classify every pane of one workspace; ``agents`` spans the whole server.

    ``observations`` maps ELIGIBLE pane ids to a frozen ``Snapshot`` or to the
    ``HerdrError`` of a failed lookup (that row becomes LOOKUP_FAILED). When it
    is None no lookups are assumed (used for the first planning pass).
    """
    tab_info = {t["tab_id"]: t for t in tabs if isinstance(t, Mapping) and t.get("tab_id") and t.get("workspace_id", workspace_id) == workspace_id}
    ranks: dict[str, int] = {}
    for tab in sorted(tab_info.values(), key=lambda t: t["number"] if isinstance(t.get("number"), int) else 1 << 30):
        ranks.setdefault(tab["tab_id"], len(ranks))
    members = [p for p in panes if p.get("workspace_id") == workspace_id]
    for pane in members:
        ranks.setdefault(pane["tab_id"], len(ranks))
    members = [p for _, p in sorted(enumerate(members), key=lambda item: (ranks[item[1]["tab_id"]], item[0]))]

    by_pane: dict[str, list[Mapping[str, Any]]] = {}
    for record in agents:
        by_pane.setdefault(record.get("pane_id"), []).append(record)
    held: dict[str, list[Mapping[str, Any]]] = {}
    for record in agents:
        if isinstance(record.get("name"), str) and record["name"]:
            held.setdefault(record["name"], []).append(record)

    base: list[tuple[Mapping[str, Any], RowStatus, str, str | None, str | None]] = []
    for pane in members:
        label = _text(pane.get("label")) or None
        records = by_pane.get(pane["pane_id"], [])
        agent = records[0] if len(records) == 1 else None
        name = _text(agent.get("name")) or None if agent else None
        status, detail = RowStatus.ELIGIBLE, ""
        if label is None:
            status, detail = RowStatus.NO_PANE_NAME, NO_NAME
        elif len(records) > 1 or (agent and agent.get("terminal_id") != pane["terminal_id"]):
            status, detail = RowStatus.LOOKUP_FAILED, "The agent record does not match this pane."
        elif not agent or not _text(agent.get("agent")):
            status, detail = RowStatus.NO_AGENT, NO_AGENT
        elif validate_name(label) is not None:
            status, detail = RowStatus.INVALID_LABEL, "The pane name is not a valid agent name."
        elif name == label:
            status, detail = RowStatus.MATCHING, "The agent name already matches."
        base.append((pane, status, detail, name, label))

    wanted: dict[str, int] = {}
    for _, status, _, _, label in base:
        if status is RowStatus.ELIGIBLE:
            wanted[label] = wanted.get(label, 0) + 1

    rows = []
    for pane, status, detail, name, label in base:
        if status is RowStatus.ELIGIBLE:
            other = [r for r in held.get(label, []) if r.get("terminal_id") != pane["terminal_id"]]
            if wanted[label] > 1:
                status, detail = RowStatus.CONFLICT, "Another pane in this workspace has the same name."
            elif other:
                status = RowStatus.CONFLICT
                detail = "This name is already used by the agent in pane %s." % sanitize_message(other[0].get("pane_id"))
            elif observations is not None:
                seen = observations.get(pane["pane_id"])
                if isinstance(seen, Snapshot):
                    detail = _observation_problem(seen, label, name)
                else:
                    detail = sanitize_message(seen) if seen else "The agent could not be inspected."
                if detail:
                    status = RowStatus.LOOKUP_FAILED
        tab = tab_info.get(pane["tab_id"], {})
        rows.append(SyncRow(
            pane["pane_id"], pane["terminal_id"], pane["tab_id"], workspace_id, name, label, status, detail,
            _text(tab.get("label")), tab.get("number") if isinstance(tab.get("number"), int) else None,
        ))
    snapshots = {
        row.pane_id: observations[row.pane_id]
        for row in rows
        if row.status is RowStatus.ELIGIBLE and observations and isinstance(observations.get(row.pane_id), Snapshot)
    }
    return SyncPlan(workspace_id, tuple(rows), workspace_label, snapshots)


def _observation_problem(snapshot: Snapshot, label: str, name: str | None) -> str:
    if pane_label(snapshot) != label or agent_name(snapshot) != (name or ""):
        return "The pane changed while the preview was prepared."
    return ""


def _lookup_detail(error: HerdrError) -> str:
    """English per-pane reason; an unusual record never aborts the whole preview."""
    if error.code == "unsupported":
        return "This pane's details could not be read in a supported form."
    return sanitize_message(error) or "The agent could not be inspected."


def collect_plan(client: Any, workspace_id: str) -> SyncPlan:
    """Freeze one workspace through the public list APIs (blocking)."""
    panes = client.pane_list(workspace_id)
    agents = client.agent_list()
    tabs: list = []
    label = None
    try:
        tabs = client.tab_list(workspace_id)
        label = client.workspace_label(workspace_id)
    except HerdrError as error:
        if error.code in SERVER_CODES:
            raise
    first = build_plan(workspace_id, panes, agents, tabs, None, label)
    observations: dict[str, Snapshot | HerdrError] = {}
    for row in first.eligible:
        try:
            observations[row.pane_id] = client.get_snapshot(Invocation(row.pane_id, row.workspace_id, row.tab_id))
        except HerdrError as error:
            if error.code in SERVER_CODES:
                raise
            observations[row.pane_id] = HerdrError(_lookup_detail(error), code=error.code)
    return build_plan(workspace_id, panes, agents, tabs, observations, label)


# ---------------------------------------------------------------------------
# Workspace batch: execution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RowResult:
    row: SyncRow
    outcome: RowOutcome
    detail: str = ""


@dataclass(frozen=True)
class SyncProgress:
    """``result`` is None when the row starts, set when it finishes."""
    index: int
    total: int
    row: SyncRow
    result: RowResult | None = None


@dataclass(frozen=True)
class SyncReport:
    results: tuple[RowResult, ...]
    canceled: bool = False
    stopped: str = ""

    @property
    def counts(self) -> dict[RowOutcome, int]:
        result = {outcome: 0 for outcome in RowOutcome}
        for item in self.results:
            result[item.outcome] += 1
        return result

    def summary(self) -> str:
        counts = self.counts
        names = (
            (RowOutcome.SYNCED, "synced"), (RowOutcome.MATCHING, "already matching"),
            (RowOutcome.SKIPPED, "skipped"), (RowOutcome.FAILED, "failed"),
            (RowOutcome.UNCERTAIN, "uncertain"), (RowOutcome.NOT_ATTEMPTED, "not attempted"),
        )
        return ", ".join("%d %s" % (counts[o], text) for o, text in names if counts[o])


def _skip(row: SyncRow, detail: str) -> tuple[RowResult, bool]:
    return RowResult(row, RowOutcome.SKIPPED, detail), False


def apply_row(client: Any, plan: SyncPlan, row: SyncRow) -> tuple[RowResult, bool]:
    """Recheck, write once and verify one ELIGIBLE row; returns (result, stop).

    Blocking. ``stop`` is True after a connection or capability failure.
    """
    invocation = Invocation(row.pane_id, row.workspace_id, row.tab_id)
    initial = plan.snapshots.get(row.pane_id)
    label = row.label or ""
    write_started = write_returned = False
    try:
        if initial is None:
            return RowResult(row, RowOutcome.FAILED, "The original target details are missing."), False
        pane = next((p for p in client.pane_list(row.workspace_id) if p.get("pane_id") == row.pane_id), None)
        if pane is None or pane.get("tab_id") != row.tab_id or pane.get("terminal_id") != row.terminal_id:
            return _skip(row, "The pane is no longer in this workspace tab.")
        if _text(pane.get("label")) != row.label:
            return _skip(row, "The pane name has changed." if pane.get("label") else "The pane name was removed.")
        agents = client.agent_list()
        mine = [a for a in agents if a.get("pane_id") == row.pane_id]
        if len(mine) != 1 or mine[0].get("terminal_id") != row.terminal_id:
            return _skip(row, NO_AGENT)
        if (_text(mine[0].get("name")) or None) != row.agent_name:
            return _skip(row, "The agent name has changed.")
        taker = next((a for a in agents if a.get("name") == label and a.get("terminal_id") != row.terminal_id), None)
        if taker:
            return _skip(row, "This name is now used by the agent in pane %s." % sanitize_message(taker.get("pane_id")))
        current = client.get_snapshot(invocation)
        check_target(initial, current, invocation)
        check_sync_source(initial, current)
        check_sync_agent_name(initial, current)
        if validate_name(label) is not None:
            return _skip(row, "The pane name is not a valid agent name.")
        write_started = True
        client.rename(row.pane_id, label)
        write_returned = True
        return _verify(client, row, initial, invocation, label, "Verified after rename."), False
    except HerdrError as error:
        stop = error.code in STOP_CODES
        if write_started and (write_returned or error.uncertain):
            if error.uncertain and not write_returned:
                try:
                    return _verify(client, row, initial, invocation, label, "Confirmed after an unclear response."), False
                except HerdrError as again:
                    stop = stop or again.code in STOP_CODES
            # An unconfirmed write over a broken transport must stop the queue.
            return RowResult(row, RowOutcome.UNCERTAIN, _uncertain_text(error)), stop or error.code == TRANSPORT
        if error.code in _CHANGED_CODES:
            return _skip(row, sanitize_message(error))
        return RowResult(row, RowOutcome.FAILED, sanitize_message(error)), stop
    except Exception as error:  # a bug must not wedge the queue
        outcome = RowOutcome.UNCERTAIN if write_started else RowOutcome.FAILED
        return RowResult(row, outcome, sanitize_message(error)), False


def _uncertain_text(error: HerdrError) -> str:
    text = "The rename result could not be confirmed. Check the current name before retrying."
    message = sanitize_message(error)
    return text if text in message else text + " " + message


def _verify(client: Any, row: SyncRow, initial: Snapshot, invocation: Invocation, label: str, ok: str) -> RowResult:
    try:
        verified = client.get_snapshot(invocation)
        check_target(initial, verified, invocation)
        check_sync_source(initial, verified)
    except HerdrError as error:
        raise HerdrError(_uncertain_text(error), code=error.code, uncertain=True) from None
    if agent_name(verified) != label:
        raise HerdrError("The agent name was not confirmed after the rename.", code="unconfirmed", uncertain=True)
    return RowResult(row, RowOutcome.SYNCED, ok)


class SyncRunner:
    """One batch application, shared by the confirm, info and error dialogs."""

    def __init__(self, client: Any, plan: SyncPlan | None, error: str = "") -> None:
        self.client = client
        self.plan = plan
        self.error = error
        self.state = "ready"
        self.report: SyncReport | None = None
        self.cancel_requested = False
        self._external_cancel = False

    @property
    def mode(self) -> str:
        if self.plan is None or self.error:
            return "error"
        return "confirm" if self.plan.mode == "confirm" else "info"

    def request_cancel(self) -> bool:
        """Stop after the in-flight row; False when nothing is running."""
        if self.state != "running":
            return False
        self.cancel_requested = True
        return True

    async def _thread_call(self, function: Callable[..., Any], *args: Any) -> Any:
        """Never abandon a worker thread mid-request (see ``Controller``)."""
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                self._external_cancel = True
                self.cancel_requested = True
                if task.cancelled():
                    raise

    async def run(self, progress: Callable[[SyncProgress], None] | None = None) -> SyncReport | None:
        # The state flips before the first await: duplicate keys cannot start
        # a second batch, and info/error modes never write.
        if self.state != "ready" or self.mode != "confirm" or self.plan is None:
            return None
        self.state = "running"
        plan = self.plan
        total = len(plan.rows)
        results: list[RowResult] = []
        stopped = ""

        def emit(index: int, row: SyncRow, result: RowResult | None) -> None:
            if progress is not None:
                try:
                    progress(SyncProgress(index, total, row, result))
                except Exception:
                    pass

        try:
            for index, row in enumerate(plan.rows):
                emit(index, row, None)
                if row.status is RowStatus.MATCHING:
                    result = RowResult(row, RowOutcome.MATCHING, row.detail)
                elif row.status is not RowStatus.ELIGIBLE:
                    result = RowResult(row, RowOutcome.SKIPPED, row.detail)
                elif stopped:
                    result = RowResult(row, RowOutcome.NOT_ATTEMPTED, "Stopped: " + stopped)
                elif self.cancel_requested:
                    result = RowResult(row, RowOutcome.NOT_ATTEMPTED, "Canceled before this pane.")
                else:
                    result, stop = await self._thread_call(apply_row, self.client, plan, row)
                    if stop:
                        stopped = result.detail or "Could not reach Herdr."
                results.append(result)
                emit(index, row, result)
            self.report = SyncReport(tuple(results), self.cancel_requested, stopped)
            return self.report
        finally:
            self.state = "done"


# ---------------------------------------------------------------------------
# Plan serialization for the popup handoff
# ---------------------------------------------------------------------------

_ROW_STRINGS = ("pane_id", "terminal_id", "tab_id", "workspace_id")


def plan_to_dict(plan: SyncPlan) -> dict[str, Any]:
    return {
        "workspace_id": plan.workspace_id,
        "workspace_label": plan.workspace_label,
        "rows": [
            {**{k: getattr(r, k) for k in _ROW_STRINGS}, "agent_name": r.agent_name, "label": r.label,
             "status": r.status.value, "detail": r.detail, "tab_label": r.tab_label, "tab_number": r.tab_number}
            for r in plan.rows
        ],
        "snapshots": {
            pane: {"agent": s.agent, "pane": s.pane, "process": s.process}
            for pane, s in plan.snapshots.items()
        },
    }


def plan_from_dict(raw: object) -> SyncPlan:
    """Strictly rebuild a plan; raises ``ValueError`` for anything malformed."""
    def optional(value: object, kind: type) -> Any:
        if value is not None and (type(value) is not kind):
            raise ValueError("Invalid plan field")
        return value

    if not isinstance(raw, dict) or not isinstance(raw.get("workspace_id"), str) or not raw["workspace_id"]:
        raise ValueError("Invalid plan")
    rows = []
    for item in raw.get("rows", ()) if isinstance(raw.get("rows"), list) else ():
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k] for k in _ROW_STRINGS):
            raise ValueError("Invalid plan row")
        if item["workspace_id"] != raw["workspace_id"] or not isinstance(item.get("detail", ""), str):
            raise ValueError("Invalid plan row")
        row = SyncRow(
            *(item[k] for k in _ROW_STRINGS), optional(item.get("agent_name"), str), optional(item.get("label"), str),
            RowStatus(item["status"]), item.get("detail", ""), optional(item.get("tab_label"), str),
            optional(item.get("tab_number"), int),
        )
        if row.status is RowStatus.ELIGIBLE and validate_name(row.label) is not None:
            raise ValueError("Invalid label in an eligible row")
        rows.append(row)
    if not isinstance(raw.get("rows"), list) or len({r.pane_id for r in rows}) != len(rows):
        raise ValueError("Invalid plan rows")
    snapshots = {}
    raw_snapshots = raw.get("snapshots", {})
    if not isinstance(raw_snapshots, dict):
        raise ValueError("Invalid snapshots")
    for pane, item in raw_snapshots.items():
        if not isinstance(item, dict) or any(not isinstance(item.get(k), dict) for k in ("agent", "pane", "process")):
            raise ValueError("Invalid snapshot")
        if any(item[k].get("pane_id") != pane for k in ("agent", "pane")):
            raise ValueError("Mismatched snapshot")
        snapshots[pane] = Snapshot(item["agent"], item["pane"], item["process"])
    if any(r.status is RowStatus.ELIGIBLE and r.pane_id not in snapshots for r in rows):
        raise ValueError("Missing snapshot")
    return SyncPlan(raw["workspace_id"], tuple(rows), optional(raw.get("workspace_label"), str), snapshots)
