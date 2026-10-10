# Compatibility and public API limits

## Baseline

The initial baseline is Linux, Python 3.11+, and the installed `herdr 0.9.3` executable. The manifest declares `min_herdr_version = "0.9.3"` and `platforms = ["linux"]`. The launcher also checks the version and required schema capabilities. A newer version is eligible for verification, not automatically certified. Windows and macOS are outside this release's scope.

See [verification-results.md](verification-results.md) for observed outcomes. A schema/help check, source review, or mocked test must not be reported as a completed interactive UI check.

## Operations and transport

The implementation uses these public CLI operations:

| Purpose | Operation |
| --- | --- |
| Version and capabilities | `herdr --version`; `herdr api schema --json` |
| Agent state | `herdr agent get <original-pane-id>` |
| Pane location and explicit label | `herdr pane get <original-pane-id>` |
| Additional process observations | `herdr pane process-info --pane <original-pane-id>` |
| One agent-name write | `herdr agent rename <original-pane-id> <name>` |
| Popup launch | `herdr plugin pane open --plugin local.agent-rename --entrypoint <id> --env <handoff>` |
| Successful sync notification | `herdr notification show "Agent name synced."` |

CLI calls are argv arrays with stdin disconnected, bounded durations, captured JSON output, and the supplied connection environment. The plugin prefers `HERDR_BIN_PATH` over PATH resolution. Unknown envelopes and incomplete required records are errors; terminal output and visual titles are not parsed as agent names. The pane's `label` is the synchronization source.

The installed 0.9.3 schema supports `plugin.pane.open` placement `popup`, numeric dimensions, and launch environment fields. Its CLI `--placement` help lists only overlay, split, tab, and zoomed. The plugin therefore declares popup placement and dimensions in its manifest and supplies no CLI placement override. Runtime opening still requires an available modal surface; Herdr may report `ui_busy`. [Versioned popup contract](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/plugins.mdx#panes)

## Frozen origin

The action structurally parses `HERDR_PLUGIN_CONTEXT_JSON` and freezes `focused_pane_id` plus available `workspace_id`/`tab_id`. It captures agent, pane, and public process observations before launching the popup. The separate `HERDR_AGENT_RENAME_HANDOFF` environment field carries the frozen invocation, snapshot, initial errors, and connection values through public `--env` support.

This separation matters because Herdr 0.9.3 recomputes `current_plugin_context("plugin-pane")` when opening a popup. The installed CLI rejects `--target-pane` and `--workspace` for popup/overlay placement, so the launcher supplies neither; the explicit handoff determines the agent target. Herdr also protects and overwrites its own plugin context, binary, and socket environment keys. The plugin-owned handoff preserves the original values without attempting to overwrite those protected keys. [Public host implementation at v0.9.3](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/api/plugins/panes.rs#L12)

Inline handoffs are limited to 48 KiB so they fit Linux's per-argument/environment limits. Larger snapshots first drop metadata unused by the dialogs and identity checks, preserving exact names, pane labels, location/session data, and observed process executable fields. If the remaining payload is still large, a short environment pointer identifies a temporary file in `HERDR_PLUGIN_STATE_DIR`. The file is limited to 16 MiB, has a random validated filename, and must be a regular file owned by the current user with mode 0600 and one link. Decode checks the expected state directory, refuses a symlink, validates the bounded payload, then consumes and unlinks the file. Popup-launch failures trigger best-effort cleanup. Missing state storage or an excessive payload produces an English error instead of an oversized process launch.

A private file can remain if the action or popup is terminated before decoding or cleanup. There is no resident cleanup process. Inspect `agent-rename-handoff-*.json` files in this plugin's state directory and remove stale files only after its dialogs/actions have closed; leave other plugin state untouched.

Missing/malformed invocation or handoff data disables submission. The plugin never picks a different focused pane, uses the popup as an agent target, or falls back to `HERDR_PANE_ID`.

## Identity coverage

Before every write, the controller freshly queries the frozen target and checks:

| Observation | Check and coverage |
| --- | --- |
| Canonical pane/workspace/tab IDs | Must agree with the frozen origin and initial location. A moved pane that still resolves through an old alias is rejected. |
| Agent and pane terminal IDs | Must agree and remain unchanged; catches terminal replacement, not every new occupant in the same terminal. |
| Agent kind | Must remain unchanged. |
| Optional `agent_session` | Exact available reference must remain unchanged. It is session metadata, not a verified immutable process-launch token. |
| Foreground processes | If an initial nonempty list and a current list are available, at least one PID with the same known executable fields must remain. Child process churn is allowed. |
| Agent status, revision, focus, cwd | These can change during normal work and are not treated as replacement evidence. |

For synchronization, the explicit pane label and agent name must also match the values shown when confirmation opened. The plugin performs a fresh read after rename and confirms the desired name before reporting success.

Strict replacement detection remains incomplete. Public process observations contain no process start-time/generation token. PID reuse with identical executable data can remain indistinguishable. A replacement that preserves the terminal, kind, and session reference can evade detection when process evidence is absent, incomplete, or shares a surviving process. Public `agent_session` references may be absent or reused during resumed sessions. A terminal ID alone cannot prove that the same agent still occupies it.

The `AgentRenameParams` schema exposes `target` and optional `name` without an expected-identity/version precondition. Public v0.9.3 rename resolves the target and writes its current agent name. There is therefore a race between the final read and the rename request, and another observation window before post-write verification. Revalidation is not an atomic compare-and-set. **The strict identity acceptance criterion in PLAN.md remains unmet.** [Public rename implementation](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/agents.rs#L90)

## Popup geometry and appearance

Read-only review of official v0.9.3 source gives this native baseline:

| Surface | Requested outer cells | Normal content/PTY cells |
| --- | --- | --- |
| Native rename dialog | 56 × 7 | 54 × 5 content |
| Plugin rename popup | 56 × 11 | 53 × 9 PTY |
| Plugin sync popup | 60 × 14 | 57 × 12 PTY |

The native renderer clamps to screen width minus four and height minus two, draws a square-corner accent border, and uses `panel_bg` for content and `surface0` for the input. Its title, input, and buttons occupy inner rows 0, 2, and 3; the buttons are save, clear, and cancel. The plugin uses Apply/Cancel and Yes/No, with additional explanation and a reserved error area. [Native rename renderer](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlays.rs#L630)

Terminal plugin popups include Herdr's outer border. In v0.9.3, outer dimensions clamp to a minimum of 6 × 4 and available screen size; typical inner PTY dimensions subtract three columns and two rows, including a reserved gutter. The plugin avoids another internal frame. [Popup geometry implementation](https://github.com/herdrdev/herdr/blob/v0.9.3/src/popup_size.rs#L51)

These dimensions were checked against the actual isolated 0.9.3 client captures at 100 × 32 cells; see [results and captures](verification-results.md#visual-comparison). The plugin is taller than the native rename dialog because it reserves error and confirmation rows. Its input uses terminal reverse video and its buttons use focus highlighting, so colors differ from the native renderer. Herdr centers terminal popups over the pane area; its native rename dialog is centered over the full frame. No internal title or border duplicates Herdr's popup frame.

Closing the last pane in a popup's original tab can make Herdr close the popup itself. The plugin cannot retain an error dialog after its host terminal is removed. The isolated test observed this host closure without renaming another agent.
