# Herdr Agent Rename

A personal Linux plugin for Herdr 0.9.3 that renames agents from small terminal popups:

- **Rename** edits the agent name of the pane where the shortcut was pressed, in a dialog styled after Herdr's own rename overlay.
- **Workspace sync** copies explicit pane names to agent names across every pane of the current workspace after one confirmation.

Herdr 0.9.3 has no native agent rename overlay. The dialog reproduces its pane rename overlay (layout, labels, keys, press-activated mouse, theme colors) as closely as a plugin popup allows.

| Shortcut | Action | Result |
| --- | --- | --- |
| `prefix+shift+a` | `local.agent-rename.open` | Edit, clear or cancel the agent name of the focused pane. |
| `prefix+shift+s` | `local.agent-rename.sync` | Preview and confirm copying explicit pane names to agent names for all panes in the current workspace, including other tabs. No is selected initially. |

Status: implemented and verified in an isolated sandbox, not yet deployed to a live Herdr environment; see [verification results](docs/verification-results.md) for exactly what passed. The previous single-pane release is tagged `v0.1.0`. The manifest and package version is 0.2.0.

## Name rules

- Agent names must fully match `[a-z][a-z0-9_-]{0,31}`: 1-32 ASCII characters, beginning with a lowercase letter. Herdr requires them to be unique across the whole session (all workspaces and tabs).
- Rename follows Herdr's editor: leading and trailing whitespace is trimmed on save, and pasted CR, LF and Tab become one space. Other control characters are shown as a visible U+FFFD and rejected by validation; the plugin never turns invalid input into a different valid name.
- Sync uses the pane label exactly as stored. It does not trim or transform it, so a label such as `Writer!` or `two words` is reported as an invalid name and skipped.
- An empty save in Rename removes the agent's explicit name (`herdr agent rename <target> --clear`). Sync never clears a name.

## Requirements

Linux, Herdr 0.9.3 or newer with a running server (0.9.3 is the only verified version), Python 3.11 or newer, and either `uv` or Python's `venv`/`pip` (on some distributions that means `python3-venv`). Runtime dependencies are hash-locked in `requirements.lock`: `prompt_toolkit`, `wcwidth`, `regex` (grapheme segmentation for the editor) and `tomlkit` (installer). Compatibility baseline and public API limits: [compatibility](docs/compatibility.md).

## Local setup

