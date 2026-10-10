# Herdr Agent Rename Plugin — Development Plan

Status: Implementation available. Automated tests and core isolated Herdr 0.9.3 UI checks pass. The full release gate remains incomplete because strict atomic identity protection cannot be established through the public API. See [verification results](docs/verification-results.md).

## 1. Goal and agreed behavior

Build an independent Linux plugin for Herdr that provides two commands:

| Shortcut | Action | Behavior |
| --- | --- | --- |
| `prefix+shift+a` | `local.agent-rename.open` | Open a small dialog to edit the current agent name. |
| `prefix+shift+s` | `local.agent-rename.sync` | Open a Yes/No dialog, then copy the current pane name to the current agent name after confirmation. |

Synchronization is one-time and runs from **pane name to agent name**. It does not monitor later changes or rename the pane.

All plugin-facing text must be English: titles, field labels, buttons, descriptions, notifications, validation messages, errors, and installation/use documentation.

Use official plugin entrypoints and public CLI/API only. Do not modify Herdr source or installed binaries. Do not add a resident process or server.

## 2. Scope

Included:

- Independent plugin manifest, executable launcher, implementation, and dependencies.
- Both shortcuts and conflict-aware setup instructions.
- Invocation-time target capture and target revalidation before writes.
- Agent name editing and pane-to-agent name synchronization with confirmation.
- Small terminal popup UI modeled on Herdr's native `rename_pane` dialog.
- Input validation, explicit cancellation, errors, and user-initiated retries.
- Automated tests, actual Herdr UI checks, comparison captures, and verification results.
- Installation, use, removal, compatibility, and update verification documentation.

Excluded:

- Pane, tab, or workspace renaming.
- Agent start, stop, restart, prompts, or terminal input injection.
- Batch renaming, generated names, continuous synchronization, or reverse synchronization.
- Windows support, custom Herdr binaries, and `--clear` behavior.

## 3. Baseline and evidence

The workspace was empty apart from Git metadata during planning. The installed executable reports `herdr 0.9.3`; Python is available.

Use Linux and Herdr 0.9.3 as the initial support baseline. Declare `min_herdr_version = "0.9.3"` and `platforms = ["linux"]`. Newer versions are not automatically considered verified.

Planning-time inspection of `herdr api schema --json` found:

- Invocation context: `focused_pane_id`, `workspace_id`, and `tab_id`.
- Agent information: `name`, `pane_id`, `terminal_id`, and optional `agent_session`.
- Popup support: `popup` placement and explicit `width`/`height`.
- Agent rename parameters: `target` and `name`; no expected-identity precondition was exposed.

The installed `plugin pane open --placement` help omits `popup`, although the public schema includes it. First verify manifest-declared popup placement without a CLI placement override. Use the public socket API if necessary; do not assume undocumented CLI flags work.

These findings establish a starting point, not proof of runtime behavior or agent identity guarantees.

References:

