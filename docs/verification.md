# Verification procedure

Use this checklist for each release and after every Herdr update. The current revision (workspace sync and the Herdr-style rename dialog) is verified by the unit suite and the sandbox `next` suite; the v0.1.0 procedures below are kept where they still apply and marked where they do not. Record concrete results in [verification-results.md](verification-results.md), with version, date, terminal size, theme, scenario, and evidence paths. A test that was not exercised is **Unverified**. [verification-coverage.md](verification-coverage.md) maps every row and scenario phrase of the PLAN section 10 matrix to the sandbox scenarios (and unit tests) that verify it, and lists the few items that cannot be exercised against a real Herdr. The [compatibility limits](compatibility.md) remain separate from passing behavior tests.

## Read-only checks

Run these against the intended environment:

```bash
herdr --version
herdr api schema --json
herdr plugin list
herdr plugin action list --plugin local.agent-rename
herdr plugin log list --plugin local.agent-rename
```

Confirm the expected version and the required capabilities: `agent.get`, `agent.list`, `agent.rename` (with nullable `name` for `--clear`), `pane.get`, `pane.list` (with `workspace_id`), `pane.process_info`, `plugin.pane.open` (popup, `env`) and `notification.show`. See [capability checks](compatibility.md#capability-checks). Inspect response envelopes instead of assuming newer versions preserve them. Record both binary and server versions when they differ.

## Automated checks

After `bin/setup`, install the hash-pinned test dependencies and run:

```bash
uv pip install --python .venv/bin/python --require-hashes -r requirements-test.lock
.venv/bin/python -m pytest -q
```

Without `uv`, use `.venv/bin/python -m pip install --require-hashes -r requirements-test.lock`. Keep the exact command, result, test count, and any skipped checks in the results file.

Review coverage of the editor port (cases derived from `text_editor.rs`), theme resolution, the workspace planner (every row status, session-wide conflicts, duplicate labels), the batch executor (per-row rechecks, one write per row, transport failure stops the queue unless a read confirms, Stop, repeated-start protection, uncertain results), the `--clear` path, rename trimming, frozen invocation context, explicit popup handoff, malformed/unknown JSON, unsupported capabilities, canonical movement, terminal/agent/session/process changes, unchanged status/revision, validation, source/name changes, no-op names, one-write submission locking, literal argv, definite failures, and uncertain-result reconciliation. Verify large launch argv data uses a short pointer (inline limit 48 KiB, private file limit 4 MiB, including a large multi-pane sync plan), private file validation rejects symlinks/cross-directory pointers/wrong ownership/permissions, successful decode consumes the file, and launch failure cleans it up. Cancellation and invalid inputs must produce zero writes. TUI tests should cover editing keys, viewport scrolling, Enter/Esc/Ctrl+C, press-activated mouse on save/clear/cancel and Yes/No/Stop/Close, multiline/Unicode paste, sync table scrolling and column collapse, selection keys, resizing, small-screen messages, and terminal mode restoration (including mouse modes).

Installation tests must cover `bin/install`/`bin/uninstall`, legacy migration, repeated operations, alias/array conflicts, source registration, private scoped records, failure recovery, and selective cleanup after later user edits. Exercise separated `[[keys.command]]` groups, comments, quoting, multiline header lookalikes, CRLF/BOM, file permissions, and detected concurrent edits. A newer unrelated user binding must block only restoration of its occupied shortcut.

## Isolated Herdr environment

`tools/verify_herdr.py` and the default (`--suite old`) sandbox run exercise the **v0.1.0** single-pane UI and no longer match the current dialogs; they are historical. The reproducible integration tool launches a temporary named session and two idle Codex test processes, operates both shortcuts, captures the actual client terminal stream, and removes its test server and temporary directories:

```bash
.venv/bin/python tools/verify_herdr.py --run
```

It requires `herdr` and `codex` on PATH plus the test lock dependencies. It submits no model prompt. Captures overwrite `docs/captures/`, actual outcomes overwrite `docs/actual-results.json`, and the dedicated runtime fixture is refreshed. The tool sends no further affirmative keys if Herdr closes a popup because its owning tab disappears.

The tool also executes the actual install/uninstall commands in that isolated environment. It migrates a legacy shell popup, repeats installation, checks dry-run behavior, edits settings/adds an unrelated binding after installation, then verifies scoped restoration, preservation, registration removal, recovery-record cleanup, and repeated uninstall.

### OS sandbox E2E

On Linux with `bwrap` and enabled unprivileged user namespaces, run the same real Herdr/PTY scenarios inside a disposable filesystem, PID, and network sandbox:

```bash
.venv/bin/python tools/verify_sandbox.py --run
```

The wrapper copies the checkout, mounts system runtimes/binaries and the existing venv read-only, supplies a fresh HOME and empty environment, and hides the original repository, user Codex configuration, and Herdr sockets. `/etc` contains test passwd/group/hosts files plus only font, loader-cache, and timezone runtime mounts. The network namespace contains only loopback. Preflight checks record namespace separation, hidden paths, empty HOME, read-only runtime mounts, and a writable disposable checkout before starting Herdr.

The real native Codex executable runs with `--no-daemon`, a fresh `CODEX_HOME`, and a test-only provider that requires no authentication and points to a closed loopback endpoint. No model prompt is submitted. This exercises local agent-process detection and plugin behavior; remote model operation is outside the test. If the installed npm layout cannot be resolved, supply `--codex-binary /path/to/native/codex`.

Evidence goes to `docs/sandbox/`: actual captures, scenario results, isolation checks, and the run exit code. The original runtime fixture and existing host-session evidence remain unchanged. The wrapper timeout depends on the suite: 4 minutes for `old`, 55 minutes for `next`, 60 minutes for `all`; exiting the PID namespace terminates its test processes, and the temporary checkout is removed. This is a local integration-test sandbox sharing the host kernel and read-only system runtimes, rather than a VM or a certification of malicious-code containment. It does not add an atomic precondition to Herdr's rename API.

#### Next suite (current revision, PLAN section 10 scenarios)

```bash
.venv/bin/python tools/verify_sandbox.py --run --suite next [--only S1,R03,T]   # docs/sandbox-next/
.venv/bin/python tools/verify_sandbox.py --run --suite all                      # old suite + next suite
```

`--suite old` (the default) keeps the original scenarios and installer lifecycle and writes `docs/sandbox/`; it asserts the pre-revision UI (single-pane sync), so it is only meaningful against the `d5cd79f` (v0.1.0) runtime. `--suite next` writes `docs/sandbox-next/` (`actual-results.json`, `captures/*.txt|png`, `isolation.json`, `run.json`). Scenarios are defined in `tools/next_suite.py` on top of `tools/herdr_lab.py` (layout, agents, popup frame detection by title, keys, bracketed paste, SGR mouse at popup-relative or client coordinates). Each scenario resets labels/names, then reports `pass`, `fail` (planned behavior not observed), `error` (harness/exception) or `skip` (not observable); failures also save the screen and the plugin log. `--only` takes comma-separated ID prefixes. `--output DIR` redirects the old suite's evidence and `--next-output DIR` the next suite's (default `docs/sandbox-next/`); use a scratch directory for partial runs so the recorded full-run evidence is not overwritten. A full next run takes about 12 minutes of scenario time. Fixture: workspace 1 tab 1 has agents a, b and agent-less c; tab 2 has agents d, e and agent-less g; workspace 2 has control agent f. Added scenarios: S23-S25 (80-column client, narrow client resize message, resize while the sync popup is open), S26/S27 (stale preview: changed label, closed pane, renamed agent, cleared label), S28/S29 (Stop during application and Stop latched with Yes; Ctrl+C is used because Herdr holds a lone Esc for over 170 ms; rows take about 20 ms each, so these two scenarios make the batch long with the CLI shim's rename delay, see below), R13 (resize while the rename popup is open) and L01-L09 (installer lifecycle on the current runtime in a separate un-installed session, L09 being an in-place update of an installed v0.1.0 tree to the current code, including a rollback cycle with the `v0.1.0` tree that the wrapper exports with `git archive` to `legacy-v0.1.0/` inside the sandbox checkout; v0.1.0 cannot be installed over a registration from another checkout, so the cycle is new uninstall, v0.1.0 install/uninstall, new install). Captures are TXT except for key UI states (PNG). Limits: Herdr 0.9.3's headless client draws no notification toast, so notification wording is checked through the CLI shim log instead of the screen; Herdr closes a popup when the workspace focus changes; repeated-batch checks compare agent `revision` deltas rather than counting API writes.

**CLI shim (test-only, sandbox only).** `tools/herdr_shim.py` is installed by `Lab` inside the disposable session (`<session tmp>/shim/herdr`, a `sh` launcher) and never on a real Herdr. Herdr overwrites `HERDR_BIN_PATH` for plugin processes with its own executable, so the harness starts the sandbox server with `PYTHONPATH` pointing at a `sitecustomize.py` that sets `HERDR_BIN_PATH` to the shim for the plugin's Python processes (actions and popups) only; `src/` and `bin/` are unchanged, and the harness's own CLI calls use the real binary. The shim appends every call's argv as JSON lines (`start`/`end` records with the exit code) to `shim/calls.jsonl`, runs the real `herdr` with the same argv and returns its exit status, and sleeps before `agent rename` calls only when `shim/rename-delay-ms` exists (the harness sets it per scenario and resets it in `reset`). Verification built on it: H01 checks the plugin really runs through the shim, that output and exit code are transparent and that the delay applies to `agent rename` only; S07 asserts from the log exactly one `notification show` with "All agent names already match their pane names.", no `plugin pane open` and no `agent rename`; S28 sets a 400 ms rename delay on a 7-row batch, sends Ctrl+C when the shim logs the second rename (row 1 finished, row 2 in flight), then requires exactly those two renames in the log (none after Stop), two names synced and five rows "not attempted"; S29 writes Ctrl+C with Yes and requires zero `agent rename` calls and all rows "not attempted". Delivery of the toast itself to a user's screen remains unobserved. The unit-test-only items of the previous revision (timeout or disconnect after a mutation, uncertain `--clear`, unknown envelopes, missing capabilities, large and tampered handoffs, terminal-mode cleanup) now have sandbox scenarios; see the coverage matrix.

**Fault injection (same shim, off by default).** `Lab.set_faults(...)` writes rules to `shim/faults.json`; `Lab.reset` deletes them and `shim/fault-state.json` (the per-scenario counters). A rule matches a contiguous argv subsequence (optionally requiring further argv elements, skipping the first N matches, limiting the number of firings, or arming only after N real `agent rename` calls or after another rule fired) and then makes the plugin's call: fail with no output (a lost connection), print given text (malformed JSON or an error envelope), run the real binary and rewrite its JSON (unknown envelope type, a dropped key, a schema without a method or with a non-nullable rename `name`, argv padding in `pane process-info`), run the real write and then exit non-zero or hang past the client's 5 s timeout, tamper with the private handoff file named by a `plugin pane open` pointer (mode 0644, symlink to a valid copy, garbage, truncation, second hard link, wrong workspace, pointer to another directory, path traversal in the name), or fail `plugin pane open`. Rule format and operations are documented at the top of `tools/herdr_shim.py`. The shim also logs `handoff` records (file presence, mode, size, the plugin's `HERDR_*` environment) that the harness uses to check cleanup and to run a real action or dialog process with a crafted invocation context in its own PTY (X25, X26, X27, X28). The real server is never made to misbehave and is never stopped by these scenarios; see [verification-coverage.md](verification-coverage.md#items-not-exercisable-against-a-real-herdr) for what that means for disconnect scenarios.

Scenario series (S has 27 scenarios, S01-S07 and S10-S29; X has 27, X01-X11 and X13-X28): S01-S29 workspace sync (scope, row mix, conflicts, selection keys and mouse, repeated batch, completion, small/narrow/resized clients, stale previews, Stop), R01-R13 rename (save, trim, clear, `--clear`, cancel, mouse, paste, duplicate, small terminal, resize), T01-T02 theme captures with palette-color checks (default and Dracula), T03-T04 native `rename pane` vs plugin rename side-by-side parity (Ctrl+B Shift+P vs Ctrl+B Shift+A), L01-L09 installer lifecycle, rollback and in-place update, H01 harness self-test of the CLI shim, **F01-F17** transport/API fault injection (connection loss between rows and mid-write, definite row failure, lost or timed-out responses reconciled without a second write for sync, rename and `--clear`, malformed or unknown envelopes, per-pane read failures, missing capabilities, older and newer version strings), **X01-X11, X13-X28** exited/moved/replaced targets for both dialogs and for sync previews, swaps and cycles, a pane arriving after the preview, scrolling in a short client, large/oversize/tampered/cleaned-up handoffs, invocation context handling, and terminal-mode restoration of the dialog processes, **E01-E06** the editor inside the real popup compared with the ported model. The last full run (2026-10-11): 104 pass, 0 fail, 0 error, 0 skip (S 27, R 13, T 4, L 9, H 1, F 17, X 27, E 6); unit suite 464 passed. Results: [sandbox-next/actual-results.json](sandbox-next/actual-results.json).

Use a separate test environment and a dedicated pane/agent; do not rename the active development agent. `HERDR_CONFIG_PATH` isolates only the config file. Herdr 0.9.3's plugin registry and session directories use `XDG_CONFIG_HOME`, and plugin state uses `XDG_STATE_HOME`. Set both to fresh test directories, choose a named test session, and retain the normal HOME value. [Config/state path implementation](https://github.com/herdrdev/herdr/blob/v0.9.3/src/config/io.rs#L30)

An example environment for a manual test:

```bash
rename_test_root=$(mktemp -d /tmp/herdr-agent-rename-test.XXXXXX)
export XDG_CONFIG_HOME="$rename_test_root/config"
export XDG_STATE_HOME="$rename_test_root/state"
export HERDR_CONFIG_PATH="$XDG_CONFIG_HOME/herdr/config.toml"
mkdir -p "$XDG_CONFIG_HOME/herdr"
```

Write the test config with onboarding disabled and only the intended test bindings. Under this same environment, link the checkout and attach using `herdr --session agent-rename-test`. Supply that named session consistently to test commands. Explicit `--session` takes precedence over inherited socket context; its data/API/client sockets are within the test config tree. [Session path behavior](https://github.com/herdrdev/herdr/blob/v0.9.3/src/session.rs#L157)

Capture the resolved config, plugin list, and socket paths before proceeding. Every test mutation must target the test environment. On completion, unlink the test registration and stop only the named test session created for this verification. Record cleanup; leave the normal configuration and live agents untouched.

## Installation and removal

- Confirm setup succeeds from a fresh checkout and that `plugin link` does not need build hooks to have run implicitly.
- Confirm linking exposes exactly `local.agent-rename.open` and `local.agent-rename.sync`.
- Use a test config with unrelated settings, comments, and a conflicting shifted-uppercase spelling. The helper must migrate the known legacy popup; unrelated collisions must be reported unless explicitly selected with `--replace-conflicts`. Do not append duplicates or overwrite unrelated entries.
- Validate with `herdr config check`, then use `herdr server reload-config` or the UI's reload action. Existing agents must keep running.
- Invoke both actions through real shortcuts, not only `plugin action invoke`.
- Install, then change a theme/setting and add another binding/comment before uninstall. Remove only this plugin's binding tables and selectively restore displaced ones, without replacing the config from a whole-file backup. Compare unrelated config/source files and private record cleanup. A newer occupied shortcut must win over restoration. Reload must remove plugin bindings without a server restart.

## Actual UI and behavior checklist

Herdr 0.9.3 has no native agent rename overlay, so the comparison reference is the native rename pane overlay (see [r1-source-findings](r1-source-findings.md)). At one fixed terminal size and theme, capture native `rename_pane`, Rename agent and Sync agent names, and note dimensions, labels, colors and differences. Source review or standalone prompt_toolkit screenshots do not complete this comparison. The `next` suite automates the items below; do them by hand only for a new Herdr version or theme.

Rename:

- Opens with the current explicit agent name and the cursor at the end; no replace-on-type; Tab does nothing.
- Cursor and kill keys, long text, Unicode, valid paste, newline and control-character paste (shown as U+FFFD, rejected, nothing written).
- Enter saves with trimming; Ctrl+C clears the field only; saving an empty field runs `--clear`; Esc cancels. Only the selected agent's name changes; its pane name, work and pending terminal input remain.
- Mouse: press on save, clear, cancel; press elsewhere cancels.
- Duplicate name (held anywhere in the session) shows an error on the status row and keeps the input.
- Moved, exited or replaced targets are rejected and never redirected.

Sync:

- Panes across several tabs, another workspace unchanged; focus change after invocation does not retarget.
- Mixed rows, unnamed agent with a named pane, duplicate and externally held names, invalid labels, no eligible rows (notification, no popup), conflict-only (information popup).
- Opening sends zero writes; default No plus Enter, N, Esc and Ctrl+C write nothing; Tab/Left/Right plus Enter, Y and a mouse press on Yes apply once.
- Repeated Enter/Y/clicks and double clicks cannot start a second batch; Stop leaves later rows not attempted.
- Pane or agent name changed, pane closed, or label cleared after the preview: the row is skipped with a reason.
- Small, narrow and resized clients: controls reachable, or a resize message with Esc to cancel.

General:

- Resize to a tiny area; confirm no write is possible when controls cannot fit.
- After success, cancellation and exceptions, confirm original focus, cursor, echo, input modes, mouse reporting and paste behavior recover.
- Exercise definite transport/API failures; after an uncertain write, a retry reads first and does not write twice.

## Release decision

Passing unit/TUI checks and a working popup establish implementation behavior, not atomic identity protection. Mark actual UI scenarios independently and list any unverified item or remaining visual difference. The release gate requires installation/removal preservation, actual UI evidence, and transparent limitations. The sandbox run does not cover deployment: the user's live Herdr has not been updated. The strict replacement-identity criterion remains unmet while the public API lacks a verified immutable identity plus an atomic rename precondition.
