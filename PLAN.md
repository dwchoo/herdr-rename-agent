# Herdr Agent Rename — Python Revision Plan

Status: implemented and verified, 2026-10-11 (HEAD `f46565a`), in the disposable sandbox only. Not deployed to the user's live Herdr environment: the installed plugin there is still the `v0.1.0` runtime (`d5cd79f`) until the user authorizes P4's last step. Results: [docs/verification-results.md](docs/verification-results.md). This document replaced the earlier Rust rewrite plan; see §12 for why Python was chosen. Sections 1 and 3-8 are the original plan; where R1 changed it, the text below is updated and §2 and §13 record the differences.

## 1. Objective and decisions

Keep the existing Python plugin and revise it to:

1. Fix the reported sync Yes failure.
2. Synchronize agent names across every pane in the invoking workspace, skipping panes without an explicit name.
3. Make the rename dialog behave and look as close to Herdr's rename overlay as the public plugin boundary permits. (R1: Herdr 0.9.3 has no native agent rename overlay; the pane rename overlay is the reference.)
4. Stay a personal plugin, installed from a local checkout. No public release or Herdr plugin registry distribution.

| Shortcut | Action ID | Planned behavior |
| --- | --- | --- |
| `prefix+shift+a` | `local.agent-rename.open` | Agent name editor styled after Herdr's rename overlay, with save, clear, and cancel. |
| `prefix+shift+s` | `local.agent-rename.sync` | Confirm one batch that copies explicit pane labels into agent names across all panes in the current workspace, including other tabs. |

The plugin ID stays `local.agent-rename`, so existing bindings and recovery records remain valid.

Confirmed behavior:

- The runtime stays in Python (3.11+) with prompt_toolkit. No Rust component.
- All product UI and installation/use documentation remain English.
- Herdr source code may be referenced and its logic ported, with attribution. Herdr is a binary-only crate (no `lib.rs`), so nothing can be imported as a module.
- Rename: Enter saves. Ctrl+C clears the input only. Esc cancels. Saving an empty input removes the agent's explicit name through `herdr agent rename <target> --clear`. Clearing the input alone never sends a request.
- Sync scope is the workspace where the action was invoked, frozen before the popup opens. Include every normal pane in every tab of that workspace, whether focused or visible. Exclude other workspaces and plugin popup resources.
- Sync skips each pane with a missing/empty explicit label or no running agent. It preserves that agent's name, never issues a clear, and never substitutes a terminal title or generated display name. If no pane needs a rename, it finishes without confirmation or writes.
- An unnamed agent may receive a valid explicit pane label. An absent pane label is distinct from an absent agent name.
- Sync is a one-time batch of pane-to-agent copies. It never changes pane labels, starts agents, or monitors later updates. One Yes authorizes the previewed batch.
- Use public plugin/CLI/API interfaces only. Do not modify Herdr's source, executable, existing agents, or terminal input. No resident service.
- Personal use only. Install through the existing `bin/install` (local `herdr plugin link`). GitHub, if used, only hosts the source.

### Decisions (confirmed 2026-10-10)

| ID | Decision | Choice |
| --- | --- | --- |
| D5 | Project license. | Apache-2.0, matching the ported Herdr code. Keep attribution in THIRD_PARTY_NOTICES.md. |
| D6 | Mouse support if Herdr 0.9.3 does not forward mouse reports to plugin popups. | Keyboard parity is mandatory. Mouse is supported to the extent Herdr delivers it; any gap is a documented limitation. No private API or terminal hacks. |
| D7 | Whitespace and control characters. Herdr's editor turns pasted CR/LF/Tab into spaces, drops other control characters, and trims leading/trailing whitespace on save. The current plugin never transforms input. | Follow Herdr for CR/LF/Tab→space and trimming on save. Do not silently drop other control characters, because dropping can turn invalid input into a different valid name; show them as visible U+FFFD so validation still rejects them. |

Dropped with the personal-use scope: GitHub-managed install support and key-management plugin actions (former D2), and a public plugin ID with migration (former D4).


## 2. Baseline, Herdr 0.9.3 facts and R1 results

