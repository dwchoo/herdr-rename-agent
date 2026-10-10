# Herdr Agent Rename

An independent Linux plugin that opens a small terminal dialog to rename the agent in the pane where the action was invoked.

| Shortcut | Action | Result |
| --- | --- | --- |
| `prefix+shift+a` | `local.agent-rename.open` | Edit the agent name, then choose Apply or Cancel. |
| `prefix+shift+s` | `local.agent-rename.sync` | Confirm copying the pane's explicit name to the agent name. No is selected initially. |

Synchronization runs once, from pane name to agent name. Names must match `[a-z][a-z0-9_-]{0,31}`: 1–32 ASCII characters, beginning with a lowercase letter. The plugin preserves the entered text and the exact pane label; it does not trim or transform either value. Herdr checks live-name uniqueness.

The initial compatibility baseline is Herdr **0.9.3**, Linux, and Python **3.11 or newer**. The [verification results](docs/verification-results.md) state which checks actually passed. Public identity checks have unavoidable limitations; see [compatibility](docs/compatibility.md) before treating this as a fully verified release.

## Local setup

Keep the checkout in a stable directory. You need a running Herdr server, Python, and either `uv` or Python's `venv`/`pip` support. On distributions that package it separately, install `python3-venv` to use the fallback setup path. The complete person/agent procedure is [INSTALL.md](INSTALL.md).

From the checkout:

```bash
bin/install --dry-run
bin/install
```

The helper prepares hash-pinned dependencies in `.venv`, links the checkout, manages only this plugin's bindings, validates/reloads configuration, and verifies its actions. `bin/setup` remains available for dependency setup alone; raw `herdr plugin link` does not execute build hooks. The launchers resolve their source directory, so invocation from another directory or through a symlink uses the same project environment. [Official installation behavior](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/plugins.mdx#install-and-link)

You can also ask a coding agent: "Read this checkout's INSTALL.md and install the plugin in my current Herdr environment." Development and isolated tests do not install it into the normal environment.

## Shortcuts and conflicts

The helper honors `HERDR_CONFIG_PATH` and the XDG config/state roots; `--config` and `--session` select an explicit file and running session. Herdr's `prefix+?` help shows active bindings. The helper preserves existing matching action entries, including customized shortcuts, and migrates this project's known legacy rename shell popup automatically.

Treat `prefix+A`, `prefix+Shift+A`, and `prefix+shift+a` as the same shortcut; the corresponding spellings for S also coincide. An unrelated command conflict is reported without modifying the config; use `bin/install --replace-conflicts --dry-run` to inspect an intentional replacement, then omit `--dry-run` to apply it. Built-in key collisions require explicit resolution. [Herdr 0.9.3 key parser](https://github.com/herdrdev/herdr/blob/v0.9.3/src/config/keybinds.rs#L1269)

The managed entries use these action IDs:

```toml
[[keys.command]]
key = "prefix+shift+a"
type = "plugin_action"
description = "Rename current agent"
command = "local.agent-rename.open"

[[keys.command]]
key = "prefix+shift+s"
type = "plugin_action"
description = "Set agent name from pane name"
command = "local.agent-rename.sync"
```

Validation/reload runs automatically. In remote/multiple-server use, choose the intended server and also reload through Herdr's global menu where needed so local client and server key settings refresh. No server restart or agent termination is required. [Official reload instructions](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/configuration.mdx#reload-config)

## Using the dialogs

Press your configured prefix (normally Ctrl+B), then Shift+A or Shift+S while the intended agent pane is focused. The action freezes that pane before opening the popup. Opening a dialog never sends input to the running agent.

In Rename agent, type a name and press Enter or choose Apply. Left/Right, Home/End, Backspace/Delete, and horizontal scrolling work in the field. Tab and Shift+Tab cycle through Name, Apply, and Cancel. Invalid input remains available for correction; pasted newlines do not submit or become a different valid name.

In Sync agent name, inspect the current and proposed names, then choose Yes or No. Tab and Shift+Tab move between the buttons; Enter activates the focused button. Left/Right and Home/End scroll long names without editing them. A changed pane name or agent name invalidates the confirmation and requires reopening. Missing/invalid pane names and missing agents leave confirmation disabled.

Esc or Ctrl+C cancels before a submission. Errors remain in the popup with a cancellation path. During a request, affirmative submissions and cancellation are locked until it completes or is reconciled; cancellation cannot undo a write already sent. For an uncertain result, an explicit retry reads the target first and closes without another write if the desired name is already present. Otherwise reopen after checking the current name. If the popup is too small, enlarge the terminal or press Esc to cancel.

## Removal and updates

Use the helper to remove plugin-owned action tables, selectively restore displaced bindings, unlink the checkout, and reload/verify the result:

```bash
bin/uninstall --dry-run
bin/uninstall
```

Uninstall never restores a whole config backup. Later theme/settings changes and unrelated bindings/comments remain. A newer user binding wins over restoration; any blocked former binding stays in a private scoped recovery record, whose path is printed. The checkout, `.venv`, and plugin config/state directories remain. See [INSTALL.md](INSTALL.md#uninstall) for offline cleanup and recovery. Raw `herdr plugin unlink` alone leaves config bindings in place. [Official removal contract](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/plugins.mdx#install-and-link)

After updating this checkout, rerun `bin/setup`, `bin/install`, and the [verification checklist](docs/verification.md). Existing matching bindings are not duplicated. Herdr plugin v1 has no separate `plugin update` command. Future Herdr versions require fresh API and UI verification; the minimum version declaration does not certify them.

## Development

Install the test dependency lock and run the suite:

```bash
uv pip install --python .venv/bin/python --require-hashes -r requirements-test.lock
.venv/bin/python -m pytest
```

If setup used Python's fallback instead of `uv`, use `.venv/bin/python -m pip install --require-hashes -r requirements-test.lock` for the first command. Tests use isolated fixtures and terminal processes; actual Herdr checks use a separate test environment as described in [verification](docs/verification.md).

For real Herdr E2E inside a disposable Linux OS sandbox, install `bubblewrap` and run `.venv/bin/python tools/verify_sandbox.py --run`. It uses a copied checkout, fresh HOME, separate PID/network namespaces, and no user credentials or model prompts. See the [sandbox procedure](docs/verification.md#os-sandbox-e2e) and [recorded results](docs/sandbox/actual-results.json).

The plugin calls the public CLI through `HERDR_BIN_PATH` (or `herdr` from PATH), preserves connection context, and runs argv commands without shell interpolation. It starts no resident service and changes only agent names. The implementation and acceptance criteria are recorded in [PLAN.md](PLAN.md).

Large target snapshots use temporary private files in Herdr's plugin state directory and a short popup environment pointer. Successful decode removes the file; launch failure attempts cleanup. A terminated process may leave a stale file, which can be removed after this plugin's dialogs/actions have closed. See [the handoff contract](docs/compatibility.md#frozen-origin).
