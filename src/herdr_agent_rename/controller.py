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
from .targets import _check_snapshot, check_target
from .sync import agent_name
from .validation import sanitize_message, validate_name


UNCERTAIN_MESSAGE = (
    "The rename result could not be confirmed. Check the current name before retrying."
)
_FATAL_CODES = {
    "target_changed", "target_moved", "source_changed", "name_changed",
    "no_agent", "agent_not_found", "pane_not_found", "unsupported",
    "method_not_found", "unknown_method",
}


# Rust ``str::trim`` removes the Unicode ``White_Space`` property, which Herdr
# uses on save. Python's ``str.strip`` differs (it also strips U+001C-U+001F),
# so list the Rust set explicitly.
_RUST_WHITESPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def trim_name(text: str) -> str:
    """Trim leading/trailing whitespace exactly as Herdr's overlay save does (D7)."""
    return text.strip(_RUST_WHITESPACE)


class RenameClient(Protocol):
    def get_snapshot(self, invocation: Invocation) -> Snapshot: ...

    def rename(self, pane_id: str, name: str) -> dict[str, Any]: ...

    def clear_name(self, pane_id: str) -> dict[str, Any]: ...


class Controller:
    def __init__(
        self,
        client: RenameClient,
        invocation: Invocation,
        snapshot: Snapshot | None,
        mode: str,
        initial_error: str = "",
    ) -> None:
        if mode != "open":
            raise ValueError("Unknown rename mode.")
        self.client = client
        self.invocation = invocation
        self.snapshot = deepcopy(snapshot)
        self.mode = mode
        self.state = "loading"
        self.message = ""
        self.initial_agent_name = agent_name(snapshot) if snapshot else ""
        self.proposed_name = self.initial_agent_name
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
        self.state = "editing"
        if not self.initial_agent_name:
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
        return True

    async def submit(self, name: str | None = None) -> bool:
        if not self.can_submit or self.snapshot is None:
            return False
        # The lock is acquired before the first await, so simultaneous affirmative
        # keys cannot launch duplicate lookups or writes.
        self._busy = True
        self._external_cancel = False
        self.state = "validating"
        # Open mode receives the raw editor text: trim it as Herdr does (D7);
        # an empty result means "remove the explicit name" (``--clear``).
        candidate = self.proposed_name if name is None else trim_name(name)
        self.proposed_name = candidate
        write_started = False
        write_returned = False
        try:
            error_message = None if candidate == "" else validate_name(candidate)
            if error_message:
                self._error(error_message)
                return False
            current = await self._thread_call(self.client.get_snapshot, self.invocation)
            if self._external_cancel:
                self.state = "closed"
                self.message = ""
                return False
            check_target(self.snapshot, current, self.invocation)
            if self._uncertain_name is not None:
                # Reconcile an already-applied request.
                if candidate == self._uncertain_name and agent_name(current) == candidate:
                    return await self._success()
                self._blocked = True
                self._error(UNCERTAIN_MESSAGE + " Reopen the dialog before another rename.")
                return False
            if agent_name(current) == candidate:
                return await self._success()
            self.state = "submitting"
            write_started = True
            if candidate == "":
                await self._thread_call(self.client.clear_name, self.invocation.pane_id)
            else:
                await self._thread_call(self.client.rename, self.invocation.pane_id, candidate)
            write_returned = True
            # The response envelope alone is insufficient: inspect the target again.
            verified = await self._thread_call(self.client.get_snapshot, self.invocation)
            check_target(self.snapshot, verified, self.invocation)
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
