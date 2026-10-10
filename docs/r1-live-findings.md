# R1 live findings (Herdr 0.9.3)

Collected by `tools/probe_r1.py --run` (reruns everything). It uses the same bubblewrap approach as `tools/verify_sandbox.py`: fresh HOME, separate PID/net/mount namespaces, copied checkout, named Herdr session, three idle Codex 0.162.1 agents with no model prompt. The user's Herdr was never touched. Raw data: `docs/r1-live/r1-data.json`; screens: `docs/r1-live/screens/`. Client PTY was 100x32 unless noted. Plugin popups were opened with the plugin's real Ctrl+B, Shift+S shortcut or with `herdr plugin pane open`.

Test layout: workspace `w1` has tab `w1:t1` (label "1") with panes `p1` (label `tester`, agent `alpha`), `p2` (no label, no agent), `p3` (label `docs-shell`, no agent) and tab `w1:t2` (label "second") with `p4` (no label, agent `beta`), `p5` (label `tab2-extra`, no agent). Workspace `w2` has `w2:p1` (label `gamma-label`, agent `gamma`).

## 1. Workspace enumeration

Commands: `herdr pane list --workspace w1`, `herdr pane list`, `herdr agent list`, `herdr pane get <id>`, `herdr agent get <id>`.

- `pane list --workspace w1` returned all 5 panes of both tabs (`w1:p1 p3 p2` in tab `w1:t1`, then `w1:p4 p5` in `w1:t2`). Non-focused tabs are included. `pane list` with no filter returned all 6 panes including `w2:p1`. Order follows layout/tab order, not pane id (`p3` precedes `p2`).
- Pane entry with explicit label and agent (identical shape from `pane list` and `pane get`, wrapped as `{"result":{"pane":{...},"type":"pane_info"}}` for `get`):

```json
{"agent":"codex","agent_status":"unknown","cwd":"/workspace/agent-rename","focused":true,"foreground_cwd":"/workspace/agent-rename","label":"tester","pane_id":"w1:p1","revision":1,"scroll":{"max_offset_from_bottom":9,"offset_from_bottom":0,"viewport_rows":14},"tab_id":"w1:t1","terminal_id":"term_...","terminal_title":"agent-rename","terminal_title_stripped":"agent-rename","workspace_id":"w1"}
```

- Unnamed pane without agent (`w1:p2`): the `label`, `agent`, `terminal_title` keys are all absent: `{"agent_status":"unknown","cwd":...,"focused":false,"foreground_cwd":...,"pane_id":"w1:p2","revision":0,"scroll":{...},"tab_id":"w1:t1","terminal_id":"term_...","workspace_id":"w1"}`.
- The explicit pane label is the key `label`. For an unnamed pane the key is missing (not null, not empty). A pane with an agent but no label (`w1:p4`) also has no `label` key. The pane entry has `agent` (kind, `"codex"`) but never the agent name; the agent name only exists in `agent list`/`agent get` as `name`.
- `herdr agent list` returned only the 3 panes that run agents, across tabs and workspaces, with `name`, `pane_id`, `tab_id`, `workspace_id`, `terminal_id`, `interactive_ready`, `state_change_seq`:

```json
{"agent":"codex","agent_status":"unknown","cwd":"/workspace/agent-rename","focused":true,"foreground_cwd":"/workspace/agent-rename","interactive_ready":true,"name":"alpha","pane_id":"w1:p1","revision":1,"state_change_seq":0,"tab_id":"w1:t1","terminal_id":"term_...","terminal_title":"agent-rename","terminal_title_stripped":"agent-rename","workspace_id":"w1"}
```

