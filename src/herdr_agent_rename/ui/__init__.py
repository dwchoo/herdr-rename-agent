"""Popup dialogs: ``run_dialog`` picks the dialog for the controller's mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .common import MIN_WIDTH, RESIZE_MESSAGE, display_text
from .rename import RenameDialog
from .sync import SyncDialog

if TYPE_CHECKING:
    from ..controller import Controller

__all__ = [
    "MIN_WIDTH", "RESIZE_MESSAGE", "RenameDialog", "SyncDialog", "display_text", "run_dialog",
]


async def run_dialog(controller: Controller) -> bool:
    dialog = SyncDialog if controller.mode == "sync" else RenameDialog
    return await dialog(controller).run_async()
