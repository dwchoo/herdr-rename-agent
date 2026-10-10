"""Submission state and conservative checks around the public rename API.

Public terminal/session/process fields are observations, not an atomic identity
token. Common foreground PIDs and executable signatures can catch many restarts,
but PID reuse, an unchanged session reference, or missing process information can
hide replacement. Revalidation cannot remove the race before the rename call.
"""

import asyncio
from copy import deepcopy
from typing import Any, Callable, Protocol

from .models import HerdrError, Invocation, Snapshot
from .sync import (
    agent_name,
    check_sync_agent_name,
    check_sync_source,
    pane_label,
    validate_sync_name,
)
from .validation import sanitize_message, validate_name


UNCERTAIN_MESSAGE = (
    "The rename result could not be confirmed. Check the current name before retrying."
)
_FATAL_CODES = {
    "target_changed", "target_moved", "source_changed", "name_changed",
    "no_agent", "agent_not_found", "pane_not_found", "unsupported",
    "method_not_found", "unknown_method",
}


class RenameClient(Protocol):
    def get_snapshot(self, invocation: Invocation) -> Snapshot: ...

    def rename(self, pane_id: str, name: str) -> dict[str, Any]: ...

    def notify(self, title: str) -> None: ...


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


class Controller:
    def __init__(
        self,
        client: RenameClient,
        invocation: Invocation,
        snapshot: Snapshot | None,
        mode: str,
        initial_error: str = "",
    ) -> None:
        if mode not in {"open", "sync"}:
            raise ValueError("Unknown rename mode.")
        self.client = client
        self.invocation = invocation
        self.snapshot = deepcopy(snapshot)
        self.mode = mode
        self.state = "loading"
        self.message = ""
        self.initial_agent_name = agent_name(snapshot) if snapshot else ""
        self.proposed_name = (
            pane_label(snapshot) if mode == "sync" and snapshot else self.initial_agent_name
        )
        self._busy = False
        self._blocked = False
        self._uncertain_name: str | None = None
        self._external_cancel = False
        if initial_error or snapshot is None:
            self._blocked = True
            self._error(initial_error or "The original pane could not be identified.")
            return
        try:
            _check_snapshot(invocation, snapshot)
        except HerdrError as error:
            self._blocked = True
            self._error(error)
            return
        if mode == "sync":
            error_message = validate_sync_name(snapshot)
            if error_message:
                self._blocked = True
                self._error(error_message)
                return
        self.state = "confirming" if mode == "sync" else "editing"
        if mode == "open" and not self.initial_agent_name:
            self.message = "This agent has no name. Enter a name to continue."

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def can_submit(self) -> bool:
        return not self._busy and not self._blocked and self.state != "closed"

    def _error(self, error: object) -> None:
        self.state = "error"
        self.message = sanitize_message(error)

    def cancel(self) -> bool:
        if self._busy:
            return False
        self.state = "closed"
        return True

    async def _thread_call(self, function: Callable[..., Any], *args: Any) -> Any:
        """Do not abandon a worker thread while a public request is running.

        Ordinary task cancellation is deferred until this bounded call finishes.
        The caller can stop before mutation or complete post-write reconciliation.
        If loop teardown cancels the worker task itself, the caller blocks further
        submissions because cancellation cannot stop an already-running thread.
        """
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                self._external_cancel = True
                if task.cancelled():
                    raise

    async def _success(self) -> bool:
        self.state = "closed"
        self.message = ""
        if self.mode == "sync":
            try:
                await self._thread_call(self.client.notify, "Agent name synced.")
            except Exception:
                # Notification transport cannot invalidate a verified rename.
                pass
        return True

    async def submit(self, name: str | None = None) -> bool:
        if not self.can_submit or self.snapshot is None:
            return False
        # The lock is acquired before the first await, so simultaneous affirmative
        # keys cannot launch duplicate lookups or writes.
        self._busy = True
        self._external_cancel = False
        self.state = "validating"
        candidate = self.proposed_name if self.mode == "sync" or name is None else name
        if self.mode == "open":
            self.proposed_name = candidate
        write_started = False
        write_returned = False
        try:
            error_message = validate_name(candidate)
            if error_message:
                self._error(error_message)
                return False
            current = await self._thread_call(self.client.get_snapshot, self.invocation)
            if self._external_cancel:
                self.state = "closed"
                self.message = ""
                return False
            check_target(self.snapshot, current, self.invocation)
            if self.mode == "sync":
                check_sync_source(self.snapshot, current)
            if self._uncertain_name is not None:
                # Reconcile an already-applied request before checking the initial
                # sync name, which naturally changed if the first write succeeded.
                if candidate == self._uncertain_name and agent_name(current) == candidate:
                    return await self._success()
                self._blocked = True
                self._error(UNCERTAIN_MESSAGE + " Reopen the dialog before another rename.")
                return False
            if self.mode == "sync":
                check_sync_agent_name(self.snapshot, current)
            if agent_name(current) == candidate:
                return await self._success()
            self.state = "submitting"
            write_started = True
            await self._thread_call(self.client.rename, self.invocation.pane_id, candidate)
            write_returned = True
            # The response envelope alone is insufficient: inspect the target again.
            verified = await self._thread_call(self.client.get_snapshot, self.invocation)
            check_target(self.snapshot, verified, self.invocation)
            if self.mode == "sync":
                check_sync_source(self.snapshot, verified)
            if agent_name(verified) != candidate:
                raise HerdrError(UNCERTAIN_MESSAGE, code="unconfirmed", uncertain=True)
            return await self._success()
        except HerdrError as error:
            if self._external_cancel and not write_started:
                self.state = "closed"
                self.message = ""
                return False
            if error.code in _FATAL_CODES:
                self._blocked = True
            if error.uncertain or write_returned:
                self._uncertain_name = candidate
            # A rename failure marked definite by the transport (for example a
            # duplicate-name response) remains explicitly retryable. Any failure
            # during post-write verification is uncertain even if lookup reports
            # a definite failure of its own.
            message = str(error)
            if self._uncertain_name is not None and UNCERTAIN_MESSAGE not in message:
                message = UNCERTAIN_MESSAGE + " " + message
            self._error(message)
            return False
        except asyncio.CancelledError:
            # An actually canceled inner task signals loop teardown, rather than
            # the ordinary cancellation deferred by _thread_call. The worker may
            # still execute; do not offer another affirmative action.
            self._blocked = True
            if write_started:
                self._uncertain_name = candidate
            self._error(
                UNCERTAIN_MESSAGE if write_started
                else "The request was interrupted. Reopen the dialog."
            )
            raise
        except Exception as error:
            if self._external_cancel and not write_started:
                self.state = "closed"
                self.message = ""
                return False
            if write_started:
                self._uncertain_name = candidate
                self._error(UNCERTAIN_MESSAGE + " " + sanitize_message(error))
            else:
                self._error("Could not rename the agent. Try again or cancel. " + sanitize_message(error))
            return False
        finally:
            self._busy = False