Baseline before this revision (`v0.1.0`, `d5cd79f`): 225 Python tests, 42 sandbox checks across 30 scenarios plus 8 isolation checks. It synchronized only the invoking pane. The reported "Yes does not work" was reproduced in R1 (§5 of `docs/r1-live-findings.md`): No was focused by default; Left/Right scrolled the name text instead of switching buttons; there was no `y` binding; `mouse_support=False` meant Herdr never forwarded clicks. Only Tab then Enter reached Yes. No hidden precondition disabled Yes.

The user's config at planning time: `/home/dwchoo/.config/herdr/config.toml` (theme `dracula`, `auto_switch = false`). Existing config restoration metadata must be retained. Nothing in the user's environment has been changed by this revision.

Herdr 0.9.3 facts (R1 evidence in [docs/r1-source-findings.md](docs/r1-source-findings.md) and [docs/r1-live-findings.md](docs/r1-live-findings.md)):

| Topic | Finding |
| --- | --- |
| `herdr plugin link` | Registers the local checkout. Does not run build commands, so `bin/install` prepares `.venv` itself. |
| Native agent rename overlay | **None exists.** The client overlay has only new workspace/tab and rename workspace/tab/pane targets. Agent rename is API/CLI only. The rename pane overlay (same renderer) is the reference. |
| Rename overlay layout | 56x7 outer, 54x5 content: title row 0, blank, input row 2, buttons row 3, row 4 blank. Buttons ` ↵ save ` (8), ` ^c clear ` (10), ` esc cancel ` (12), 2-cell gaps, centered. Initial text is the current explicit label, cursor at end. No replace-on-type for renames (only new workspace/tab). No error display: errors are not surfaced. |
| Rename overlay input | Enter saves (trimmed). Esc cancels. Ctrl+C and Super+Backspace clear the field only. Save with empty pane input sends a clear; workspace/tab send nothing. No keyboard button focus. |
| Rename overlay mouse | Activation on press, left button only. Save and clear rects act; a click anywhere else, including the cancel button and outside the panel, cancels. |
| Popup sizing | Manifest `width`/`height` are outer cells including the border, or percentages of the pane area (right of the sidebar). PTY = outer - 3 columns and - 2 rows. A 57x7 popup gives the 54x5 content area. Popups are centered in the pane area. Minimum clamp 6x4. |
| Popup input and mouse | A popup receives all terminal input, including Escape. Mouse reports are delivered, translated to popup-relative SGR coordinates, only if the popup enables mouse reporting. Clicks outside the popup are not delivered. |
| `pane list` | `pane list --workspace <ID>` returns every pane of every tab. The `label` key holds the explicit pane name and is absent when unnamed. Entries have no agent name. Plugin popups do not appear. |
| `agent list` / `agent get` | `agent list` is session-wide and only covers agent panes; `name` is absent for an unnamed agent. One agent per pane; join by `pane_id`. `agent get` on a pane without an agent exits 1. |
| Agent names | Rule `[a-z][a-z0-9_-]{0,31}`. Unique across the whole session (all workspaces and tabs). Self-rename is a no-op success. Clearing a name does not free it until the holder is cleared. |
| `herdr agent rename <TARGET> <NAME>\|--clear` | `--clear` removes the explicit name and is idempotent. It fails with `agent_not_found` if no agent is running. Names can also drop silently when the agent changes. |
| Rename API | No expected-identity precondition (no compare-and-set). |
| Theme | Default theme is `catppuccin`. `auto_switch = true` depends on the host terminal appearance, which a plugin cannot read. |
| Startup | Importing the runtime modules takes about 0.13 s per Python process. A shortcut starts two processes (action, dialog). v0.1.0 dialog content appeared in about 0.26 s. |

The official Herdr v0.9.3 source was inspected at commit `7b116c05bfda646af39d2524c54e70c751f57ee8`:

