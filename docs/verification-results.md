# Verification results

This file has two parts: the current revision (workspace sync and the Herdr-style rename dialog; section dated 2026-10-11) and, below it, the historical v0.1.0 results (2026-10-10), which describe the old single-pane UI and are kept for reference only.

# Current revision (version 0.2.0, base HEAD 654748d), 2026-10-11

Status: implemented and verified in an isolated sandbox. It has **not** been installed into or tested against the user's live Herdr environment. The strict identity criterion remains unmet (below).

## Environment and commands

Recorded 2026-10-11, Asia/Seoul:

| Item | Observed value |
| --- | --- |
| Host | Linux 7.2.9-2-t2-noble, x86_64 |
| Herdr binary and isolated server | 0.9.3, public API protocol 22 |
| Python | 3.14.7; declared prerequisite is 3.11+ |
| Test agents | Idle Codex 0.162.1 processes (`--no-daemon`, test-only no-auth provider), no model prompt submitted |
| Terminal | PTY `xterm-256color`, truecolor; 100 x 32 unless a scenario states another size (40 x 10, 50 x 20, 60 x 16, 80 x 24) |
| Themes | catppuccin (default) and Dracula |
| Sandbox | bubblewrap, separate mount/PID/network namespaces, empty HOME, loopback only, read-only runtimes, disposable checkout; 8 isolation checks recorded in [isolation.json](sandbox-next/isolation.json) |

```text
.venv/bin/python -m pytest -q
464 passed

.venv/bin/python tools/verify_sandbox.py --run --suite next
104 pass, 0 fail, 0 error, 0 skip (104 scenarios); exit code 0
```

Every row and scenario phrase of the PLAN section 10 matrix is mapped to the sandbox scenarios that verify it in [verification-coverage.md](verification-coverage.md); the few items that cannot be exercised against a real Herdr, with reasons, are listed at its end.

Raw data: [actual-results.json](sandbox-next/actual-results.json), [run.json](sandbox-next/run.json), [isolation.json](sandbox-next/isolation.json), captures in [sandbox-next/captures/](sandbox-next/captures/) (TXT, key states also PNG). The R1 investigation that fixed the target behavior is in [r1-source-findings](r1-source-findings.md) and [r1-live-findings](r1-live-findings.md) (`tools/probe_r1.py --run`).

The unit suite covers the editor port, theme resolution, workspace planner and executor, rename controller (trim, `--clear`, reconciliation), client capability checks, large-handoff files (4 MiB bound), installer lifecycle (41 tests) and prompt_toolkit dialogs driven through pseudo-terminals.

## Scenario summary by series

| Series | Count | Result | What it covers |
| --- | --- | --- | --- |
| S (sync) | 27 | 27 pass | S01-S06: scope across tabs with another workspace unchanged, mixed rows, unnamed agent, duplicate labels, name held in another workspace, conflict-only information popup. S07: no eligible rows, no popup, no writes. S10-S22: default No, Tab/Left/Right/Y/N/Esc/Ctrl+C, mouse Yes/No, repeated Enter/Y and double click, completion table with Close, focus change, 60 x 16 client. S23-S25: 80-column layout, 50-column resize message, resize while open. S26-S27: stale previews (changed label, closed pane, renamed agent, cleared label). S28-S29: Stop during a batch and Stop latched with Yes, made deterministic with the CLI shim's rename delay (see below). |
| R (rename) | 13 | 13 pass | Save, trim, Ctrl+C clear-only, empty save runs `--clear`, Esc, mouse save/clear/cancel, click elsewhere cancels, invalid paste, name held by another agent, 40 x 10 terminal, resize while open. |
| T (theme) | 4 | 4 pass | T01-T02: default and Dracula captures with palette-color checks. T03-T04: side-by-side parity of Herdr's native `rename pane` overlay and the plugin rename popup (default and Dracula, 100 x 32); see the next section. |
| L (lifecycle) | 9 | 9 pass | Dry-run, fresh install with legacy migration, shortcuts open the new dialogs, repeated install, rollback cycle with `v0.1.0` (new, v0.1.0, new), in-place update (L09: a `v0.1.0` tree replaced by the current code, `bin/install` again: same bindings, config and recovery record, new dialogs), later user edits survive uninstall, repeated uninstall, retained recovery record. |
| H (harness) | 1 | 1 pass | Self-test of the test-only CLI shim (logging, transparency, rename-only delay). |
| F (faults) | 17 | 17 pass | Shim fault injection against the real server. F01-F03: connection lost between rows, write lost in transit with the connection down or up (failed/uncertain row with an English reason, later rows not attempted, no extra writes, Close works). F04: a definite row failure does not stop the others. F05-F06: write applied but the response lost or timed out: reconciled by a read, one request per row. F07-F09: the same for the rename dialog and for `--clear`, and a refusal when the name changed in between. F10-F13: malformed pane list, unknown or incomplete envelopes, malformed lookups (information popup), rename dialog with malformed lookups. F14: per-pane read failures. F15-F17: missing capabilities, version 0.9.2, version string `0.9.4 (abc123)`. |
| X (targets, handoff, context, terminal) | 27 | 27 pass | X01-X04: rename dialog target closed, moved, exited, replaced between open and Enter. X05-X07: the same for a sync preview. X08-X09: swap and 4-cycle. X10: a pane arriving after the preview. X11: scrolling in a short client. X13-X14: large sync plan and rename snapshot through the private file. X15: above 4 MiB. X16-X23: tampered handoffs (mode 0644, symlink, garbage, truncation, second hard link, wrong workspace, pointer to another directory, path traversal). X24: failed popup launch cleans up. X25-X26: invocation context (workspace-only, pane-only, missing/malformed, frozen against later focus). X27-X28: terminal modes restored by the dialog processes (success, cancel, error screen, injected failures). |
| E (editor) | 6 | 6 pass | The editor inside the real popup compared with the ported editor model after every key: movement and no replace-on-type (E01), all kill/yank keys (E02), graphemes (E03), viewport scrolling (E04), paste normalization (E05), absent name (E06). |

