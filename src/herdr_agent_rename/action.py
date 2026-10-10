"""One-shot action launch and explicit frozen-context popup handoff."""

import argparse
import asyncio
import os
import sys

from .client import HerdrClient, sanitize
from .context import cleanup_handoff, decode_handoff, encode_handoff, parse_context
from .models import HerdrError, Invocation


def launch(mode: str, env: dict[str, str]) -> None:
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


async def dialog(mode: str, env: dict[str, str]) -> bool:
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
    parser = argparse.ArgumentParser(description="Rename the current Herdr agent or confirm copying its pane name.")
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
