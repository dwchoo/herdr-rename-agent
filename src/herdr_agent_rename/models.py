"""Invocation and API snapshots shared by the launcher and dialogs."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Invocation:
    pane_id: str
    workspace_id: str | None = None
    tab_id: str | None = None


@dataclass(frozen=True)
class Snapshot:
    agent: dict[str, Any]
    pane: dict[str, Any]
    process: dict[str, Any]


class HerdrError(Exception):
    """An English user-facing failure with optional sanitized server details."""

    def __init__(self, message: str, *, code: str = "", uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain
