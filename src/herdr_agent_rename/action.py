"""One-shot action launch and explicit frozen-context popup handoff."""

import argparse
import asyncio
import os
import sys

from .client import HerdrClient, sanitize
from .context import (
    cleanup_handoff, decode_handoff, decode_sync_handoff, encode_handoff, freeze_workspace,
    parse_context, parse_sync_context,
)
from .models import HerdrError, Invocation
from .sync import SyncRunner, collect_plan

ALL_MATCH = "All agent names already match their pane names."


def launch_sync(env: dict[str, str]) -> None:
    """Freeze the invoking workspace and its panes, then pick the popup mode.

    No eligible and no problem rows: notification only. No eligible rows but
    problems: information popup. Otherwise: confirmation popup. The plan
    travels in the handoff; ``decode_sync_handoff`` returns it to the popup.
    """
    client = HerdrClient(env)
    try:
        client.verify_support()
        invocation = freeze_workspace(parse_sync_context(env), client.pane_get)
        plan = collect_plan(client, invocation.workspace_id)
    except HerdrError as exc:
        try:
            client.notify(sanitize(str(exc)))
        except HerdrError:
            pass
        raise
    if plan.mode == "noop":
        client.notify(ALL_MATCH)
        return
    try:
        handoff = encode_handoff(invocation, None, "", env, plan)
    except HerdrError as exc:
        try:
            client.notify(sanitize(str(exc)))
        except HerdrError:
            pass
        raise
    try:
        client.open_popup(invocation, "sync", handoff)
    except BaseException:
        cleanup_handoff(handoff, env)
        raise


def launch(mode: str, env: dict[str, str]) -> None:
    if mode == "sync":
        return launch_sync(env)
    client = HerdrClient(env)
    snapshot = None
    error = ""
    try:
        invocation = parse_context(env)
    except HerdrError as exc:
        invocation = Invocation("")
        error = str(exc)
    client.verify_support()
    if not error:
        try:
            snapshot = client.get_snapshot(invocation)
            if snapshot.pane["pane_id"] != invocation.pane_id or (invocation.workspace_id and snapshot.pane["workspace_id"] != invocation.workspace_id) or (invocation.tab_id and snapshot.pane["tab_id"] != invocation.tab_id):
                snapshot = None
                error = "The target pane has moved. Reopen the dialog."
        except HerdrError as exc:
            error = str(exc)
    handoff = encode_handoff(invocation, snapshot, error, env)
    try:
        client.open_popup(invocation, mode, handoff)
    except BaseException:
        cleanup_handoff(handoff, env)
        raise


async def dialog_sync(env: dict[str, str]) -> bool:
    """Workspace sync popup: ``ui.run_sync_dialog(runner)`` shows ``runner.mode``."""
    from . import ui

    plan = None
    try:
        invocation, plan, error, connection = decode_sync_handoff(env)
    except HerdrError as exc:
        error, connection = str(exc), {}
    if plan is None and not error:
        error = "The workspace could not be identified."
    client = HerdrClient({**env, **connection})
    if plan is not None:
        try:
            await asyncio.to_thread(client.verify_support)
        except HerdrError as exc:
            plan, error = None, str(exc)
    return await ui.run_sync_dialog(SyncRunner(client, plan, error))


async def dialog(mode: str, env: dict[str, str]) -> bool:
    if mode == "sync":
        return await dialog_sync(env)
    from .controller import Controller
    from .ui import run_dialog

    try:
        invocation, snapshot, error, connection = decode_handoff(env)
    except HerdrError as exc:
        invocation, snapshot, error, connection = Invocation(""), None, str(exc), {}
    client = HerdrClient({**env, **connection})
    try:
        await asyncio.to_thread(client.verify_support)
    except HerdrError as exc:
        snapshot, error = None, str(exc)
    controller = Controller(client, invocation, snapshot, mode, initial_error=error)
    success = await run_dialog(controller)
    return success


def main() -> int:
    parser = argparse.ArgumentParser(description="Rename the current Herdr agent, or copy explicit pane names to agent names across the current workspace after confirmation.")
    parser.add_argument("action", choices=("open", "sync", "dialog-open", "dialog-sync"))
    args = parser.parse_args()
    try:
        if args.action.startswith("dialog-"):
            asyncio.run(dialog(args.action.removeprefix("dialog-"), dict(os.environ)))
        else:
            launch(args.action, dict(os.environ))
    except HerdrError as exc:
        print(sanitize(str(exc)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