Skips: none. The F series and most of the X series, and four older scenarios, rely on the test-only CLI shim (`tools/herdr_shim.py`, sandbox only; mechanism and fault rules in [verification.md](verification.md#next-suite-current-revision-plan-section-10-scenarios)). The four older uses:

- **H01**: the shim self-check. The plugin's popup open produces logged CLI calls, the shim output equals the real binary's, and the 400 ms delay applies only to `agent rename`.
- **S07**: Herdr 0.9.3's headless client draws no toast, so the notification is checked in the shim log: exactly one `notification show` with "All agent names already match their pane names.", no `plugin pane open`, no `agent rename`, and no observable write.
- **S28**: with a 400 ms rename delay on a 7-row batch, Ctrl+C is sent once the second `agent rename` is logged; the log shows exactly two renames (none after Stop), two names synced, five rows "not attempted", summary shows "Canceled".
- **S29**: Ctrl+C written together with Yes; zero `agent rename` calls in the log, all seven rows "not attempted".

Repeated isolated runs after the change (`--only S28` / `--only S29`, scratch `--output`): S28 5/5 pass, S29 5/5 pass. The earlier S29 intermittent failure and S28's delay search no longer exist.

The 51 scenarios added for the coverage matrix (F01-F17, X01-X11, X13-X28, E01-E06, L09) each passed in three separate isolated sandbox runs (`--only F,X,E,L09`, scratch `--output`: 51/51, 51/51, 51/51), before the full run above. No runtime (`src/`) bug was found by them; the changes are in `tools/` (shim, lab, scenarios) and docs.

Hardening of the harness found while adding them: `Lab.reset` and scenario S21 moved focus with `pane focus --pane`, which Herdr 0.9.3 does not accept without a direction, so focus silently stayed where an earlier scenario left it; both now use `agent focus`, and S21 asserts that the focus really moved to pane b before confirming.

## Native / plugin parity (T03, T04)

Both dialogs were opened in the same session at 100 x 32 for the default and Dracula themes: the native overlay with Ctrl+B Shift+P (its default `rename pane` binding) and the plugin popup with Ctrl+B Shift+A. Captures: `native-pane-rename-<theme>` and `plugin-rename-<theme>` (TXT and PNG) plus `parity-<theme>.json` with the measured points, in [sandbox-next/captures/](sandbox-next/captures/). Compared relative to the content area (inside Herdr's border):

| Point | Native | Plugin | Result |
| --- | --- | --- | --- |
| Content area | 54 x 5 | 54 x 5 | same |
| Title row / input row / button row | 0 / 2 / 3 | 0 / 2 / 3 | same |
| Column offsets of ` ↵ save `, ` ^c clear `, ` esc cancel ` | 10, 20, 32 | 10, 20, 32 | same |
| Popup background | panel_bg (`181825` catppuccin, `282a36` Dracula) | same | same |
| Save button background / foreground | accent (`89b4fa`, `bd93f9`) | same | same |
| Clear and cancel button background / foreground | same pair in both | same pair | same |

Remaining differences, not hidden by the checks:

- Border and title: Herdr draws the plugin popup's border with the manifest title (`Rename agent`) in the top border; the native overlay has no title in its border. The plugin also draws its own first content row, `rename agent`, whereas the native row reads `rename pane`.
- Outer size: native box 56 x 7; plugin frame 57 x 7 (Herdr reserves a gutter column on the right).
- Placement: the native overlay is centered on the client surface (x 22, y 12 at 100 x 32); the plugin popup is centered on the pane surface (x 34, y 13).
- Input text: the plugin starts with the agent name, the native overlay with the pane label (the fixtures differ by design); the plugin has a status/error row 4 which is blank here.

## Limitations and unverified items

- **Strict identity criterion: unmet.** Herdr's `agent.rename` has no expected-identity or version precondition, so a race between the final read and each write remains, repeated per row in a batch. Revalidation is not a compare-and-set. See [compatibility](compatibility.md#identity-coverage).
- Notification delivery to the screen is unobserved (the headless client draws no toast); the notification text is verified only as the argument of the plugin's `notification show` call in the shim log (S07).
- The in-progress screen of a running batch was not captured. S28/S29 verify Stop through outcomes and the shim call log (later rows not attempted, in-flight row finished, no rename call after Stop).
- Repeated-batch checks compare agent `revision` deltas rather than counting API writes (counted precisely only in unit tests).
- Mouse works only where Herdr forwards it to the popup (when the popup enables mouse reporting). Only left press and wheel were exercised; right/middle/drag/motion and border clicks were not.
- Herdr closes a popup when the workspace focus changes; S21 is written to skip in that case (it passed, so the popup survived).
- Only the default and Dracula themes, 100 x 32 plus the listed sizes, and one outer terminal were captured. Native/plugin parity (T03/T04) was measured at 100 x 32 only. `auto_switch = true` assumes dark and was not exercised.
- Idle Codex agents only; behavior with working agents or other agent kinds, and `--clear` on real Claude agents, is unverified.
- Faults are injected between the plugin and the real server by the test-only shim (the server is not stopped, no real newer Herdr exists, the real server never emits malformed envelopes). A timeout or disconnect after a mutation, uncertain `--clear`, unknown envelopes, missing capabilities and large/tampered handoffs are therefore verified in the sandbox (F, X series) but as shim-simulated transport behavior. The private handoff file's owner check cannot be forged without privileges and remains unit-tested. See [verification-coverage.md](verification-coverage.md#items-not-exercisable-against-a-real-herdr).
- Popup-open latency was measured only for the v0.1.0 runtime (about 0.26 s to a drawn dialog, [r1-live-findings](r1-live-findings.md#6-popup-open-latency-current-plugin)); no figure is recorded for the current runtime.
- Python 3.14.7 only. The `python3 -m venv` fallback could not run on this host (no `ensurepip`); the `uv` path was used. Newer Herdr versions need their own checks.
- Deployment to the user's live environment (`bin/setup`, `bin/install`, config reload) is pending the user's authorization; no live agent was renamed or touched.

---

# Historical: v0.1.0 results (2026-10-10, single-pane runtime at `d5cd79f`)

The rest of this file records the previous release. Its commands (`tools/verify_herdr.py`, `verify_sandbox.py --run` with the default old suite) and UI descriptions (Apply/Cancel, per-pane sync, no clear action) do not describe the current dialogs.

## Environment and commands (v0.1.0)

Recorded on 2026-10-10, Asia/Seoul:

| Item | Observed value |
| --- | --- |
| Host | Linux 7.2.9-2-t2-noble, x86_64 |
| Herdr binary and isolated server | 0.9.3, public API protocol 22 |
| Python | 3.14.7; declared prerequisite is 3.11+ |
| Dedicated test agents | Two idle Codex 0.162.1 processes, no model prompt submitted |
| Terminal | Controlling PTY, `TERM=xterm-256color`, truecolor |
| Comparison size/theme | 100 × 32 cells, default Catppuccin |
| Small-screen check | 38 × 10 cells |
| Capture format | Actual Herdr client VT100 stream reconstructed into cells, TXT and PNG; no OS compositor capture |

The integration tool used fresh `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `HERDR_CONFIG_PATH`, and a named session. It linked the checkout only in that isolated registry, reloaded configuration, exercised actual Ctrl+B/Shift+A and Ctrl+B/Shift+S inputs, then unlinked and stopped its own session. The normal Herdr registry remains unmodified.

```text
.venv/bin/python -m pytest -q
225 passed in 1.87s

.venv/bin/python -m compileall -q src tools
Passed

Initial session-only run of tools/verify_herdr.py (before installer checks were added)
35 recorded checks passed across 23 distinct scenarios

.venv/bin/python tools/verify_sandbox.py --run
42 recorded real Herdr checks passed across 30 distinct scenarios; 8 sandbox isolation checks passed; exit code 0
```

The core E2E and seven new installation/removal checks were rerun inside Linux bubblewrap with separate mount/PID/network namespaces and a disposable checkout. It used an initially empty HOME, no inherited credentials, no user Codex config or host Herdr sockets, read-only runtime mounts, and loopback-only networking. The two native Codex 0.162.1 processes used `--no-daemon` and a fresh test-only no-auth provider; no model prompt was sent. See [sandbox scenarios](sandbox/actual-results.json), [isolation evidence](sandbox/isolation.json), [run/cleanup](sandbox/run.json), and [sandbox captures](sandbox/captures/plugin-rename-agent.png). Remote model operation was not tested. The sandbox shares the host kernel and read-only system runtime directories.

Current-environment `bin/install --dry-run` and `bin/uninstall --dry-run` also passed. Exact config bytes and registry JSON were unchanged after both previews. The normal environment was not installed/uninstalled during development.

`bin/setup` and launcher invocation through a symlink from another directory also passed. The Python `venv` fallback could not initialize on this host because system `ensurepip` is absent; the documented `uv` path succeeded. Other Python versions and the fallback on a host with `python3-venv` are unverified.

## Automated coverage

The suite uses the installed [schema fixture](../tests/fixtures/schema-0.9.3.json), [runtime fixture](../tests/fixtures/runtime-0.9.3.json), and [not-found envelopes](../tests/fixtures/errors-0.9.3.json). Runtime identifiers are retained from the dedicated test session. It covers:

- Frozen invocation and connection context, missing/malformed context, popup handoff and public error envelopes.
- Exact ASCII validation, zero-write cancellation/no-op/invalid input, one-write Apply/Yes, source/name changes, location/session/process replacement observations.
- Duplicate submissions, cancellation during bounded worker requests, definite failures, post-write verification and uncertain-result reconciliation without automatic retry.
- Literal argv, disconnected CLI stdin, version/capability checks and malformed process/API data.
- Large snapshots, short state-file pointers, private file validation/consumption and cleanup after popup-launch failure.
- Twenty real prompt_toolkit TUI tests: cursor editing, focus order, paste boundaries/newlines/control characters/Unicode, scrolling, resize, small-screen controls and exception cleanup.
- Forty-one installer checks: scoped cleanup/restoration after intervening user edits, aliases/arrays, separated command groups, comments/quoting/multiline strings/CRLF/BOM, repeated operations, private records, file permissions, concurrent-change refusal, registry/validation/reload failures, and retry recovery. The pinned style-preserving TOML dependency is `tomlkit==0.15.1`.

## Actual Herdr outcomes

The full scenario log is [actual-results.json](actual-results.json). Repeated shortcut openings account for the difference between total checks and distinct scenarios. These actual checks verify resulting server state; the automated suite additionally proves request counts.

The expanded current log is [sandbox/actual-results.json](sandbox/actual-results.json). Its seven additional checks verify actual `bin/install` and `bin/uninstall`: legacy-popup migration; repeated install preserving config/record; dry-run preservation; later user setting/comment/binding preservation; selective legacy restoration; registration/record removal; and harmless repeated uninstall. The original host-session evidence remains alongside it.

| Scenario | Result and evidence |
| --- | --- |
| Manifest linking, two actions, config reload, real shortcuts | Passed; both declared popup entrypoints open. |
| Missing live agent | Passed; Apply disabled, cancellation works. [Capture](captures/missing-agent.png) |
| Edit and Apply | Passed; `verifier` becomes `renamed-verifier`; pane label stays `backend-api`. [Edit](captures/plugin-rename-agent.png) |
| Sync confirmation and default No | Passed; opening and default Enter leave the agent name unchanged. [Confirmation](captures/plugin-sync-agent.png) |
| Confirmed Yes | Passed; agent becomes `backend-api`; pane label remains unchanged. |
| Pasted newline | Passed; raw `invalid\nname` remains visible and invalid, without a rename. [Capture](captures/invalid-paste.png) |
| Changed pane label | Passed; stale confirmation rejected. [Capture](captures/changed-source.png) |
| Changed agent name | Passed; stale confirmation rejected. [Capture](captures/changed-agent-name.png) |
| Missing or invalid pane label | Passed; Yes disabled with an English explanation. [Invalid label](captures/invalid-pane-label.png) |
| Resize and cancellation | Passed; a small terminal disables Apply and Esc closes it. [Capture](captures/small-terminal.png) |
| Normal popup closure | Passed; original agent terminal/process group and focus survive the core popup scenarios. |
| Focus changes after invocation | Passed; rename still affects the captured agent, leaving the other agent named `reserved-name`. |
| Duplicate live name | Passed; error and entered name remain; Ctrl+C cancels. [Capture](captures/duplicate-name.png) |
| Movement through an old pane alias | Passed; a move to another tab is rejected before rename. [Capture](captures/moved-target.png) |
| Target pane/tab closure | Passed; Herdr closes the host popup when its last-pane tab disappears, without redirecting a rename. [After closure](captures/exited-target.png) |
| Unlink/removal | Passed; registry is empty afterward, config bytes and checkout remain intact. |

## Visual comparison

All three captures use the same actual Herdr client, 100 × 32 terminal and default theme:

| Surface | Outer cells | Inner cells | Capture |
| --- | --- | --- | --- |
| Native `rename_pane` | 56 × 7 | 54 × 5 content | [PNG](captures/native-rename-pane.png), [TXT](captures/native-rename-pane.txt) |
| Plugin edit | 56 × 11 | 53 × 9 terminal | [PNG](captures/plugin-rename-agent.png), [TXT](captures/plugin-rename-agent.txt) |
| Plugin sync | 60 × 14 | 57 × 12 terminal | [PNG](captures/plugin-sync-agent.png), [TXT](captures/plugin-sync-agent.txt) |

The edit popup matches the native requested width; both plugin dialogs are taller to reserve errors and confirmation information. Herdr draws their outer border/title, without a redundant inner title/frame. Native rename is centered over the full frame; terminal popups are centered over the pane area beside the sidebar. The plugin input uses reverse video and its buttons use text/focus highlighting, while native rename uses themed filled surfaces and Save/Clear/Cancel. The plugin instead has Apply/Cancel or Yes/No and deliberately offers no clear action. These remaining differences are visible, rather than claimed to be an exact native clone.

## Limits and unverified scenarios

**Strict identity criterion: unmet.** Terminal IDs, optional session references and foreground process observations are useful checks, but provide no verified immutable launch generation. PID/session reuse or missing observations can make a replacement indistinguishable. `agent.rename` exposes no atomic expected-identity precondition, leaving a race between revalidation and the write. See [public identity coverage](compatibility.md#identity-coverage).

The following were not exercised inside actual Herdr and remain **Unverified**, even where automated tests pass: a timeout/disconnect after mutation and subsequent reconciliation; same-terminal agent exit/restart with reused session metadata; all cursor/long Unicode/invalid-control paste combinations; terminal cleanup after a real popup exception; and visible notification delivery under every toast setting. The tests use idle agents, so protection of an actively working agent's output and work is not certified by this run. Older Python interpreters, other themes/outer terminal emulators, and newer Herdr versions require their own checks.

`bin/install`/`bin/uninstall` and [INSTALL.md](../INSTALL.md) now automate scoped setup/removal. The current environment's known legacy popup is recognized by exact command text, unrelated custom conflicts require an explicit replacement option, and builtin conflicts are left for deliberate resolution. Uninstall removes exact plugin action entries and restores displaced bindings only into free slots; later user bindings win and blocked restoration records remain available. Inline keys tables/command arrays are rejected without edits. Parsed unrelated settings are checked for equality before any candidate is written; comments and separated tables are covered by preservation tests. Whole-file backup restoration is never used.

Source-backed Graphify baseline outputs are ignored development aids. Its schema graph reports 57 repeated same-endpoint edges collapsed; this is not a plugin test result or evidence of atomic identity protection.
