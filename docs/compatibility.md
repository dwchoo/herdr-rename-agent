# Compatibility and public API limits

## Baseline

The initial baseline is Linux, Python 3.11+, and the installed `herdr 0.9.3` executable. The manifest declares `min_herdr_version = "0.9.3"` and `platforms = ["linux"]`. The launcher also checks the version and required schema capabilities (below). Newer version strings and schema shapes are tolerated when the capabilities match; unknown session kinds on other agents do not block sync. A newer version is eligible for verification, not automatically certified. Windows and macOS are outside this release's scope.

See [verification-results.md](verification-results.md) for observed outcomes. A schema/help check, source review, or mocked test must not be reported as a completed interactive UI check.

## Operations and transport

The implementation uses these public CLI operations:

| Purpose | Operation |
| --- | --- |
| Version and capabilities | `herdr --version`; `herdr api schema --json` |
| Agent state | `herdr agent get <pane-id>` |
| Pane location and explicit label | `herdr pane get <pane-id>` |
| Workspace panes (every tab) | `herdr pane list --workspace <workspace-id>` |
| All agents, session-wide (names and conflicts) | `herdr agent list` |
| Additional process observations | `herdr pane process-info --pane <pane-id>` |
| One agent-name write | `herdr agent rename <pane-id> <name>` |
| Explicit-name removal | `herdr agent rename <pane-id> --clear` |
| Popup launch | `herdr plugin pane open --plugin local.agent-rename --entrypoint <id> --env <handoff>` |
| Nothing-to-sync and launch-failure notices | `herdr notification show "<text>"` (for example "All agent names already match their pane names.") |

CLI calls are argv arrays with stdin disconnected, bounded durations, captured JSON output, and the supplied connection environment. The plugin prefers `HERDR_BIN_PATH` over PATH resolution. Unknown envelopes and incomplete required records are errors; terminal output and visual titles are not parsed as agent names. The pane's `label` is the synchronization source: it is the explicit pane name, and the key is absent when the pane is unnamed. `pane list` entries carry no agent name; the agent name exists only in `agent list`/`agent get` as `name`, joined to panes by `pane_id`. Each pane hosts at most one agent. Agent names are unique across the whole session, so a name held in another workspace is a conflict. Plugin popups never appear in `pane list` or `agent list`.

### Capability checks

Before any dialog work the client requires Linux, a `herdr --version` of 0.9.3 or newer, and in `herdr api schema --json` the methods `agent.get`, `agent.list`, `agent.rename`, `pane.get`, `pane.list`, `pane.process_info`, `plugin.pane.open` and `notification.show`. When the schema describes them, it also requires popup placement, `AgentRenameParams` fields `target` and `name` with `name` nullable (for `--clear`), `PluginPaneOpenParams` fields `env`, `target_pane_id` and `workspace_id`, `PluginInvocationContext` fields `focused_pane_id`, `workspace_id` and `tab_id`, and `PaneListParams.workspace_id`. Missing capabilities are an English error, not a partial run.

