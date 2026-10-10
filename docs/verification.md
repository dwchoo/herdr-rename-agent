# Verification procedure

Use this checklist for the initial release and after every Herdr update. Record concrete results in [verification-results.md](verification-results.md), with version, date, terminal size, theme, scenario, and evidence paths. A test that was not exercised is **Unverified**. The [compatibility limits](compatibility.md) remain separate from passing behavior tests.

## Read-only checks

Run these against the intended environment:

```bash
herdr --version
herdr api schema --json
herdr plugin list
herdr plugin action list --plugin local.agent-rename
herdr plugin log list --plugin local.agent-rename
```

Confirm the expected version and the required agent, pane, process-info, popup, and notification capabilities. Inspect response envelopes instead of assuming newer versions preserve them. Record both binary and server versions when they differ.

## Automated checks

After `bin/setup`, install the hash-pinned test dependencies and run:

```bash
uv pip install --python .venv/bin/python --require-hashes -r requirements-test.lock
.venv/bin/python -m pytest
```

Without `uv`, use `.venv/bin/python -m pip install --require-hashes -r requirements-test.lock`. Keep the exact command, result, test count, and any skipped checks in the results file.

Review coverage of frozen invocation context, explicit popup handoff, malformed/unknown JSON, unsupported capabilities, canonical movement, terminal/agent/session/process changes, unchanged status/revision, validation, source/name changes, no-op names, one-write submission locking, literal argv, definite failures, and uncertain-result reconciliation. Verify large launch argv data uses a short pointer, private file validation rejects symlinks/cross-directory pointers/wrong ownership/permissions, successful decode consumes the file, and launch failure cleans it up. Cancellation and invalid inputs must produce zero writes. TUI tests should cover editing, horizontal scroll, focus order, Enter/Esc/Ctrl+C, multiline/Unicode paste, resizing, small-screen cancellation, and terminal mode restoration.

Installation tests must cover `bin/install`/`bin/uninstall`, legacy migration, repeated operations, alias/array conflicts, source registration, private scoped records, failure recovery, and selective cleanup after later user edits. Exercise separated `[[keys.command]]` groups, comments, quoting, multiline header lookalikes, CRLF/BOM, file permissions, and detected concurrent edits. A newer unrelated user binding must block only restoration of its occupied shortcut.

## Isolated Herdr environment

The reproducible integration tool launches a temporary named session and two idle Codex test processes, operates both shortcuts, captures the actual client terminal stream, and removes its test server and temporary directories:

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

Evidence goes to `docs/sandbox/`: actual captures, scenario results, isolation checks, and the run exit code. The original runtime fixture and existing host-session evidence remain unchanged. The wrapper has a four-minute timeout; exiting the PID namespace terminates its test processes, and the temporary checkout is removed. This is a local integration-test sandbox sharing the host kernel and read-only system runtimes, rather than a VM or a certification of malicious-code containment. It does not add an atomic precondition to Herdr's rename API.

#### Next suite (PLAN section 10 scenarios)

```bash
.venv/bin/python tools/verify_sandbox.py --run --suite next [--only S1,R03,T]   # docs/sandbox-next/
.venv/bin/python tools/verify_sandbox.py --run --suite all                      # old suite + next suite
```

`--suite old` (the default) keeps the original scenarios and installer lifecycle and writes `docs/sandbox/`; it asserts the pre-revision UI (single-pane sync), so it is only meaningful against the `d5cd79f` runtime. `--suite next` writes `docs/sandbox-next/` (`actual-results.json`, `captures/*.txt|png`, `isolation.json`, `run.json`). Scenarios are defined in `tools/next_suite.py` on top of `tools/herdr_lab.py` (layout, agents, popup frame detection by title, keys, bracketed paste, SGR mouse at popup-relative or client coordinates). Each scenario resets labels/names, then reports `pass`, `fail` (planned behavior not observed), `error` (harness/exception) or `skip` (not observable); failures also save the screen and the plugin log. `--only` takes ID prefixes. Fixture: workspace 1 tab 1 has agents a, b and agent-less c; tab 2 has agents d, e and agent-less g; workspace 2 has control agent f. Added scenarios: S23-S25 (80-column client, narrow client resize message, resize while the sync popup is open), S26/S27 (stale preview: changed label, closed pane, renamed agent, cleared label), S28/S29 (Stop during application and Stop latched with Yes; Ctrl+C is used because Herdr holds a lone Esc for over 170 ms, and rows take about 20 ms each, so S28 tries several delays and skips if none lands inside the batch), R13 (resize while the rename popup is open) and L01-L08 (installer lifecycle on the current runtime in a separate un-installed session, including a rollback cycle with the `v0.1.0` tree that the wrapper exports with `git archive` to `legacy-v0.1.0/` inside the sandbox checkout; v0.1.0 cannot be installed over a registration from another checkout, so the cycle is new uninstall, v0.1.0 install/uninstall, new install). Captures are TXT except for key UI states (PNG). Limits: Herdr 0.9.3's headless client draws no notification toast, so notification wording is unobservable; Herdr closes a popup when the workspace focus changes; repeated-batch checks compare agent `revision` deltas rather than counting API writes.

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

At one fixed terminal size and theme, capture native `rename_pane`, Rename agent, and Sync agent name. Store captures in `docs/captures/` and note outer/inner dimensions, spacing, colors, borders, and button differences. Source review or standalone prompt_toolkit screenshots do not complete this comparison.

- Open both shortcuts on a test agent; verify the action captures the original pane despite later focus changes.
- Verify existing/unnamed agent display, exact pane label display, and No as the initial sync focus. Opening sync must send zero writes.
- Exercise cursor movement, Home/End, deletion, long text, Tab/Shift+Tab, Enter, Esc, Ctrl+C, valid paste, newline paste, Unicode, invalid names, and duplicate names.
- Apply a valid name once. Confirm only the selected agent name changes; its pane label, work, and pending terminal input remain intact.
- Confirm Yes copies exactly the pane label. Confirm No/Esc/Ctrl+C produce no rename. Confirm matching names complete without a write.
- While a dialog is open, separately change the test pane label or agent name. Sync must refuse the stale confirmation and require reopening.
- Exit, replace, or move only the test target. Confirm detected identity/location changes produce an error and never redirect the rename. Record any replacement case public data cannot distinguish.
- Exercise definite transport/API failures and explicit retry. After an uncertain write, inspect the current name; a matching desired name must reconcile without a second write, and a differing value must require reopening.
- Repeat affirmative input during an in-flight request. Confirm exactly one write.
- Resize normally and to a tiny area. Affirmative controls must be disabled when they cannot fit, input/focus preserved where possible, and cancellation available.
- After success, cancellation, and exceptions, confirm original focus, cursor, echo, input modes, and paste behavior recover. Confirm a successful sync notification is shown once when notification delivery is available.

## Release decision

Passing unit/TUI checks and a working popup establish implementation behavior, not atomic identity protection. Mark actual UI scenarios independently and list any unverified item or remaining visual difference. The release gate requires installation/removal preservation, actual UI evidence, and transparent limitations. The strict replacement-identity criterion remains unmet while the public API lacks a verified immutable identity plus an atomic rename precondition.
