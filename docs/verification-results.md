# Verification results

Implementation is available for the Linux/Herdr 0.9.3 baseline. Automated checks and the recorded core actual UI scenarios pass. The full release gate remains **incomplete**: the public API cannot establish strict atomic agent identity, and the additional real-world scenarios listed below are unverified.

## Environment and commands

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