- `agent get` on a pane without agent exits 1 (`w1:p2`, `p3`, `p5`; empty stdout in the probe's capture). An agent with no explicit name has no `name` key (see q2 after `--clear`).
- Plugin popups do NOT appear: with the mouse-probe popup open and with the repo's sync popup open, `pane list` (both filtered and unfiltered) and `agent list` contained exactly the same 5 / 6 / 3 entries.
- Relation: pane to agent is at most one agent per pane (`agent get <pane>` returns that pane's agent; `agent.pane_id` equals the pane). Agents are a subset of panes; most panes in the test had no agent. Join key: `pane_id` (also `terminal_id`).
- Workspace and tab listing: `workspace list` gives `workspace_id`, `label`, `pane_count`, `tab_count`; `tab list` gives all tabs with `workspace_id`.

Conclusion: a workspace sync can use `pane list --workspace <id>` (covers every tab) joined to `agent list` by `pane_id`; absent `label` means "no explicit name".

## 2. Name uniqueness and `--clear`

Agent names are unique across the whole session (all workspaces and tabs), and valid names are lowercase only.

```text
$ herdr agent rename w2:p1 alpha          # alpha lives in workspace w1; target in w2
rc=1  stderr: {"error":{"code":"agent_name_taken","message":"agent name alpha is already used; candidates: terminal_id=term_65d7d167941141 pane_id=w1:p1 workspace_id=w1 tab_id=w1:t1 cwd=/workspace/agent-rename status=Unknown"},"id":"cli:agent:rename"}
```

- The error goes to stderr, stdout empty, exit code 1. The target kept its name (`gamma`). Same envelope for a different tab in the same workspace (`w1:p4` to `alpha`).
- Renaming an agent to its own current name succeeds (rc 0, no change).
- `ALPHA` and `Gamma2` fail with `invalid_agent_name`: `agent name must start with a lowercase letter and contain only lowercase letters, digits, '-' or '_' (1-32 characters)`. This matters for sync because pane labels with capitals or spaces cannot be agent names.
- `herdr agent rename tester alpha` (target is a pane label) fails with `agent_not_found`: `agent target tester not found`. Targets are pane/agent ids only.
- `herdr agent rename w2:p1 --clear`: rc 0, returns `agent_info` without the `name` key; `agent get` confirms the key is gone. Running `--clear` again on an unnamed agent also returns rc 0 with the same unnamed result (idempotent). A cleared name frees the global name only when the holder is cleared: after clearing `w2:p1`, `alpha` stayed taken by `w1:p1`; after clearing `w1:p1`, renaming `w2:p1` to `alpha` succeeded.
- Implication for batch sync: two panes in the same workspace (or any workspace) with identical labels, or a label equal to an existing agent name elsewhere, produce `agent_name_taken` for the later one; a swap between two agents cannot be done in one pass without an intermediate step.

## 3. Mouse delivery to plugin popups

Throwaway plugin `local.mouse-probe` (created inside the sandbox, not this repo's manifest) with popup entrypoint `tools/mouseprobe.py`: raw tty, enables `\x1b[?1000h\x1b[?1002h\x1b[?1006h`, logs every stdin byte. Opened with `herdr plugin pane open --plugin local.mouse-probe --entrypoint m80 --focus`; synthetic SGR events were written to the Herdr client PTY master.

| Input injected at client PTY | Bytes the popup process received |
| --- | --- |
| `a` | `61` |
| Up arrow `\x1b[A` | `1b5b41` |
| Tab | `09` |
| click at screen cell (41,10) 0-based, sent as `\x1b[<0;42;11M` + `\x1b[<0;42;11m` | `\x1b[<0;8;4M` and `\x1b[<0;8;4m` |
| wheel up `\x1b[<64;42;11M` | `\x1b[<64;8;4M` |
| click at (0,0), outside the popup | nothing delivered; popup stays open |

Mouse reports are delivered to the popup, with coordinates translated to be relative to the popup's PTY (1-based, popup cell (1,1) is the PTY's top-left; the click 7 columns right and 3 rows below the probe's origin cell arrived as column 8, row 4). A click far outside the popup (cell 0,0) was not delivered; border clicks were not tested.

Delivery requires the popup to enable mouse reporting itself: a second run with `MOUSEPROBE_NOENABLE=1` (probe never writes `?1000h`) received nothing from the same click and wheel. This is consistent with this repo's sync popup ignoring clicks, since `prompt_toolkit` runs with `mouse_support=False`. Conclusion: setting prompt_toolkit `mouse_support=True` should work; button hit areas map directly from the received SGR coordinates.

## 4. Popup sizing

PTY size seen by the popup process (`TIOCGWINSZ`, rows x cols) and the outer frame measured from the client screen:

| Manifest size | Client | Outer frame (w x h) | Popup PTY (cols x rows) |
| --- | --- | --- | --- |
| `width="80%"`, `height="70%"` | 100x32 | 59 x 21 | 56 x 19 |
| `width="80%"`, `height="70%"` | 160x50 | 107 x 34 | 104 x 32 |
| `width=56`, `height=7` | 100x32 | 56 x 7 | 53 x 5 |
| `width=56`, `height=7` | 160x50 | 56 x 7 | 53 x 5 |

- Percentages are relative to the pane area to the right of the sidebar (25-cell sidebar plus its border at 100 columns gives about 74 columns and 30 rows: 80% is 59, 70% is 21), not the whole client.
- PTY = outer width - 3 columns and outer height - 2 rows (left border 1, a reserved column plus right border 2, top and bottom borders). A 56 cell popup gives a 53 cell PTY, as previously observed. For a native-sized 56x7 PTY the manifest would need `width = 59`, `height = 9`.
- The popup is centered in the pane area (not the full client).

## 5. Sync "Yes does not work" with the current plugin

Setup: `p1` with pane label `tester`, agent name `other`; plugin linked only in the sandbox; popup opened with Ctrl+B then Shift+S (`S`). The popup (initial screen in `docs/r1-live/screens/q5-sync-open.txt`) shows the current name `other`, the new name `tester`, and `[ Yes ]  [ No ]`. Yes is enabled (nothing blocks it). Focus starts on No. State was reset before every gesture.

| Gesture | Agent renamed to `tester`? | Popup afterwards |
| --- | --- | --- |
| (a) Tab, Enter | yes | closed |
| Tab, Space | yes | closed |
| Shift+Tab, Enter | yes | closed |
| (b) Right (`\x1b[C`), Enter | no | closed (acted as No) |
| (b2) Right (`\x1bOC`), Enter | no | closed (acted as No) |
| (c) Left, Enter | no | closed (acted as No) |
| Enter alone | no | closed (default No) |
| Tab, Tab, Enter | no | closed (focus returned to No) |
| (d) `y` / `Y` | no | still open, no visible change |
| (e) mouse click on Yes (SGR press and release at the `Yes` text, cell 77,21) | no | still open, no visible change |

Why: in `src/herdr_agent_rename/ui.py` the default focus is the No button (`focused_element=self.cancel_button.control` in sync mode); Left/Right/Home/End in sync mode are bound to `_scroll_names` (horizontal scroll of the name text) rather than button focus, so Enter still activates No; there is no `y` binding; and `mouse_support=False` means the popup never enables mouse reporting, so Herdr never forwards the click (see q3). Only Tab (or Shift+Tab) moves focus to Yes. Two Tabs cycle back to No. No hidden precondition disabled Yes in this scenario (valid label, differing name, normal geometry); the earlier E2E only passed because it used Tab then Enter.

## 6. Popup open latency (current plugin)

Measured from writing `S` (after Ctrl+B) to the client screen, 5 runs, pyte-polled every 10 ms (resolution about 10 ms; includes the host action process, Python startup of the action and dialog, the popup open request and Herdr rendering):

- popup frame with title visible: about 0.10 s (0.100 to 0.113)
- dialog content with Yes/No buttons: about 0.26 s (0.243 to 0.318; median 0.257)

Single sandbox, idle agents, 100x32; absolute figures on the user's machine will vary.

## Not verified

- Mouse button variants other than left press/release and wheel up (right, middle, drag, motion, modifier keys), and whether Herdr consumes events for popup frames/border clicks.
- Whether `?1000h` alone (without `?1006h`) or other encodings are forwarded.
- Behavior with an actively working (non-idle) agent, other `agent_status` values, and non-Codex agents.
- Rename of agents in a pane that disappears mid-batch; `pane list` ordering guarantees; pane list behavior for zoomed panes or plugin split/tab-placement panes (only popups were tested).
- Different Herdr themes, outer terminals, sidebar widths and `sidebar` collapsed state for percentage sizing.
- Latency on cold start (first launch after boot) and on the user's real machine; the figures exclude Herdr's own key-binding dispatch.
- Real Claude/other agent kinds' interaction with `--clear`.
