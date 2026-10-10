"""Conservative identity checks for one frozen pane/agent target.

Public terminal/session/process fields are observations, not an atomic identity
token. Common foreground PIDs and executable signatures can catch many restarts,
but PID reuse, an unchanged session reference, or missing process information can
hide replacement. Revalidation cannot remove the race before the rename call.
"""

from typing import Any

from .models import HerdrError, Invocation, Snapshot


def _changed() -> HerdrError:
    return HerdrError(
        "The target agent has changed. Reopen the dialog.", code="target_changed"
    )


def _moved() -> HerdrError:
    return HerdrError(
        "The target pane has moved. Reopen the dialog.", code="target_moved"
    )


def _check_snapshot(invocation: Invocation, snapshot: Snapshot) -> None:
    """Check canonical public locations, including pane aliases after moves."""
    pane, agent = snapshot.pane, snapshot.agent
    if not isinstance(agent.get("agent"), str) or not agent["agent"]:
        raise HerdrError("No running agent was found in this pane.", code="no_agent")
    if pane.get("pane_id") != invocation.pane_id:
        raise _moved()
    for key in ("pane_id", "workspace_id", "tab_id"):
        if not isinstance(pane.get(key), str) or not pane[key]:
            raise HerdrError("This Herdr version is not supported.", code="unsupported")
        if agent.get(key) != pane[key]:
            raise _moved()
    for key in ("workspace_id", "tab_id"):
        expected = getattr(invocation, key)
        if expected is not None and pane[key] != expected:
            raise _moved()
    if not pane.get("terminal_id") or agent.get("terminal_id") != pane["terminal_id"]:
        raise _changed()
    process_pane = snapshot.process.get("pane_id")
    if process_pane is not None and process_pane != pane["pane_id"]:
        raise _moved()


def _foreground(snapshot: Snapshot) -> dict[int, dict[str, Any]] | None:
    processes = snapshot.process.get("foreground_processes")
    if not isinstance(processes, list):
        return None
    return {
        process["pid"]: process
        for process in processes
        if isinstance(process, dict)
        and isinstance(process.get("pid"), int)
        and not isinstance(process.get("pid"), bool)
        and process["pid"] > 0
    }


def _same_process(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Require a common PID's known executable fields to stay the same.

    Cwd is intentionally excluded: an agent may change its directory. Child
    process churn is allowed when at least one observed executable remains.
    These fields have no start-time token, so they cannot disambiguate PID reuse.
    """
    for key in ("name", "argv0", "argv", "cmdline"):
        if left.get(key) is not None and right.get(key) != left[key]:
            return False
    return True


def check_target(initial: Snapshot, current: Snapshot, invocation: Invocation) -> None:
    _check_snapshot(invocation, current)
    for key in ("pane_id", "workspace_id", "tab_id"):
        if initial.pane.get(key) != current.pane.get(key):
            raise _moved()
    for key in ("terminal_id", "agent", "agent_session"):
        if initial.agent.get(key) != current.agent.get(key):
            raise _changed()
    if initial.pane.get("terminal_id") != current.pane.get("terminal_id"):
        raise _changed()
    old_processes, new_processes = _foreground(initial), _foreground(current)
    if old_processes and new_processes is not None:
        common = old_processes.keys() & new_processes.keys()
        if not any(_same_process(old_processes[pid], new_processes[pid]) for pid in common):
            raise _changed()