The installed 0.9.3 schema supports `plugin.pane.open` placement `popup`, numeric dimensions, and launch environment fields. Its CLI `--placement` help lists only overlay, split, tab, and zoomed. The plugin therefore declares popup placement and dimensions in its manifest and supplies no CLI placement override. Runtime opening still requires an available modal surface; Herdr may report `ui_busy`. [Versioned popup contract](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/plugins.mdx#panes)

## Frozen origin

The action structurally parses `HERDR_PLUGIN_CONTEXT_JSON` and freezes `focused_pane_id` plus available `workspace_id`/`tab_id`. It captures agent, pane, and public process observations before launching the popup. The separate `HERDR_AGENT_RENAME_HANDOFF` environment field carries the frozen invocation, snapshot, initial errors, and connection values through public `--env` support.

This separation matters because Herdr 0.9.3 recomputes `current_plugin_context("plugin-pane")` when opening a popup. The installed CLI rejects `--target-pane` and `--workspace` for popup/overlay placement, so the launcher supplies neither; the explicit handoff determines the agent target. Herdr also protects and overwrites its own plugin context, binary, and socket environment keys. The plugin-owned handoff preserves the original values without attempting to overwrite those protected keys. [Public host implementation at v0.9.3](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/api/plugins/panes.rs#L12)

Inline handoffs are limited to 48 KiB so they fit Linux's per-argument/environment limits. The workspace sync plan travels in the same handoff. Larger snapshots first drop metadata unused by the dialogs and identity checks, preserving exact names, pane labels, location/session data, and observed process executable fields. If the remaining payload is still large, a short environment pointer identifies a temporary file in `HERDR_PLUGIN_STATE_DIR`. The file is limited to 4 MiB, has a random validated filename, and must be a regular file owned by the current user with mode 0600 and one link. Decode checks the expected state directory, refuses a symlink, validates the bounded payload, then consumes and unlinks the file. Popup-launch failures trigger best-effort cleanup. Missing state storage or an excessive payload produces an English error instead of an oversized process launch.

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

For synchronization, each row is rechecked immediately before its write: it must still be in the frozen workspace and at the same location, its terminal and agent observations must be unchanged, and its explicit pane label and agent name must match the values shown in the preview. The plugin performs a fresh read after every rename or clear and confirms the result before reporting success.

Strict replacement detection remains incomplete. Public process observations contain no process start-time/generation token. PID reuse with identical executable data can remain indistinguishable. A replacement that preserves the terminal, kind, and session reference can evade detection when process evidence is absent, incomplete, or shares a surviving process. Public `agent_session` references may be absent or reused during resumed sessions. A terminal ID alone cannot prove that the same agent still occupies it.

The `AgentRenameParams` schema exposes `target` and optional `name` without an expected-identity/version precondition. Public v0.9.3 rename resolves the target and writes its current agent name. There is therefore a race between the final read and the rename request, and another observation window before post-write verification. Revalidation is not an atomic compare-and-set. **The strict identity acceptance criterion remains unmet.** A workspace batch repeats this window once per row; it is sequential, not atomic. [Public rename implementation](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/agents.rs#L90)

## Popup geometry, mouse and appearance

Sizes were measured in the isolated 0.9.3 client (R1) and are recorded in [r1-live-findings](r1-live-findings.md#4-popup-sizing) and [r1-source-findings](r1-source-findings.md):

- Manifest `width`/`height` are outer cells including Herdr's border, or percentages of the pane area (the area right of the sidebar, not the client). Values are clamped to a minimum of 6 x 4 and the available area.
- The popup PTY is the outer size minus 3 columns and minus 2 rows (borders plus one reserved gutter column).
- Popups are centered in the pane area; the native overlay is centered over the whole client.

| Surface | Manifest size | PTY seen by the dialog |
| --- | --- | --- |
| Rename popup | 57 x 7 | 54 x 5, the native overlay's content size |
| Sync popup | `"80%"` x `"70%"` | 56 x 19 at a 100 x 32 client, 104 x 32 at 160 x 50 |

The rename dialog needs at least 40 x 5 and the sync dialog at least 40 x 10 PTY cells; below that each shows a resize message and Esc cancels. Both re-layout on resize without losing input or selection.

The native renderer draws a square-corner accent border, `panel_bg` content, a `surface0` input, and the buttons ` ↵ save `, ` ^c clear `, ` esc cancel `. The plugin reproduces rows, labels and colors inside Herdr's popup frame. Remaining differences: Herdr's own popup border and title surround the content, popups are centered over the pane area, the plugin adds an error/status row, and its title reads `rename agent`.

Mouse: Herdr forwards mouse reports to a popup only when the popup enables mouse reporting itself, with coordinates relative to the popup PTY. Both dialogs enable it. Clicks outside the popup are not delivered. Variants other than left press/release and wheel were not tested. Native rename activates on press and cancels on any click outside save and clear; the plugin matches this.

Theme: Herdr's `auto_switch = true` depends on the host terminal appearance, which a plugin cannot read, so dark is assumed. Only the default (catppuccin) and Dracula palettes were compared against captures.

Closing the last pane in a popup's original tab can make Herdr close the popup itself. The plugin cannot retain an error dialog after its host terminal is removed. The isolated test observed this host closure without renaming another agent. Herdr also closes a popup when the workspace focus changes.

The headless test client draws no notification toast, so notification delivery and wording are unverified. The implementation sends the notification through `notification show`; Herdr may rate-limit or suppress it.
