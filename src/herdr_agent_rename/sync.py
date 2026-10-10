"""One-time pane-label synchronization rules shared by controller and UI."""

from .models import HerdrError, Snapshot
from .validation import validate_name


def agent_name(snapshot: Snapshot) -> str:
    value = snapshot.agent.get("name")
    return value if isinstance(value, str) else ""


def pane_label(snapshot: Snapshot) -> str:
    value = snapshot.pane.get("label")
    return value if isinstance(value, str) else ""


def validate_sync_name(snapshot: Snapshot) -> str | None:
    label = snapshot.pane.get("label")
    if label is None or label == "":
        return "This pane has no name."
    if validate_name(label) is not None:
        return "The pane name is not a valid agent name."
    return None


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
