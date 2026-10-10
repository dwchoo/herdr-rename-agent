"""Popup dialogs: ``run_dialog`` for rename, ``run_sync_dialog`` for workspace sync."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .common import MIN_WIDTH, RESIZE_MESSAGE, display_text
from .rename import RenameDialog
from .sync import SyncDialog

if TYPE_CHECKING:
    from ..controller import Controller
    from ..sync import SyncRunner

__all__ = [
    "MIN_WIDTH", "RESIZE_MESSAGE", "RenameDialog", "SyncDialog", "display_text",
    "run_dialog", "run_sync_dialog",
]


async def run_dialog(controller: Controller) -> bool:
    return await RenameDialog(controller).run_async()


async def run_sync_dialog(runner: SyncRunner) -> bool:
    """True when at least one agent name was synced."""
    return await SyncDialog(runner).run_async()