| Source | Relevant behavior |
| --- | --- |
| [`src/client/shell/overlays.rs`](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlays.rs#L630) | Rename overlay renderer; 56x7 outer frame, title/input/button placement, centered button dimensions, styles. |
| [`src/client/shell/text_editor.rs`](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/text_editor.rs) | Editor model ported to `editor.py` (details below). |
| [`src/client/shell/overlay_input.rs`](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlay_input.rs#L891) | Overlay key routing. |
| [`src/client/shell/mouse.rs`](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/mouse.rs#L1684) | Mouse activation of the overlay buttons. |
| [`src/app/state.rs`](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/state.rs) | Palette tokens of the built-in themes. |
| [`src/app/agents.rs`](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/agents.rs#L90) | Agent name validation, uniqueness, and explicit-name removal. |

Herdr's editor, the porting reference:

- Grapheme-cluster cursor movement. Words are runs of the same class: whitespace, alphanumeric/`_`, or other. Backward word movement does not skip whitespace first; forward word movement does.
- Keys: Left/Ctrl+B, Right/Ctrl+F, Home/Ctrl+A, End/Ctrl+E, Alt+B, Alt+F, Backspace/Ctrl+H, Delete/Ctrl+D, Ctrl+U and Ctrl+K (kill to start/end), Ctrl+W, Ctrl/Alt+Backspace, and Alt+D (kill word), Ctrl+Y (yank). There is a single local kill buffer, not the system clipboard.
- Insert/paste: CRLF/CR/LF/Tab become one space and other control characters are dropped. The replace-on-type mode (first insert replaces the whole text) exists but no rename overlay uses it for a prefilled value.
- Viewport: at most `width - 1` cells precede the cursor; the remaining cells show the text after it. Width comes from `unicode-width`.

## 3. Architecture

Keep the existing package layout and extend it. Do not add a second runtime.

```text
herdr-rename-agent/
├── herdr-plugin.toml           # actions, popups (sync popup sized by percentage)
├── bin/                        # setup, launcher, install/uninstall
├── src/herdr_agent_rename/
│   ├── action.py               # action/dialog dispatch
│   ├── context.py              # frozen pane or workspace, private handoff
│   ├── client.py               # public CLI calls (+ pane list, agent list, clear)
│   ├── controller.py           # single-target rename/clear with revalidation
│   ├── sync.py                 # pure workspace planner + sequential executor
│   ├── editor.py               # NEW: port of Herdr text_editor.rs semantics
│   ├── theme.py                # NEW: Herdr palette tokens, theme from config
│   ├── ui/                     # prompt_toolkit dialogs (split from ui.py)
│   │   ├── common.py           # terminal guard, buttons, mouse hit areas
│   │   ├── rename.py
│   │   └── sync.py
│   ├── validation.py
│   └── installation.py         # scoped config lifecycle (unchanged)
├── tests/
├── tools/                      # real Herdr / bubblewrap verification
├── LICENSE                     # D5
├── THIRD_PARTY_NOTICES.md      # Herdr attribution for ported logic
├── INSTALL.md
├── README.md
└── docs/
```

- `editor.py` is a pure model with no prompt_toolkit dependency. It holds the text, cursor, kill buffer, and viewport and is unit-tested against cases derived from `text_editor.rs`. It renders through a `FormattedTextControl` with an explicit cursor position. Do not use prompt_toolkit's `Buffer` editing bindings, because their Emacs semantics differ from Herdr's.
- Grapheme segmentation uses a pinned, hash-locked `regex` package (`\X`). Width uses the existing `wcwidth`. Record any observed width differences from `unicode-width`.
- `sync.py` planning is a pure function from a frozen snapshot to rows. The executor is separate from the UI. Rendering reads local state only and never calls the API, processes, or filesystem.
- Keep the action path free of prompt_toolkit imports so popup launch latency stays low.
- Keep launcher paths stable so existing bindings and scoped uninstall records stay valid.
- Mark ported logic with a header naming the upstream file, version `v0.9.3`, commit `7b116c05bfda646af39d2524c54e70c751f57ee8`, and local changes.

## 4. Native UI parity and limits

Rename dialog (reference: Herdr's rename pane overlay, as implemented in `ui/rename.py`):

- Popup 57x7 manifest size gives a 54x5 content area. Title `rename agent` at row 0, input at row 2, centered buttons at row 3, status/error at row 4.
- Button widths: save 8, clear 10, cancel 12 cells, 2-cell gaps, labels ` ↵ save `, ` ^c clear `, ` esc cancel `.
- Panel background, input surface, accent primary button, text, and bold styling come from the active theme's tokens.
- Input starts with the current explicit agent name, cursor at the end. There is no replace-on-type (the native rename overlays do not use it).
- No keyboard button focus, matching native. Enter, Ctrl+C, and Esc act directly. Mouse acts on press: save and clear buttons act, any other cell cancels (D6; delivered because the popup enables mouse reporting).
- Errors keep the entered text and usable controls and are shown on content row 4 (the native overlay shows none).

Sync dialog (custom, built from the same palette and button rendering):

- Popup `width = "80%"`, `height = "70%"` (kept after captures). Below a 40x10 PTY it shows a resize message.
- Scrollable table with tab, pane, current agent name, proposed name, and status. Totals line. Yes/No and hints stay visible regardless of scrolling.
- On narrow terminals, collapse the Tab column first, then the Pane column. A selected row can show full long names.
- During application the status column shows progress. Afterward it shows outcomes, with a single Close button.

```text
 Sync agent names · workspace "api" · 6 panes
 Tab       Pane  Agent          Pane name       Status
 1 main    p3    (unnamed)   →  reviewer        change
 1 main    p4    coder       →  coder           matches
 2 tests   p7    tester      →  test_runner     change
 2 tests   p9    tester2        —               no pane name
 3 docs    p11   writer      →  Writer!         invalid name
 3 docs    p12   —              shell           no agent
 ──────────────────────────────────────────────────────────
 2 change · 1 matches · 3 skipped · 0 conflicts
                     [ Yes ]  [ No ]
 ↑↓ PgUp/PgDn scroll · ←→/Tab select · Y/N · Enter · Esc
```

Theme: read `[theme]` from the active config and map built-in theme names and custom tokens to Herdr's palette (`src/app/state.rs`). Dracula and the default (`catppuccin`) theme were the acceptance cases. `auto_switch = true` depends on terminal state that is not available publicly; the dark variant is assumed.

Public plugin boundary differences already observed in 0.9.3, which no runtime language can remove:

- Herdr draws the popup's outer border and the manifest entrypoint title.
- The popup PTY is the outer size minus 3 columns and 2 rows (reserved gutter), so a 57x7 manifest size is needed for the 54x5 content area.
- Popups center within the pane surface; the native overlay centers within the client surface.

Dimensions were locked from live measurements (57x7 rename popup, `"80%"`/`"70%"` sync popup). Parity acceptance: captures in the same theme and size match in colors, text, and button positions, apart from the listed boundary differences. Checked for the default and Dracula themes by palette-color assertions on the plugin's own captures (T01, T02) and by side-by-side captures of the native `rename pane` overlay and the plugin popup at 100x32 (T03, T04): content size, title/input/button rows, button column offsets, popup background and button colors are equal; border/title, outer size (56x7 vs 57x7) and placement differ as listed above ([results](docs/verification-results.md#native--plugin-parity-t03-t04)).

Terminal cleanup: restore cursor, raw mode, alternate screen, bracketed paste, and mouse modes on success, cancellation, and errors.

## 5. Rename behavior contract

1. Freeze the invoking pane, server context, and initial target observations.
2. Read the explicit agent name and open the editor.
3. Ctrl+C or the clear button clears only the field. Esc or cancel closes without writing.
4. Enter/save applies D7 trimming, then validates a nonempty name, or requests explicit-name removal for an empty field.
5. Before a rename or clear, revalidate the frozen target and canonical location.
6. If the current name already equals the request, or is already absent for a clear, close after revalidation with zero writes.
7. Otherwise send exactly one `agent rename` request and verify the resulting name or absence.
8. On a retryable failure keep the input and show an English diagnostic. Never automatically repeat an uncertain write.

Nonempty names must fully match `[a-z][a-z0-9_-]{0,31}`. Apart from the D7 rules, never transform invalid input into a different valid name.

## 6. Sync behavior contract and Yes fix

### Discovery and confirmation

1. Freeze the invoking workspace ID and server context from invocation metadata. If the workspace ID is absent, resolve it only from the frozen invoking pane's canonical record, never from later focus. If it cannot be established, stop with an explanation and zero writes.
2. Enumerate all normal panes in all tabs of that workspace with verified public list APIs. Finish discovery before any write. Later focus changes do not retarget.
3. Build a frozen plan with canonical pane/tab/workspace IDs, target observations, initial agent names, and exact labels. Never add panes created later or silently replace a disappeared pane.
4. Classify rows as eligible, already matching, no pane name, no agent, invalid label, conflict, or lookup failure.
5. Show one confirmation with the preview and counts. Display `(unnamed)` only for an absent agent name.
6. Start with No selected; Enter on it cancels the batch. Support mouse Yes/No (D6), Left/Right, Tab/Shift+Tab, Y/N, and Enter. Use Up/Down/PageUp/PageDown for list scrolling. Keep selection unambiguous.
7. One Yes authorizes only the eligible rows and exact labels shown. Before Yes, No/Esc/Ctrl+C send zero requests.

### Result feedback without a confirmation

| Situation | Feedback |
| --- | --- |
| No eligible rows and no problems | `notification show`: "All agent names already match their pane names." |
| No eligible rows but conflicts or invalid labels | Popup in information mode: same table, Close only. |
| Workspace cannot be established, unsupported API | Notification plus plugin log. |

### Conflicts and application

- Preflight duplicate desired names within the batch and names held by other live agents, including those in other workspaces. Verify the real uniqueness scope first; the server remains authoritative.
- Mark conflicting rows and skip them; independent rows still proceed. If two panes want the same name, pick no winner. An already-matching row stays a no-op, and no other row may take its name.
- No temporary clears, swaps, cycles, or reordering to free names. A name held at preflight remains a conflict for this batch.
- After Yes, recheck each row immediately before its write: membership, location, target observations, source label, and initial agent name. If anything changed, skip or fail that row. Never use a new label, follow a moved pane, or pick another agent.
- Apply sequentially, at most one request per eligible row, verifying each before the next. Repeated Enter/Y/clicks cannot start a second batch.
- A label that became empty after preview skips its row.
- A definite row failure does not stop independent rows. A connection or capability failure stops the queue; report the rest as not attempted. Uncertain results are recorded without automatic retry.
- Final summary per row: synced, already matching, skipped (reason), failed, uncertain, not attempted. Report partial success honestly and do not roll back.
- Cancel during application stops the remaining queue after the in-flight request is reconciled. Distinguish this from cancel before Yes. A later invocation builds a new plan.

There is no atomic workspace rename in the public API. Do not promise all-or-nothing behavior.

Diagnose the current Yes report first and add a regression for the reproduced route. Test mouse delivery through a real Herdr popup, not only a local click handler.

## 7. Public API and safety parity

Keep the existing safeguards:

- Parse `HERDR_PLUGIN_CONTEXT_JSON` structurally. Rename freezes the pane; sync freezes the workspace and discovered panes. Never fall back to later focus or a popup's `HERDR_PANE_ID`.
- Preserve `HERDR_BIN_PATH`, socket/config selection, and the original connection environment.
- Literal argv, bounded timeouts, and child stdin that cannot consume dialog input.
- Parse versioned envelopes. Require Herdr ≥ 0.9.3 and check needed schema capabilities (add `pane.list`, `agent.list`, and rename `--clear` support). Do not require an exact version, so newer Herdr releases keep working when capabilities match.
- Compare canonical pane/workspace/tab and terminal/session/process observations before writes and after results.
- Reject moved, exited, or replaced targets; never redirect.
- Large-snapshot private-file handoff with bounded size (re-sized for multi-pane snapshots), restrictive permissions, ownership/path checks, no symlink traversal, and cleanup on consumption and launch failure.
- No duplicate writes or concurrent batches from repeated keys or double clicks.
- Timeouts or disconnects after submission are uncertain. An explicit retry reconciles first.

Herdr 0.9.3's rename API has no expected-identity precondition. This race remains a documented limitation.

## 8. Installation and rollback

Installation is unchanged: `bin/install` / `bin/uninstall` from this checkout, using local `herdr plugin link`/`unlink` and the scoped config editor. Plugin and action IDs do not change, so the current bindings and recovery record stay valid.

- Prerequisites: Linux, Python 3.11+, and either `uv` or a working `python3 -m venv`. This host lacks `ensurepip`, so only the `uv` path works here.
- The new `regex` dependency is added to the hash-locked `requirements.lock`. After updating the checkout, `bin/setup` (or `bin/install`) refreshes `.venv`.
- `herdr plugin install` from GitHub and the Herdr plugin registry are out of scope.
- Add LICENSE (Apache-2.0) and THIRD_PARTY_NOTICES.md for the ported Herdr logic.

Rollback: `d5cd79f` is tagged `v0.1.0`. Check out the tag, run `bin/setup` and `bin/install`. Because IDs are unchanged, no binding migration is needed. The sandbox rollback cycle (L05) passed.

Keep the current installation working until the new version passes all gates. Develop and test in the disposable sandbox, and change the user's environment only after the user authorizes deployment.

## 9. Work stages and acceptance gates

| Stage | Work | Status and evidence |
| --- | --- | --- |
| R1: Investigate | Reproduce the Yes failure; mouse delivery; pane/agent list coverage; name uniqueness; replace-on-type, trimming and buttons; percentage popup sizes. | **Done.** [r1-source-findings](docs/r1-source-findings.md), [r1-live-findings](docs/r1-live-findings.md), `tools/probe_r1.py`, fixtures. The native agent rename overlay does not exist, so the pane rename overlay is the reference, taken from source review. The side-by-side capture was made later (T03, T04). |
| P1: Sync | `theme.py`; workspace planner/executor; sync dialog with fixed Yes/No; feedback paths. | **Done.** Planner, conflict and executor unit tests; sandbox S01-S29 (keyboard and mouse Yes/No, Yes regression S11-S17). |
| P2: Rename parity | `editor.py` port; rename dialog rewrite; `--clear`; D7 rules. | **Done.** Editor tests derived from `text_editor.rs`; sandbox R01-R13 and T01-T04 captures. |
| P3: Docs and packaging | `regex` in the dependency lock, LICENSE/notices, README/INSTALL updates. | **Done.** `regex==2026.8.31` locked; LICENSE (Apache-2.0), THIRD_PARTY_NOTICES.md, README, INSTALL, docs; 41 installer tests pass; sandbox lifecycle L01-L08. |
| P4: E2E and deploy | Full sandbox E2E, docs/results, `v0.1.0` rollback tag, then update the user's environment with `bin/install`. | **Partly done.** `next` suite: 53 pass, 0 skip, 0 fail, 0 error (H01/S07/S28/S29 verified through the sandbox CLI shim), unit suite 464 passed; `v0.1.0` tag exists. **Pending:** updating the user's environment (`bin/setup`, `bin/install`, reload), which needs the user's authorization. |

A passing old suite or a rendered test buffer alone does not complete a stage.

## 10. Verification matrix

| Area | Required scenarios |
| --- | --- |
| Editor | Existing/absent name; replace-on-type; char/word/grapheme movement; all kill/yank keys; viewport scrolling; paste with CR/LF/Tab/control characters (D7); Enter save; trim; Ctrl+C clear-only; empty save clears; Esc cancel; mouse save/clear/cancel. |
| Sync selection | One confirmation per workspace; default No → zero writes; Tab+Enter; Left/Right+Enter; Y/N; real mouse Yes/No; scrolling keeps buttons visible; repeated keys/double clicks cannot repeat a batch. |
| Sync scope | Panes across several tabs, including hidden/unfocused; other workspace unchanged; focus changes after invocation; missing workspace context; new panes excluded; moved/closed panes skipped. |
| Sync source | Mixed valid, unnamed, agent-less, invalid, and matching panes; unnamed agent with named pane; no eligible rows (notification); conflict-only (information popup); label/name changes while confirming; label cleared after opening. |
| Batch results | Duplicate desired labels; names held inside/outside the workspace; swaps/cycles skipped; independent success despite failures; connection failure stops queue; uncertain not retried; cancel during application; accurate summary. |
| Target/API | Exited/moved/replaced targets; invalid/unknown envelopes; missing capabilities; newer Herdr version with matching capabilities; read failures; uncertain rename/clear reconciliation. |
| Terminal/UI | Dracula and default theme; compact and small terminals; resize; visible errors; normal/error exit restores terminal modes. |
| Handoff | Inline and large multi-pane snapshot; malformed context; file permissions/ownership; symlink rejection; cleanup. |
| Lifecycle | Update of the installed plugin without duplicate bindings; existing recovery record retained; later user edits survive uninstall; repeated install/uninstall; rollback to `v0.1.0`. |

Testing layers:

- Python unit/behavior tests (existing suite extended).
- prompt_toolkit TUI tests through a real PTY (existing approach).
- Real Herdr PTY E2E in the bubblewrap harness, extended to mouse, `--clear`, and multi-tab sync with another workspace as an unchanged control. Never rename the user's development agent in a test.
- Side-by-side native/plugin captures in the same theme and size (T03, T04, 100x32, default and Dracula; see [results](docs/verification-results.md)).

Historical 225/42/8 counts are baseline only; current results are recorded separately in [docs/verification-results.md](docs/verification-results.md). Matrix scenarios not observable in Herdr 0.9.3 (notification wording) are listed as skips there.

## 11. Deliverables

Revised Python runtime with workspace batch sync and native-style rename; `editor.py` and `theme.py` with Herdr attribution; LICENSE and THIRD_PARTY_NOTICES.md; extended tests; real Herdr keyboard/mouse/clear/cross-tab evidence; updated README.md, INSTALL.md, compatibility notes, verification report, and rollback instructions.

## 12. Decision record: Python instead of Rust

A Rust rewrite was planned earlier the same day and rejected. Its benefits were exact reuse of Herdr's editor code and about 0.26 s faster popup launch. Its costs were a full runtime rewrite and re-verification, release CI with per-architecture binaries and a cargo fallback, and either a mixed Rust/Python codebase or an installer port. The reported problems (Yes failure, workspace sync) do not depend on the language. Python keeps the verified runtime, installer, and test assets. The user then limited the scope to personal use, which removed the public distribution work entirely. Revisit Rust only if editor parity or launch latency proves insufficient in practice.

Rust development tools were installed while the Rust plan was active: rustup in `~/.cargo`/`~/.rustup` with `~/.cargo/env` sourced from `~/.profile` and `~/.bashrc`. They are user-level and kept. The checkout-level Rust artifacts from that setup (`rust-toolchain.toml`, `docs/development.md`, the `target/` line in `.gitignore`, and the Rust note in README.md) were removed on 2026-10-10.

## 13. Implementation assumptions

Decisions made while implementing, where the plan was silent or R1 changed the facts:

- Enter and Space are ignored while a sync batch runs, so a repeated key cannot trigger Stop by accident. Stop, Esc and Ctrl+C request cancellation.
- `y`/`Y` triggers Yes directly in confirm mode, as the plan lists. Keys typed before the popup exists go to the underlying pane, not the popup.
- The sync popup shows a resize message when its PTY is smaller than 40x10 (about a 50-column client at `"80%"`); the rename dialog needs 40x5.
- A transport failure during a write stops the queue unless a follow-up read confirms the write.
- Per-pane lookup anomalies become `LOOKUP_FAILED` rows; only server-wide failures abort the preview.
- `auto_switch` themes assume dark, because the host appearance is not available to a plugin.
- The rename title reads `rename agent`.
- Rename shows errors on content row 4; the native overlay has no error display.
- The keybinding description for sync is now "Sync agent names from pane names". The installer matches bindings by `type` and exact `command`, so existing bindings with the old description are kept as they are.
