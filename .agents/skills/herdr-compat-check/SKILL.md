---
name: herdr-compat-check
description: Verify that the herdr-rename-agent plugin (local.agent-rename) still works after Herdr is updated, or on demand. Use when the user says "herdr updated", "herdr 업데이트", "herdr 새 버전", "check compatibility", "compat check", "점검", "호환성 확인", "verify plugin against new herdr".
---

# Herdr compatibility check

Shared by Claude Code and Codex CLI. Canonical location: `.agents/skills/herdr-compat-check/`; `.claude/skills/herdr-compat-check` is a symlink to it.

Checks this plugin against the installed Herdr. Baseline: 0.9.3 (`herdr-plugin.toml` `min_herdr_version`, `docs/compatibility.md`); last fully verified: 0.9.5 (2026-10-19, all passed).

## Safety rules (hard)
- Never run `bin/install` or `bin/uninstall` against the live Herdr. Never modify `~/.config/herdr/*`. Never restart or kill the user's Herdr server. Live checks are read-only.
- Never run `tools/verify_sandbox.py --suite old` or `--suite all` (old is historical).
- Always pass `--next-output <scratch dir>` to the sandbox run so `docs/sandbox-next/` is not overwritten.
- Do not edit docs or commit unless the user asks.
- Use a scratch dir (`$SCRATCH`) for all output.
- Agent notes (optional). Claude Code: project convention is to delegate investigation to Sonnet subagents (`model: "sonnet"`). Codex: run directly.

## Steps
1. Quick read-only check (seconds). Covers versions, `herdr status`, release notes, live install state (plugin list/actions, `plugins.json`, `config.toml` key bindings), schema drift vs `tests/fixtures/schema-<baseline>.json`, the plugin's own capability check, and `--help` drift vs stored snapshots:
   `python3 .agents/skills/herdr-compat-check/scripts/check_live.py --out $SCRATCH/live`
   Exit 1 means FAIL. WARN means review: the newer-than-baseline WARN is expected; schema or help drift is not. Read the release notes it prints for anything touching agents, panes, plugins, popups or notifications.
2. Unit tests (~15 s): `.venv/bin/python -m pytest -q` from the repo root. Pass = all passed, exit 0 (467 as of 2026-10-19). They use fixtures and do not exercise the new binary.
3. Sandbox E2E against the real binary (~11 min; needs `bwrap`, `herdr`, `codex` on PATH). Run it as a long-running background command and wait for completion; do not poll in a tight loop (Claude Code: Bash `run_in_background: true`, no trailing `&`; Codex: a long-running command with a generous timeout, e.g. 20 min):
   `.venv/bin/python tools/verify_sandbox.py --run --suite next --next-output $SCRATCH/next > $SCRATCH/next.log 2>&1`
   Pass: first log line `Passed: sandbox isolation checks`; last line `next suite: {'pass': N, 'fail': 0, 'error': 0, 'skip': 0}` (N was 104; may grow); exit 0; `$SCRATCH/next/isolation.json` records the new Herdr version. On failure inspect `$SCRATCH/next` and re-run subsets with `--only S07,T04`.
4. Report: version delta, release-note highlights, drift table (unchanged / changed / unknown), pytest and E2E results, and a recommendation on whether to update the "verified versions" in README.md, INSTALL.md and `docs/compatibility.md`. Do not edit them unless asked. After a fully verified run, offer to refresh help snapshots with `check_live.py --save-baseline` (writes `baseline/`, labelled with the Herdr version).
5. Tell the user the live smoke test is theirs to do: prefix+Shift+A on a live agent (rename), prefix+Shift+S (sync).

## Troubleshooting
- `codex` discovery (`native_codex()` in `tools/verify_sandbox.py`) can break independently of Herdr (npm launcher layout); pass `--codex-binary` or fix PATH before blaming Herdr.
- `restart_needed: yes` or `server_binary_stale: yes` in status: tell the user to restart Herdr themselves; do not do it.
- Help or schema drift: diff against `docs/compatibility.md` operations table, then run the E2E to see if behavior actually changed.