Keep the checkout in a stable directory (the plugin is linked to it). The complete person/agent procedure, including updating an existing install, is [INSTALL.md](INSTALL.md).

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
description = "Sync agent names from pane names"
command = "local.agent-rename.sync"
```

Validation/reload runs automatically. In remote/multiple-server use, choose the intended server and also reload through Herdr's global menu where needed so local client and server key settings refresh. No server restart or agent termination is required. [Official reload instructions](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/configuration.mdx#reload-config)

## Using the dialogs

Press your prefix (normally Ctrl+B), then Shift+A or Shift+S while the intended pane is focused. Rename freezes that pane; sync freezes that pane's workspace and the panes found in it. Opening a dialog never sends input to a running agent. Keys typed before the popup exists go to the pane, not the popup.

Colors come from your active Herdr theme (see [Theme](#theme)). Herdr draws the popup's outer border and the title `Rename agent` / `Sync agent name`; the dialogs add no second frame.

### Rename agent

The input starts with the pane's current explicit agent name (empty if none), cursor at the end. The popup is 57x7 cells, giving a 54x5 content area like Herdr's overlay: title `rename agent`, the input, and three buttons ` ↵ save `, ` ^c clear `, ` esc cancel `.

| Input | Effect |
| --- | --- |
| Enter | Save. Non-empty text renames the agent. Empty text removes its explicit name. Unchanged text (or clearing an already unnamed agent) closes without a write. |
| Ctrl+C | Clears the field only; nothing is sent until you save. |
| Esc | Cancel without writing. |
| Left/Ctrl+B, Right/Ctrl+F, Home/Ctrl+A, End/Ctrl+E | Move by grapheme, to start, to end. |
| Alt+B, Alt+F | Move by word. |
| Backspace/Ctrl+H, Delete/Ctrl+D | Delete one grapheme. |
| Ctrl+U, Ctrl+K, Ctrl+W, Alt+Backspace, Alt+D, Ctrl+Y | Kill to start/end, kill word, yank from the local kill buffer (not the system clipboard). |
| Mouse | A left press on ` ↵ save ` saves, on ` ^c clear ` clears the field. A press anywhere else, including ` esc cancel `, closes without saving (Herdr's behavior). Activation is on press. |

There is no replace-on-type and no keyboard button focus (Tab does nothing), as in Herdr's rename overlay. Errors (invalid or duplicate name, changed target, failed request) appear on the row below the buttons, keep the entered text and leave the dialog usable. While a request runs, input is locked. After an uncertain result, a retry reads the target first and closes without another write if the desired name is already present.

### Sync agent names

The popup is sized `80%` x `70%` of the pane area. It shows a table of every pane in the workspace, all tabs, in tab order: Tab, Pane, current agent name (`(unnamed)` when the agent has none), the pane name that would be copied, and a status.

| Row status | Meaning |
| --- | --- |
| change | Valid explicit pane name, running agent, different from the agent name. Will be written on Yes. |
| matches | Agent name already equals the pane name. |
| no pane name | The pane has no explicit name. Its agent keeps its name. |
| no agent | No running agent in the pane. |
| invalid name | The pane name is not a valid agent name. |
| conflict | The name is wanted by two panes in the batch, or already held by another agent anywhere in the session. Neither duplicate wins; independent rows still proceed. No temporary clears or swaps are used. |
| lookup failed | A per-pane lookup returned unusable data; the row is skipped. |

The selected row's full names appear in a detail line. Totals and the buttons stay visible while the table scrolls. On narrow terminals the Tab column collapses first, then the Pane column. Below a popup size of 40x10 cells (for example a client around 50 columns wide) the popup shows a resize message and only Esc works.

| Input | Effect |
| --- | --- |
| Up/Down, PgUp/PgDn, Home/End, mouse wheel | Scroll the table. |
| Left/Right, Tab/Shift+Tab | Switch between Yes and No. No is selected first. |
| Y | Start the batch immediately (the Yes button). |
| N, Esc, Ctrl+C | Cancel with zero writes. |
| Enter, Space | Activate the selected button. |
| Mouse | A left press on `[ Yes ]`, `[ No ]`, `[ Stop ]` or `[ Close ]`, or on a table row to select it. |

Yes authorizes exactly the previewed `change` rows. Writes are sequential, at most one request per row, each verified by a read before the next. Before each write the row is rechecked (pane still in the workspace and at the same location, same terminal and agent observations, same pane name, same agent name); a row that changed is skipped with a reason and never retargeted. A definite row failure does not stop independent rows. A transport failure during a write stops the remaining queue unless a follow-up read confirms the rename. Uncertain results are reported, never retried.

While the batch runs the buttons become `[ Stop ]`. Stop, Esc and Ctrl+C stop the queue after the in-flight row is reconciled; remaining rows are reported as not attempted. Enter and Space are ignored while a batch runs, so a repeated key cannot stop it by accident, and repeated Y, Enter or clicks cannot start a second batch. Afterwards the table shows each row's outcome (synced, already matching, skipped, failed, uncertain, not attempted), a summary line, and a single `[ Close ]`. Partial success is reported as such; nothing is rolled back.

Nothing to do: if no pane needs a change and nothing is wrong, no popup opens and a Herdr notification says "All agent names already match their pane names." (the notification text is not observable in the headless test client). If there are no changes but some rows are conflicts, invalid or failed lookups, the popup opens in information mode: the same table with Close only. If the workspace cannot be established or a required API is unavailable, a notification and the plugin log explain it.

### Theme

The dialogs read `[theme]` from your Herdr config (`HERDR_CONFIG_PATH`, else `$XDG_CONFIG_HOME/herdr/config.toml`, else `~/.config/herdr/config.toml`), read-only, once per dialog. All 18 built-in themes, name aliases, `[theme.custom]` tokens and the legacy `[ui] accent` follow Herdr's resolution. A missing or invalid file, or an unknown name, gives Herdr's default `catppuccin`. With `auto_switch = true` the plugin cannot read the terminal's light/dark appearance and assumes dark (`dark_name`, else the dark sibling of `name`). Only Dracula and the default theme were checked against captures.

### Limits

Herdr's rename API has no expected-identity precondition, so a race between the final read and a write cannot be excluded. Mouse works only where Herdr forwards it to the popup (it does when the popup enables mouse reporting). See [compatibility](docs/compatibility.md) for the full list.

## Removal and updates

Use the helper to remove plugin-owned action tables, selectively restore displaced bindings, unlink the checkout, and reload/verify the result:

```bash
bin/uninstall --dry-run
bin/uninstall
```

Uninstall never restores a whole config backup. Later theme/settings changes and unrelated bindings/comments remain. A newer user binding wins over restoration; any blocked former binding stays in a private scoped recovery record, whose path is printed. The checkout, `.venv`, and plugin config/state directories remain. See [INSTALL.md](INSTALL.md#uninstall) for offline cleanup and recovery. Raw `herdr plugin unlink` alone leaves config bindings in place. [Official removal contract](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/plugins.mdx#install-and-link)

To update an existing install, run `bin/setup` (refreshes `.venv`, including the `regex` dependency), then `bin/install` and reload; see [INSTALL.md](INSTALL.md#update-an-existing-install). Existing matching bindings are not duplicated. Herdr plugin v1 has no separate `plugin update` command. To roll back: `git checkout v0.1.0 && bin/setup && bin/install`. Future Herdr versions require fresh API and UI verification; the minimum version declaration does not certify them.

## Development

Install the test dependency lock and run the suite:

```bash
uv pip install --python .venv/bin/python --require-hashes -r requirements-test.lock
.venv/bin/python -m pytest -q
```

If setup used Python's fallback instead of `uv`, use `.venv/bin/python -m pip install --require-hashes -r requirements-test.lock` for the first command. Tests use isolated fixtures and pseudo-terminals; they never contact your Herdr.

For real Herdr E2E inside a disposable Linux OS sandbox (needs `bubblewrap`, `herdr` and `codex` on PATH):

```bash
.venv/bin/python tools/verify_sandbox.py --run --suite next   # workspace sync, rename, themes, installer lifecycle
.venv/bin/python tools/verify_sandbox.py --run                # original single-pane suite (v0.1.0 runtime only)
```

The sandbox uses a copied checkout, a fresh HOME, separate PID/network namespaces and no credentials or model prompts. See the [sandbox procedure](docs/verification.md#os-sandbox-e2e) and the recorded results in [docs/sandbox-next/](docs/sandbox-next/actual-results.json). `tools/probe_r1.py --run` reruns the live investigation behind [r1-live-findings](docs/r1-live-findings.md).

The plugin calls the public CLI through `HERDR_BIN_PATH` (or `herdr` from PATH), preserves connection context, and runs argv commands without shell interpolation. It starts no resident service and changes only agent names. The implementation record is [PLAN.md](PLAN.md); Herdr source findings are in [r1-source-findings](docs/r1-source-findings.md).

Large snapshots (many panes) use a temporary private file in Herdr's plugin state directory, up to 4 MiB, and a short popup environment pointer. Successful decode removes the file; launch failure attempts cleanup. A terminated process may leave a stale file, which can be removed after this plugin's dialogs/actions have closed. See [the handoff contract](docs/compatibility.md#frozen-origin).

## License

Apache-2.0, see [LICENSE](LICENSE). Ported Herdr logic is attributed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