- [Official plugin documentation](https://herdr.dev/docs/plugins/)
- [Versioned 0.9.3 plugin documentation](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/plugins.mdx)
- [Agent identity and automation](https://herdr.dev/docs/agent-automation/#agent-identity-and-launch)
- [Public CLI reference](https://herdr.dev/docs/cli-reference/)
- [Public socket API and schema](https://herdr.dev/docs/socket-api/)
- [prompt_toolkit documentation](https://python-prompt-toolkit.readthedocs.io/en/stable/)

## 4. Implementation approach

Use Python with `prompt_toolkit` as the initial implementation choice. Build a custom layout with an input buffer, labels, buttons, and a persistent error area. Do not implement editing with shell `read`, or use a stock dialog layout without checking its fit inside the Herdr popup.

Keep dependencies pinned in a project-local virtual environment. Use a stable executable launcher that works from both local links and installed checkouts. Document prerequisites and dependency setup; local linking must not assume install-time build hooks have run.

Proposed structure:

```text
herdr-rename-agent/
├── PLAN.md
├── herdr-plugin.toml
├── pyproject.toml
├── dependency lock file
├── bin/agent-rename
├── src/herdr_agent_rename/
│   ├── action.py
│   ├── context.py
│   ├── client.py
│   ├── controller.py
│   ├── validation.py
│   ├── sync.py
│   └── ui.py
├── tests/
│   └── fixtures/
├── README.md
└── docs/
    ├── compatibility.md
    ├── verification.md
    ├── verification-results.md
    └── captures/
```

Use plugin ID `local.agent-rename`, local action IDs `open` and `sync`, and declared popup entrypoints for the edit and confirmation dialogs. Share context parsing, target checks, validation, transport, and UI components between actions.

## 5. Target capture and public API contract

At action invocation, parse `HERDR_PLUGIN_CONTEXT_JSON` structurally and freeze the original `focused_pane_id` and available workspace/tab identifiers. Preserve the invocation's server/socket context.

Do not depend on `HERDR_PANE_ID` inside a popup, query whichever pane is currently focused later, or select a replacement target when context is missing. Pass the frozen target and initial target snapshot explicitly to the popup through supported environment fields or equivalent public parameters. Verify propagation in Herdr 0.9.3.

Use these public operations:

```text
herdr agent get <target-pane-id>
herdr pane get <target-pane-id>
herdr agent rename <target-pane-id> <new-name>
```

Prefer the executable identified by `HERDR_BIN_PATH`; otherwise resolve `herdr` from PATH. Preserve the supplied connection environment. Pass arguments as argv arrays, never interpolate input into shell commands, and prevent child CLI processes from consuming dialog input.

Parse response envelopes and error JSON using fixtures obtained from the installed version. Do not infer names from terminal output, sidebar text, titles, or process labels.

For synchronization, the source is the pane's explicit `label`. A missing or empty label is an error; terminal title and other display fields are not substitutes. For editing, populate the agent's `name`; an unnamed agent receives an empty input with explanatory text.

### Identity and movement checks

Capture available identity information at initial lookup and compare it before applying. Investigate the semantics and availability of `agent_session` and public process information. A `terminal_id` identifies a terminal and alone cannot establish that its agent occupant is unchanged.

Detect target exit, replacement, and movement to the extent supported by public data. Compare canonical pane/workspace/tab information because old pane IDs may remain aliases after movement. A status or revision change alone must not count as agent replacement.

If required identity data is unavailable, do not silently claim equivalent protection. Record which checks can be performed and which replacement cases remain indistinguishable. Treat incomplete coverage as a release limitation and unmet strict identity acceptance criterion.

There is a remaining race between revalidation and `agent rename` unless a verified public atomic identity precondition exists. Document this limitation explicitly; do not claim revalidation makes the rename atomic.

## 6. Rename dialog

Flow:

1. Capture the original target and retrieve the agent.
2. Open a small popup with the current name and input focus.
3. Let the user edit or cancel.
4. On Apply, validate, lock duplicate submissions, and revalidate the target.
5. If the proposed name equals the freshly queried name, close successfully without a write.
6. Otherwise invoke rename once and close only after verified success.
7. On failure, retain the dialog, input, and an English error message.

Title: `Rename agent`. Field label: `Name`. Buttons: `Apply` and `Cancel`.

| Input | Behavior |
| --- | --- |
| Printable text | Insert at the cursor. |
| Left / Right | Move the cursor. |
| Home / End | Move to the beginning / end. |
| Backspace / Delete | Delete before / after the cursor. |
| Tab | Move input → Apply → Cancel. |
| Shift+Tab | Move focus in reverse order. |
| Enter | Apply from the input; activate the focused button otherwise. |
| Esc / Ctrl+C | Cancel without a rename request, before submission. |

Long input must scroll horizontally. Preserve invalid input for correction, including pasted unsupported characters. Pasted newlines must not trigger submission or be silently removed to create a different valid name.

## 7. Synchronization confirmation dialog

`prefix+shift+s` opens a confirmation dialog before any rename request:

```text
┌─ Sync agent name ──────────────────────┐
│                                       │
│ The pane name will be used as the      │
│ agent name.                            │
│                                       │
│ Current agent name: reviewer           │
│ New agent name:     backend-api        │
│                                       │
│                    [ Yes ]  [ No ]     │
└───────────────────────────────────────┘
```

This is a content example; final geometry and styling come from baseline UI observation.

- Display the initial agent name and exact pane label proposed for copying. Use `(unnamed)` only as a display placeholder for an absent agent name.
- Default focus to `No`.
- Tab and Shift+Tab move between Yes and No; Enter activates the focused button.
- No, Esc, and Ctrl+C close without changes before submission.
- Yes validates the proposed name and revalidates the pane label and target agent.
- If the pane label changes while the dialog is open, stop and require reopening. Do not apply a different name from the one confirmed.
- If the agent's name changes while awaiting confirmation, also stop and require reopening rather than overwrite a newly changed name.
- If either target identity or location changes, stop and display an error.
- If both names already match, confirmation completes without a write.
- On success, close and show `Agent name synced.` through a supported notification mechanism.
- On failure, keep the dialog open with an English error. Allow explicit retry for retryable failures after fresh checks; no automatic retry.
- For missing names, invalid labels, or missing agents, show an explanatory state with confirmation disabled and a working cancellation path.

Synchronization does not offer an editor or automatically transform the pane label. Users may fix the pane name separately or use the rename action.

## 8. Validation, errors, and submission state

Require a full match of:

```regex
^[a-z][a-z0-9_-]{0,31}$
```

Accept only 1–32 ASCII characters, starting with a lowercase letter. Do not trim, lowercase, truncate, replace characters, or clear a name. The server remains authoritative for uniqueness.

Use an explicit controller state model: loading, editing/confirming, validating, submitting, error, and closed. Disable further affirmative submissions while a request is in flight.

Initial English copy:

| Situation | Message |
| --- | --- |
| Empty entered name | `Enter an agent name.` |
| Invalid entered name | `Use 1–32 characters: a lowercase letter first, then lowercase letters, digits, underscores, or hyphens.` |
| Missing pane label | `This pane has no name.` |
| Invalid pane label | `The pane name is not a valid agent name.` |
| Duplicate name | `This agent name is already in use.` |
| No live agent | `No running agent was found in this pane.` |
| Missing pane | `The target pane no longer exists.` |
| Changed agent | `The target agent has changed. Reopen the dialog.` |
| Changed agent name | `The agent name has changed. Reopen the dialog.` |
| Moved target | `The target pane has moved. Reopen the dialog.` |
| Changed source label | `The pane name has changed. Reopen the dialog.` |
| Missing invocation context | `The original pane could not be identified.` |
| Connection failure | `Could not connect to Herdr.` |
| Unsupported version/API | `This Herdr version is not supported.` |
| Generic rename failure | `Could not rename the agent. Try again or cancel.` |
| Uncertain write result | `The rename result could not be confirmed. Check the current name before retrying.` |
| Insufficient space | `Enlarge the terminal to continue. Press Esc to cancel.` |

Bound request duration, sanitize control characters from displayed diagnostics, and retain useful server error details. Never automatically repeat a failed write.

A timeout or disconnect after submission may leave the result uncertain. Perform a fresh lookup before a user-initiated retry; an already-applied name must not produce a second write. After submission, cancellation cannot promise to undo a rename. Show a submitting state and complete or reconcile the request before returning to an actionable state.

## 9. UI and terminal behavior

- Measure the native `rename_pane` dialog before choosing final popup cell dimensions.
- Set explicit small popup dimensions; do not use the default half-screen size.
- Account for Herdr's outer border, popup minimum size, and actual inner terminal area.
- Avoid redundant internal frames and keep focus/button states clear.
- Reserve an error area so errors preserve entered text and usable controls.
- On resize, recompute layout and preserve input/focus where possible.
- If the available area is insufficient, disable affirmative actions and show the resize/cancel view; never operate clipped buttons.
- On normal exit, cancellation, and exceptions, restore terminal mode, cursor state, and any enabled paste handling.
- Verify Herdr restores the original pane focus and terminal behavior after the popup closes.
- Keep the running agent's input and work untouched.

## 10. Installation, bindings, and removal

Bind keys to manifest actions with matching IDs:

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

Document dependency setup, `herdr plugin link /path/to/plugin`, action discovery, and the official config reload mechanism verified on 0.9.3. Installation must not require a server restart or agent termination.

If a setup helper edits config, preserve formatting/comments and unrelated settings and change only the required binding entries. Save only displaced binding tables for scoped restoration; uninstall must never overwrite the current config with a full-file backup, because users may make later edits. Detect equivalent shortcut spellings using Herdr's key interpretation. Existing matching bindings are a no-op; unrelated conflicting bindings are reported without adding duplicates or silently overwriting them.

Document migration from an existing shell popup binding. Removal must delete only bindings owned by this plugin and unregister it using the appropriate unlink/uninstall command. Preserve user source files and unrelated configuration.

Provide one-command `bin/install`/`bin/uninstall` helpers and an agent-readable `INSTALL.md`. Restore a displaced binding only where its shortcut remains free; preserve newer user bindings and retain blocked scoped recovery records.

Check the minimum version in the manifest and direct launcher path. Also verify required capabilities; a version string alone is not sufficient evidence of API support.

## 11. Milestones and gates

| Milestone | Work | Exit criterion | Estimate |
| --- | --- | --- | --- |
| M1: Baseline and API contract | Observe native UI; capture context/response fixtures; investigate identity and popup behavior. | Baseline capture, verified transport path, and documented identity limitations. | 1 day |
| M2: Plugin foundation | Manifest, launcher, dependency setup, action context, popup opening. | Both actions open on the frozen target without affecting the agent. | 0.5–1 day |
| M3: Rename behavior | Client, validation, target checks, submission state, error/retry handling. | Meaningful automated behavior tests pass. | 1 day |
| M4: Edit and confirmation UI | Keyboard editing, Yes/No flow, English text, resizing, paste, terminal recovery. | Automated TUI checks pass and both dialogs fit the baseline surface. | 1–2 days |
| M5: Actual Herdr verification | Dedicated test pane/agent; identity scenarios; real keybindings; screenshot comparison. | Actual UI results and specific remaining differences recorded. | 1–2 days |
| M6: Packaging and docs | Installation/removal checks, conflicts, upgrade checklist, limitations. | Reviewable release artifacts and verification report. | 0.5–1 day |

Initial total estimate: 5–8 working days for one developer. Reassess after M1 if public identity data or popup behavior prevents a requirement from being met.

## 12. Verification plan

### Automated behavior tests

- Invocation-time target is preserved despite later focus changes; malformed or missing context never falls back to another pane.
- A valid Apply or confirmed Yes sends exactly one rename request.
- Cancel, No, Esc, and Ctrl+C before submission send zero rename requests.
- Empty/invalid names and unnamed/invalid pane labels send zero requests.
- Same-name application revalidates the target but sends zero writes.
- Duplicate-name and CLI failures retain input or confirmation details and allow cancellation.
- Missing, exited, replaced, or moved targets never redirect to another agent.
- Source label or agent-name changes invalidate synchronization confirmation.
- User input is passed as literal argv; shell metacharacters cannot execute commands.
- Unknown responses, unsupported capabilities, connection failures, and uncertain results are handled without automatic writes.
- Repeated affirmative keys during submission cannot duplicate writes.

### Automated TUI and terminal tests

- Cursor editing, horizontal scrolling, focus order, Enter, Esc, and Ctrl+C.
- Paste boundaries, embedded newlines, invalid Unicode input, and control characters.
- Resize and tiny-screen cancellation without clipped affirmative controls.
- Terminal recovery after success, cancellation, and exceptions.
- English copy across both dialogs, errors, notifications, and binding descriptions.

### Actual Herdr UI checks

Use a separate test pane and test agent. Do not rename the user's active development agent for testing.

- Both shortcuts open small dialogs; the sync dialog does not write before Yes.
- Existing agent name and exact source pane label are displayed correctly.
- Editing, paste, keyboard focus, cancellation, errors, and explicit retries work.
- Rename changes only the intended agent name.
- Synchronization makes the agent name equal to the pane label and leaves the pane label unchanged.
- Popup closure restores focus and terminal behavior while the agent's work/input remain intact.
- Resize and small-screen behavior remain usable.
- Compare native `rename_pane`, plugin edit, and sync confirmation captures at identical terminal dimensions/theme.

Record Herdr version, terminal details, dimensions, theme, scenario, result, and evidence paths. Report remaining differences in size, color, spacing, borders, and operation explicitly.

Config parsing or mocked CLI tests do not complete actual UI verification. If actual UI access is unavailable, mark those checks **Unverified** and leave the release gate incomplete.

## 13. Upgrade checks and deliverables

Document these read-only checks after a Herdr update:

```bash
herdr --version
herdr api schema --json
herdr plugin list
herdr plugin action list --plugin local.agent-rename
herdr plugin log list --plugin local.agent-rename
```

Then run the automated suite and actual UI checklist on a dedicated test agent. Record each tested version; do not promise compatibility with all future releases.

Final deliverables:

1. Independent plugin manifest, launcher, and implementation for both actions.
2. Pinned dependencies and reproducible setup instructions.
3. English installation, shortcut, use, conflict, removal, and upgrade documentation.
4. Test code, schema/response fixtures, and verification results.
5. Native/edit/sync UI captures and comparison notes.
6. Supported versions, public-identity coverage, remaining race conditions, and known limitations.

The release is complete only when required automated checks and actual UI checks pass, installation/removal preserve unrelated configuration, and evidence plus limitations are documented. Any strict requirement that cannot be satisfied through the public API must remain explicitly unmet rather than be counted as complete.

## 14. Implementation checkpoint — 2026-10-10

- M1–M4: Implemented, with installed API fixtures, native/edit/sync captures, target checks, shared controller, custom prompt_toolkit layouts and automated validation.
- M5: The initial isolated actual Herdr 0.9.3 run passed 35 checks across 23 distinct scenarios. The expanded Linux bubblewrap run passes 42 checks across 30 distinct scenarios, including one-command installation/removal with intervening user config changes, plus 8 isolation checks. Additional fault/replacement/working-agent scenarios remain unverified.
- M6: Manifest, stable executable launchers, hash-pinned setup/test locks, one-command scoped install/uninstall, agent-readable INSTALL.md, English installation/removal/upgrade documentation and a reproducible isolated verification tool are present.
- Automated suite: 225 tests pass, including 41 installation checks; source/tool compilation passes.
- Final popup sizes: edit 56×11 outer (53×9 inner), sync 60×14 outer (57×12 inner). Native rename baseline: 56×7. Differences and evidence are recorded in docs/verification-results.md.
- Strict immutable/atomic identity acceptance remains unmet through the verified public 0.9.3 API. The full release gate is not marked complete.
