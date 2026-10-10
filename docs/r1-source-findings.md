# R1 source findings (Herdr v0.9.3, source and schema half)

Source: `herdrdev/herdr` tag `v0.9.3`, commit `7b116c05bfda646af39d2524c54e70c751f57ee8` (Apache-2.0). Links are `https://github.com/herdrdev/herdr/blob/v0.9.3/<path>#L<line>`; below only `path#Lline` is shown. Schema from `herdr api schema --json` (installed 0.9.3); it is byte-identical (as parsed JSON) to `tests/fixtures/schema-0.9.3.json` in this repo. No live Herdr experiments were run.

## 0. Headline: there is no native agent rename overlay in 0.9.3

The client overlay `ClientShellOverlay::Rename` ([state.rs#L297](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/state.rs#L297)) has only these targets: `NewWorkspace`, `Workspace`, `NewTab`, `Tab`, `Pane`. Titles: "new workspace", "rename workspace", "new tab", "rename tab", "rename pane". A repo-wide grep finds no `AgentRename` in `src/client` or the keybind actions (`RenameWorkspace`, `RenameTab`, `RenamePane` only, [actions.rs#L26-L149](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/actions.rs#L26)). Agent rename exists only as API `agent.rename` / CLI `herdr agent rename <target> <name>|--clear` ([cli/agent.rs#L757](https://github.com/herdrdev/herdr/blob/v0.9.3/src/cli/agent.rs#L757)).

Consequence for PLAN.md: the "native agent rename overlay" cannot be captured or compared. The closest native reference is the **rename pane** overlay (same renderer, same buttons for all five targets). Everything in sections 1-5 describes that shared overlay; the pane variant is called out where it differs. Parity for the agent dialog is therefore "style it like Herdr's rename overlay", not "match a native agent dialog". PLAN.md §2 row 1 and §4 ("native agent rename overlay") should be reworded.

## 1. How the rename overlay opens, title, initial text, editor mode

Opening (pane variant): `open_rename_pane_overlay` ([overlay_input.rs#L425](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlay_input.rs#L425)):

- title `"rename pane"` (a `&'static str`)
- `TextEditor::new(pane.label.as_deref().unwrap_or_default(), pane.label.is_none())` -- initial text is the **explicit pane label** (`PaneInfo.label`), empty if none; `replace_on_type = true` only when there is no label (so it is moot: text is empty).
- Other variants ([overlay_input.rs#L359-L423](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlay_input.rs#L359)): rename workspace/tab use `TextEditor::new(label, false)`; "new workspace" and "new tab" use `TextEditor::new(suggested, true)` (replace-on-type = true, text is a suggestion).
- Pane rename is opened from keybind `RenamePane` or context-menu "Rename pane" ([context_menu.rs#L382](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/context_menu.rs#L382)).

Editor ([text_editor.rs#L33](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/text_editor.rs#L33)): `new(text, replace_on_type)` calls `insert(text)` (which leaves the cursor at the **end** of the text, after CR/LF/Tab to space and control-char stripping), then sets the flag. So cursor at open = end of text. With `replace_on_type = true` the first `insert` replaces everything; any movement/removal key clears the flag; Backspace/Ctrl+H with the flag set clears the whole text ([text_editor.rs#L69-L100, L196-L210](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/text_editor.rs#L69)).

For the plugin's agent dialog this means: if copying the native pane-rename behaviour, "has explicit name -> replace_on_type = false, cursor at end"; "no name -> empty". Replace-on-type for a prefilled name is used natively only by the *new workspace/tab* overlays, not by any rename.

## 2. Save path, validation, clear, layout

Save: `save_rename_overlay` ([overlay_input.rs#L926](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlay_input.rs#L926)). `trimmed = rename.input.trim()` (Rust `str::trim`, Unicode whitespace, via Deref to str). No client-side validation, no error display: the overlay is `take()`n immediately and a method is queued fire-and-forget (`push_endpoint_method`). Per target:

- Pane: always sends `PaneRename { pane_id, label: Some(trimmed) }`. Server ([api/panes.rs#L1479](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/api/panes.rs#L1479)) trims again and treats empty as `clear_manual_label()`. So **empty input on pane rename clears the label** (a request is sent).
- Workspace: empty -> no request. Tab: empty -> no request (also no request if auto-name unchanged).
- Errors: none surfaced by this overlay; the dialog closes regardless. There is no error layout to copy.

"Clear" button / Ctrl+C: only `rename.input.clear()` (text empty, cursor 0, replace flag false); no request, overlay stays open ([overlay_input.rs#L911-L919](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlay_input.rs#L891), [mouse.rs#L1699-L1703](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/mouse.rs#L1684)). Removal only happens if the user then presses save with an empty field (pane variant). This matches PLAN.md's contract.

Geometry ([overlays.rs#L630](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlays.rs#L630), helpers `panel` L221, `popup` L264, `button` L277, `row` L282, `contrast` L298):

- `popup(area, 56, 7)`: `w = min(56, area.width - 4)`, `h = min(7, area.height - 2)`; returns None if `w < 4 || h < 4`. Origin `x = area.x + (area.width - w)/2`, `y = area.y + (area.height - h)/2` (integer division; area = whole client buffer).
- `panel`: box-drawing border `┌ ─ ┐ │ └ ┘` (single, square), whole outer rect filled with spaces in `panel_bg`; returns inner `(x+1, y+1, w-2, h-2)` = 54x5 for the default size. No title in the border.
- Inner rows (i = inner rect): row 0 title (bold, `text` on `panel_bg`, text starts at `i.x`, no left padding); row 1 blank; row 2 input (full inner width 54, `text` on `surface0`); row 3 buttons; row 4 blank.
- Editor text is rendered at `x = input.x + 1`, width `input.width - 1` = 53 (one blank leading cell inside the input surface). Cursor = terminal cursor (`shape: 0`, visible) at `area.x + cursor_cells`; no cursor styling token. Viewport: up to `width-1` cells before the cursor, remainder after ([text_editor.rs#L240](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/text_editor.rs#L240)). Width via `unicode-width` on grapheme clusters.
- Buttons: `row(i, &[8, 10, 12], gap 2, off 3)`: total = 8+10+12+2*2 = 34; `x0 = i.x + (54-34)/2 = i.x + 10`; save at `i.x+10` (w 8), clear at `i.x+20` (w 10), cancel at `i.x+32` (w 12); y = `i.y + 3`. Button text is centered in its rect, and the rect is filled with the button style.
- Exact labels (leading and trailing space included, lowercase): `" ↵ save "` (8 cells), `" ^c clear "` (10), `" esc cancel "` (12). The text fills each button exactly. `↵` is U+21B5.
- Background dimming: before drawing, every cell of the frame gets `Modifier::DIM`; the panel removes DIM on its own cells ([overlays.rs#L41-L56, L221](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlays.rs#L41)).

## 3. Styles (Palette tokens, [state.rs#L35](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/state.rs#L35))

| Element | fg | bg | modifiers |
| --- | --- | --- | --- |
| Panel fill | (none) | `panel_bg` | DIM removed |
| Border | `accent` | `panel_bg` | DIM removed |
| Title | `text` | `panel_bg` | BOLD |
| Input row (surface, text, padding) | `text` | `surface0` | none |
| Cursor | terminal cursor (hardware), no token | | |
| Primary button (save) | `contrast(p)` | `accent` | BOLD |
| Secondary buttons (clear, cancel) | `text` | `surface0` | BOLD |

`contrast(p)` = `panel_bg`, except when `panel_bg` is `Reset` (theme `terminal`) then `surface_dim`. Hover and pressed states: **none exist** (mouse Moved is ignored while an overlay is open; no pressed style). There is no disabled style and no error style in this overlay.

## 4. Key routing while the rename overlay is open

`route_overlay_key` -> rename block ([overlay_input.rs#L891-L923](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/overlay_input.rs#L891)), in this order:

1. Enter (any modifiers): save.
2. Esc: close, no request.
3. Any key carrying non-empty `generated_text` (printable/AltGr/composition): inserted verbatim through `TextEditor::handle_key` (goes through `insert`: CRLF/CR/LF/Tab -> one space, other control chars dropped).
4. Ctrl+C (`Char('c')`, modifiers exactly CONTROL): clear field.
5. Backspace with SUPER in modifiers: clear field.
6. Otherwise `TextEditor::handle_key` ([text_editor.rs#L163](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/text_editor.rs#L163)); returns early for key-release events and Enter/Esc. Bindings (modifiers must match exactly; `normalize_key_combo` is applied first): Left/Ctrl+B, Right/Ctrl+F, Home/Ctrl+A, End/Ctrl+E, Alt+B / Alt+F (word back/forward), Backspace (plain) or Ctrl+H, Delete (plain) or Ctrl+D, Ctrl+U (kill to start), Ctrl+K (kill to end), Ctrl+W and Ctrl/Alt+Backspace (kill word back), Alt+D (kill word forward), Ctrl+Y (yank local kill buffer), plain/Shift `Char` via `keybind_help_text_char`. Anything else (Tab, BackTab, Up, Down, PageUp/PageDown, Insert, F-keys) returns `None` and is ignored.
7. Paste: `modal_paste_target_active` is true for Rename ([input.rs#L462](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/input.rs#L462)); text goes to `insert_overlay_text` -> `rename.input.insert(text)`.

`normalize_key_combo` ([config/keybinds.rs#L1397](https://github.com/herdrdev/herdr/blob/v0.9.3/src/config/keybinds.rs#L1397)) only maps `Tab+Shift` -> `BackTab` and strips Shift from `BackTab`. It changes nothing relevant to the editor (Shift+Tab stays unbound). Note Shift is kept for Char; the `Char(_)` arm accepts modifiers minus Shift being empty.

There is no keyboard button focus: Tab does nothing. Matches PLAN.md.

## 5. Mouse

([mouse.rs#L1684-L1707](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/mouse.rs#L1684)). Coordinates are **absolute client-frame cells** `(mouse.column, mouse.row)`, tested with `contains(rect, point)` ([shell.rs#L124](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell.rs#L124); half-open rect, requires width and height > 0) against `hits.overlay_primary/clear/cancel`, which are the exact button rects recorded at render time ([composition.rs#L675-L677](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/composition.rs#L675)).

- Only `MouseEventKind::Down(Left)` is handled; release, drag, move and scroll are ignored.
- Activation on **press**, not release.
- Save rect: save (same as Enter). Clear rect: clears field, stays open. **Anything else, including the cancel button, the panel interior, the input, the title and outside the panel, closes the overlay without saving.** The `overlay_cancel` hit rect is recorded but the cancel branch is just the fallback `else`.

## 6. Palette and theme resolution

Struct `Palette` fields (19 tokens): `accent, panel_bg, sidebar_bg, active_row_bg, selection_bg, surface0, surface1, surface_dim, overlay0, overlay1, text, subtext0, mauve, green, yellow, red, blue, teal, peach`. Constructors in [state.rs#L76-L500](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/state.rs#L76) (`catppuccin` L78, `terminal` L128, `dracula` L203).

Built-in names ([config/theme.rs#L4](https://github.com/herdrdev/herdr/blob/v0.9.3/src/config/theme.rs#L4)): `catppuccin, catppuccin-latte, terminal, tokyo-night, tokyo-night-day, dracula, nord, gruvbox, gruvbox-light, one-dark, one-light, solarized, solarized-light, kanagawa, kanagawa-lotus, rose-pine, rose-pine-dawn, vesper`. The **default theme is `catppuccin` (Catppuccin Mocha)**, not a theme called "default". Aliases via `canonical_theme_name` (lowercased, space and `_` -> `-`): `catppuccin-mocha`; `latte`, `light` -> catppuccin-latte; `tokyonight`; `tokyo-day`, `tokyonight-day`; `gruvbox-dark`; `onedark`; `onelight`; `solarized-dark`; `lotus`; `rosepine`; `rosepine-dawn`, `dawn`.

Resolution ([app/mod.rs#L218-L350](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/mod.rs#L218)):

1. `manual_name = [theme] name` or `"catppuccin"`. `dark_name`/`light_name` default to the sibling pair of `manual_name` via `sibling_theme_names` (e.g. dracula -> dracula/dracula, `catppuccin` -> catppuccin/catppuccin-latte, gruvbox <-> gruvbox-light, etc.), overridable with `[theme] dark_name`/`light_name`.
2. If `auto_switch` is false: name = `manual_name`, fallback `catppuccin`. If true: name = `dark_name` or `light_name` depending on the **host terminal appearance** (detected by the client; `None` treated as Dark), fallback catppuccin / catppuccin-latte.
3. Unknown name: warning + fallback palette. `Palette::from_name` uses `canonical_theme_name`.
4. Overrides in order: `[theme.custom]` tokens (all 19 token names, strings), then legacy `[ui] accent` (only if != "cyan" and `[theme.custom] accent` unset), then, when auto_switch is on, `[theme.custom.dark]` or `[theme.custom.light]` per-mode tokens.
5. Color strings ([theme.rs#L155](https://github.com/herdrdev/herdr/blob/v0.9.3/src/config/theme.rs#L155)): `reset|default|none|transparent` -> Reset; `#rrggbb`; `#rgb` (each nibble x17); `rgb(r, g, b)`; named: black, red, green, yellow, blue, magenta|purple, cyan, white, gray|grey, darkgray|darkgrey, lightred, lightgreen, lightyellow, lightblue, lightmagenta, lightcyan; unknown -> cyan.

Public-boundary note: the plugin can read the config TOML but cannot learn the host appearance used for `auto_switch = true` (the user's config has it false). Dark fallback is the Herdr default when unknown.

Python-ready dicts (hex; `None` means terminal default / Reset):

```python
HERDR_DEFAULT = {  # "catppuccin"
    "accent": "#89b4fa", "panel_bg": "#181825", "sidebar_bg": None,
    "active_row_bg": "#1e1e2e", "selection_bg": "#313244", "surface0": "#313244",
    "surface1": "#45475a", "surface_dim": "#1e1e2e", "overlay0": "#6c7086",
    "overlay1": "#7f849c", "text": "#cdd6f4", "subtext0": "#a6adc8",
    "mauve": "#cba6f7", "green": "#a6e3a1", "yellow": "#f9e2af", "red": "#f38ba8",
    "blue": "#89b4fa", "teal": "#94e2d5", "peach": "#fab387",
}
HERDR_DRACULA = {
    "accent": "#bd93f9", "panel_bg": "#282a36", "sidebar_bg": None,
    "active_row_bg": "#373c52", "selection_bg": "#463f5d", "surface0": "#44475a",
    "surface1": "#6272a4", "surface_dim": "#282a36", "overlay0": "#6272a4",
    "overlay1": "#828cb4", "text": "#f8f8f2", "subtext0": "#d2d2dc",
    "mauve": "#ff79c6", "green": "#50fa7b", "yellow": "#f1fa8c", "red": "#ff5555",
    "blue": "#8be9fd", "teal": "#8be9fd", "peach": "#ffb86c",
}
```

All built-in themes are in the appendix at the end. The `terminal` theme uses ANSI named colors (Blue, DarkGray, Gray, White, Green, Yellow, LightRed, Cyan) and Reset, and cannot be expressed as RGB.

## 7. Agent names

([app/agents.rs#L13-L20, L90-L146, L326-L350, L405](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/agents.rs#L90))

- Validation (`valid_agent_name`): first char `a-z`; `name.len() <= 32` (**bytes**, equals chars since ASCII only); remaining chars `a-z 0-9 - _`. Equivalent regex `^[a-z][a-z0-9_-]{0,31}$` (Python caveat: use `re.fullmatch` or `\Z`, not `$`, which accepts a trailing newline). The empty string is invalid via the API. No trimming or case folding server-side.
- Error `invalid_agent_name`: "agent name must start with a lowercase letter and contain only lowercase letters, digits, '-' or '_' (1-32 characters)".
- Uniqueness: **server-wide** across all workspaces and tabs. `agent_name_conflicts` scans `collect_agent_infos()` (every agent terminal in every workspace/tab) for the same `name` on a different `terminal_id`. Error `agent_name_taken`: `agent name <name> is already used; candidates: terminal_id=.. pane_id=.. workspace_id=.. tab_id=.. cwd=.. status=..; ...`. Renaming a terminal to the name it already has is not a conflict (self excluded) and succeeds.
- Check order: resolve target (`agent_not_found` / `agent_target_ambiguous`) -> validate (`invalid_agent_name`) -> uniqueness (`agent_name_taken`) -> `agent_launch_pending` ("agent name cannot change while startup is pending") -> `agent_not_found` "agent target does not currently host an agent" if `effective_agent_label()` is None. The last check applies **to clear too**: a clear on a pane whose agent has exited is rejected.
- Target resolution ([terminal_targets.rs#L74](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/terminal_targets.rs#L74)): a current public pane id that hosts an agent terminal, otherwise a unique agent **name**. `is_agent_terminal()` = has agent name OR has an effective agent label.
- Clear (`name: null` / `--clear`): `clear_agent_name()` sets `agent_name = None`, `agent_name_owner = None` **and `managed_agent = None`** ([terminal/state.rs#L2312](https://github.com/herdrdev/herdr/blob/v0.9.3/src/terminal/state.rs#L2312)); also clears a managed-launch session ref. `set_agent_name` on `name: ""` is unreachable via the API (invalid).
- Silent removal: a stored name is dropped automatically when the agent label or session changes (`reconcile_agent_name_owner`, [state.rs#L2353](https://github.com/herdrdev/herdr/blob/v0.9.3/src/terminal/state.rs#L2353)). So a sync can lose a name later without any plugin action.
- Response: `agent.rename` returns `agent_info` with the updated `AgentInfo` (not `ok`). The no-op case (same name) still marks session dirty and emits `pane.updated`.

## 8. API schema (`herdr api schema --json`; protocol 22, schema_version 1)

Requests are `{"id": str, "method": ..., "params": {...}}`; success `{"id", "result": {"type": ..., ...}}`; error `{"id", "error": {"code", "message"}}`.

| Method | params (`?` optional/nullable) | `result.type` and shape |
| --- | --- | --- |
| `pane.list` | `{workspace_id?}` | `pane_list`: `{panes: [PaneInfo]}` |
| `pane.get` | `{pane_id}` | `pane_info`: `{pane: PaneInfo}` |
| `pane.current` | `{caller_pane_id?}` | `pane_current` |
| `pane.rename` | `{pane_id, label?}` | `pane_info` |
| `agent.list` | `{}` | `agent_list`: `{agents: [AgentInfo]}` |
| `agent.get` | `{target}` | `agent_info`: `{agent: AgentInfo}` |
| `agent.rename` | `{target, name?}` (null = clear) | `agent_info`: `{agent: AgentInfo}` |
| `tab.list` | `{workspace_id?}` | `tab_list`: `{tabs: [TabInfo]}` |
| `workspace.list` | `{}` | `workspace_list`: `{workspaces: [WorkspaceInfo]}` |
| `workspace.get` | `{workspace_id}` | `workspace_info`: `{workspace: WorkspaceInfo}` |
| `notification.show` | `{title, body?, position?: top-left\|top-right\|bottom-left\|bottom-right, sound?: none\|done\|request}` | `notification_show`: `{shown: bool, reason: shown\|disabled\|rate_limited\|no_foreground_client\|busy}` |
| `plugin.pane.open` | `{plugin_id, entrypoint, cwd?, env?: {str:str}, focus?: bool(default false), placement?: overlay\|popup\|split\|tab\|zoomed, width?: PopupSize, height?: PopupSize, direction?: right\|down, target_pane_id?, workspace_id?}` | popup: `ok` (`{type: "ok"}`, no pane info); other placements: `plugin_pane_opened` `{plugin_pane: {plugin_id, entrypoint, pane: PaneInfo}}` |

`PaneInfo`: `pane_id, terminal_id, workspace_id, tab_id, focused, revision` (required) plus optional `cwd, foreground_cwd, restore_error, label, agent, title, terminal_title, terminal_title_stripped, display_agent, agent_status (idle|working|blocked|done|unknown, required), state_labels, tokens, agent_session {agent, kind: id|path, source, value}, scroll`.

`AgentInfo`: `pane_id, terminal_id, workspace_id, tab_id, focused, revision, agent_status` (required) plus optional `name, agent, title, terminal_title, terminal_title_stripped, display_agent, agent_session, cwd, foreground_cwd, state_labels, tokens, completion_seq, interactive_ready, launch_pending, screen_detection_skipped, state_change_seq`.

`TabInfo`: `tab_id, workspace_id, label, number, pane_count, focused, agent_status`. `WorkspaceInfo`: `workspace_id, label, number, pane_count, tab_count, active_tab_id, focused, agent_status, tokens?, worktree?`.

`PopupSize`: JSON integer 0-65535 (outer cells including border) or string matching `^(100|[1-9][0-9]?)%$` (percent of terminal area). `PluginInvocationContext` (all fields optional): `clicked_url, correlation_id, focused_pane_agent, focused_pane_cwd, focused_pane_id, focused_pane_status, invocation_source, link_handler_id, selected_text, tab_id, tab_label, workspace_cwd, workspace_id, workspace_label, worktree {checkout_path, is_linked_worktree, repo_key, repo_name, repo_root}`.

Field semantics (from [app/creation.rs#L307-L356](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/creation.rs#L307), [app/agents.rs#L366-L403](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/agents.rs#L366)):

- **`pane.list` without `workspace_id` lists every pane of every workspace and every tab** ([creation.rs#L163-L200](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/creation.rs#L163)). With `workspace_id`, all tabs of that workspace; unknown id -> error `workspace_not_found` "workspace <id> not found". Source order is workspace, tab, layout pane order.
- Identity fields: `workspace_id`, `tab_id`, `pane_id` (public ids such as `w1:p3`; ids may be renumbered, hence the plan's revalidation), `terminal_id` (stable per terminal, used for agent conflict checks).
- **`PaneInfo.label` = `terminal.manual_label`, the explicit pane label** (set only via `pane.rename`). `PaneInfo.title` is the *presentation/metadata* title (agent-reported), `terminal_title` is the OSC terminal title, `display_agent` is metadata. So `label` cleanly distinguishes explicit from generated; generated values are never put in `label`.
- **`PaneInfo` has no agent name.** The agent name is only in `AgentInfo.name` (`agent.list` / `agent.get` / `agent.rename`). `PaneInfo.agent` is the agent kind label (e.g. "claude"). Join pane to agent by `pane_id` (or `terminal_id`); there is one agent record per agent terminal, i.e. pane-agent is 1:1. A pane with an agent name but no live agent is still in `agent.list` (`is_agent_terminal`).
- `agent_session` is on both, derived from terminal state; `agent_status` same on both.
- `agent.list` covers all workspaces/tabs, only agent terminals; no workspace filter.
- Popups: `popup_pane` lives outside workspace layouts ("layout neutral"), so **a plugin popup never appears in `pane.list`, `agent.list`, `tab.list` or counts** (confirmed by the v0.9.3 test [api/plugins/mod.rs#L2306-L2410](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/api/plugins/mod.rs#L2306): popup open with a workspace of 1 pane, `pane.list` returns only the root pane). Popup focus: `current_plugin_context().focused_pane_id` stays the underlying pane. Plugin panes opened with `overlay`, `split`, `zoomed`, `tab` placement are real layout panes and do appear in `pane.list`; **PaneInfo carries no marker for them** (their `label` is set to the manifest pane title only for popups per [plugins/panes.rs#L38](https://github.com/herdrdev/herdr/blob/v0.9.3/src/app/api/plugins/panes.rs#L38); other placements: see unknowns).
- One popup at a time: opening another gives error `ui_busy` "a popup pane is already open". `width`/`height` with a non-popup placement -> `invalid_params`. popup/overlay with `workspace_id`/`target_pane_id`/`direction` -> `invalid_params`.

Error codes seen in source: `pane_not_found`, `workspace_not_found`, `agent_not_found`, `agent_target_ambiguous`, `invalid_agent_name`, `agent_name_taken`, `agent_launch_pending`, `ui_busy`, `invalid_params`, `plugin_pane_open_failed`, `plugin_pane_not_found`. Repo fixture `errors-0.9.3.json` confirms the envelope (`{"error": {"code", "message"}, "id"}`).

## 9. Popup sizing and placement

`resolve_popup_geometry` ([popup_size.rs#L50](https://github.com/herdrdev/herdr/blob/v0.9.3/src/popup_size.rs#L50)), `area` = `layout.pane_surface` (the active pane surface; excludes sidebar/tab bar, [composition.rs#L561-L596](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/composition.rs#L561)):

- Cells: `Cells(n)` is the **outer** width including the 1-cell border. Percent: `floor(available * percent / 100)` of the area dimension (`PopupSize::resolve`, L16).
- Default when unset: `width = max(area.width / 2, 6)`, `height = max(area.height / 2, 4)`.
- Clamp: `outer_width = max(size, 6)` then `min(area.width)`; `outer_height = max(size, 4)` then `min(area.height)`. If still `< 6` x `< 4` (area too small) -> no popup (spawn error "terminal area too small for popup" when area under 4x4).
- Placement: `outer.x = area.x + (area.width - outer_width) / 2`, `outer.y = area.y + (area.height - outer_height) / 2` (floor), i.e. centered in the pane surface, not the whole client.
- PTY (inner) size: `inner_w = outer_w - 2`; `terminal_cols = inner_w` if `inner_w <= 4`, **else `inner_w - 1`** (reserved right gutter column); `inner_h = outer_h - 2`; inner origin `(outer.x + 1, outer.y + 1)`. So a 56-cell popup -> 54 -> **53 PTY columns**; height 7 -> 5 rows. Test fixture: 100x30 area, `width 80%, height 40%` -> outer 80x12, PTY 77x10.
- Frame: ratatui `Block` with borders ALL, `border_style` fg `accent`, block style bg `panel_bg`, title = the terminal's `manual_label` (the manifest pane `title`, default "popup") ([composition.rs#L572-L579](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/composition.rs#L572), [server/client_shell.rs#L497-L528](https://github.com/herdrdev/herdr/blob/v0.9.3/src/server/client_shell.rs#L497)). The title is the ratatui default top-left position (after the corner), unstyled beyond the block style. The border is the ratatui default plain `Borders::ALL` (`┌─┐`).
- The gutter column and the 1-cell border mean a popup that wants an inner 54x5 content area needs `width = 57` (54+2+1) and `height = 7`, and sits inside an extra border and title that the native overlay lacks.
- Manifest `width`/`height` accept the same `PopupSize`; `plugin.pane.open` params override the manifest (`params.width.or(pane.width)`).

Popup input ([mouse.rs#L884-L914](https://github.com/herdrdev/herdr/blob/v0.9.3/src/client/shell/mouse.rs#L884), [pane_input.rs#L210-L290](https://github.com/herdrdev/herdr/blob/v0.9.3/src/server/pane_input.rs#L210)): mouse events inside the popup's inner rect **are forwarded to the plugin PTY**: Down (any button) always, and Up/Drag via a gesture only when the popup app has enabled mouse reporting (`mouse_reporting`), Moved only with mouse reporting, scroll events always. Events outside the inner rect (including border) are swallowed when a popup is up. Coordinates sent are relative to the inner rect (`column - inner.x`, `row - inner.y`) and encoded by the popup terminal's emulator according to the mode the app enabled (X10/normal/SGR/SGR-pixel). If the plugin never enables mouse modes, `encode_mouse_button` yields nothing and clicks are dropped. Non-mouse input goes to the popup (keys, including Esc); popups do not pass page keys to the host (`host_page_keys=false`). Hence D6 should be achievable with prompt_toolkit `mouse_support=True` -- live check required.

## Unknown / needs live check

1. Whether prompt_toolkit's mouse reporting (DECSET 1000/1002/1003 + 1006) is honored by Herdr's popup terminal and mouse Up arrives (source says yes when the app enables reporting). Also whether Down is delivered when mouse modes are off (source: encode returns empty).
2. Exact rendering of the popup title (position, style) and whether the right-hand gutter column shows `panel_bg` or default background; capture needed.
3. How non-popup plugin panes (`overlay/split/tab`) label themselves in `pane.list` (no `plugin` marker in PaneInfo).
4. Whether `pane.list` order of tabs equals tab order for sync UX; the layout `pane_ids()` order is layout order, not guaranteed visual order.
5. Live confirmation that `agent.rename ... --clear` on an agent with a name and a still-running agent leaves `managed_agent` side effects harmless (clear sets `managed_agent = None`).
6. Whether the daemon applies the name drop in `reconcile_agent_name_owner` after a plugin sync (agent restarts clear names).
7. `auto_switch=true` host appearance is not available to a plugin; fallback is dark. The user's config has it false.
8. Native `ratatui` button glyph width of `↵` in the user's terminal (source assumes 1 cell, `unicode-width`); Python `wcwidth` should agree but verify.
9. Whether newer Herdr versions add an agent rename overlay (not in 0.9.3).

## Contradictions with PLAN.md

- No native agent rename overlay exists; only workspace/tab/pane rename overlays (section 0). Replace-on-type for a prefilled name is not used by any rename overlay.
- The overlay has no error display, no validation, and no clear-request: save on empty sends `pane.rename` with `""` (pane) or nothing (workspace/tab); clear button/Ctrl+C only empties the field. Errors layout is the plugin's own design.
- Mouse: native activation is press-only; cancel is "any click outside save/clear", including on the cancel button itself and on the panel. PLAN.md says buttons are separate targets.
- Popup gutter is exactly one reserved column when inner width > 4 (matches plan). A 56x7 popup yields a 53x5 PTY, so a 54x5 content block does not fit: need `width >= 57`.
- `PaneInfo` has no agent name; the plan's pane list plus agent list join is required (pane_id/terminal_id).
- Clearing an agent name also sets `managed_agent = None` and fails with `agent_not_found` if no agent is currently running.
- Default theme name is `catppuccin`.

## Appendix: all built-in palettes (hex)

| theme | accent | panel_bg | sidebar_bg | active_row_bg | selection_bg | surface0 | surface1 | surface_dim | overlay0 | overlay1 | text | subtext0 | mauve | green | yellow | red | blue | teal | peach |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| catppuccin | `#89b4fa` | `#181825` | `Reset` | `#1e1e2e` | `#313244` | `#313244` | `#45475a` | `#1e1e2e` | `#6c7086` | `#7f849c` | `#cdd6f4` | `#a6adc8` | `#cba6f7` | `#a6e3a1` | `#f9e2af` | `#f38ba8` | `#89b4fa` | `#94e2d5` | `#fab387` |
| catppuccin-latte | `#1e66f5` | `#eff1f5` | `Reset` | `#e6e9ef` | `#bdd0f5` | `#ccd0da` | `#bcc0cc` | `#e6e9ef` | `#9ca0b0` | `#8c8fa1` | `#4c4f69` | `#6c6f85` | `#8839ef` | `#40a02b` | `#df8e1d` | `#d20f39` | `#1e66f5` | `#179299` | `#fe640b` |
| terminal | `Blue` | `Reset` | `Reset` | `DarkGray` | `Reset` | `Reset` | `DarkGray` | `DarkGray` | `Gray` | `White` | `Reset` | `Gray` | `Gray` | `Green` | `Yellow` | `LightRed` | `Blue` | `Cyan` | `Yellow` |
| tokyo-night | `#7aa2f7` | `#1a1b26` | `Reset` | `#232636` | `#2d3650` | `#24283b` | `#414868` | `#1a1b26` | `#565f89` | `#697196` | `#c0caf5` | `#a9b1d6` | `#bb9af7` | `#9ece6a` | `#e0af68` | `#f7768e` | `#7aa2f7` | `#7dcfff` | `#ff9e64` |
| tokyo-night-day | `#2e7de9` | `#e1e2e7` | `Reset` | `#d2d3da` | `#b6cae7` | `#c4c8da` | `#a8aecb` | `#d2d3da` | `#8990b3` | `#68709a` | `#3760bf` | `#6172b0` | `#7847bd` | `#587539` | `#8c6c3e` | `#f52a65` | `#2e7de9` | `#118c74` | `#b15c00` |
| dracula | `#bd93f9` | `#282a36` | `Reset` | `#373c52` | `#463f5d` | `#44475a` | `#6272a4` | `#282a36` | `#6272a4` | `#828cb4` | `#f8f8f2` | `#d2d2dc` | `#ff79c6` | `#50fa7b` | `#f1fa8c` | `#ff5555` | `#8be9fd` | `#8be9fd` | `#ffb86c` |
| nord | `#88c0d0` | `#2e3440` | `Reset` | `#434c5e` | `#40505d` | `#3b4252` | `#434c5e` | `#2e3440` | `#4c566a` | `#646e82` | `#eceff4` | `#d8dee9` | `#b48ead` | `#a3be8c` | `#ebcb8b` | `#bf616a` | `#81a1c1` | `#8fbcbb` | `#d08770` |
| gruvbox | `#d79921` | `#282828` | `Reset` | `#323130` | `#4b3f27` | `#3c3836` | `#504945` | `#282828` | `#928374` | `#a89984` | `#ebdbb2` | `#d5c4a1` | `#d3869b` | `#b8bb26` | `#fabd2f` | `#fb4934` | `#83a598` | `#8ec07c` | `#fe8019` |
| gruvbox-light | `#076678` | `#fbf1c7` | `Reset` | `#f2e5bc` | `#ebdbb2` | `#ebdbb2` | `#d5c4a1` | `#f2e5bc` | `#928374` | `#7c6f64` | `#3c3836` | `#504945` | `#8f3f71` | `#79740e` | `#b57614` | `#9d0006` | `#076678` | `#427b58` | `#af3a03` |
| one-dark | `#61afef` | `#282c34` | `Reset` | `#313640` | `#334659` | `#2c313a` | `#3e4451` | `#282c34` | `#5c6370` | `#737a87` | `#abb2bf` | `#969ca8` | `#c678dd` | `#98c379` | `#e5c07b` | `#e06c75` | `#61afef` | `#56b6c2` | `#d19a66` |
| one-light | `#4078f2` | `#fafafa` | `Reset` | `#d8dbe2` | `#cddbf8` | `#f0f0f1` | `#e5e5e6` | `#f5f5f6` | `#a0a1a7` | `#686b77` | `#383a42` | `#686b77` | `#a626a4` | `#50a14f` | `#c18401` | `#e45649` | `#4078f2` | `#0184bc` | `#986801` |
| solarized | `#268bd2` | `#002b36` | `Reset` | `#164b57` | `#083e55` | `#073642` | `#586e75` | `#002b36` | `#586e75` | `#657b83` | `#93a1a1` | `#839496` | `#d33682` | `#859900` | `#b58900` | `#dc322f` | `#268bd2` | `#2aa198` | `#cb4b16` |
| solarized-light | `#268bd2` | `#fdf6e3` | `Reset` | `#eee8d5` | `#c9dcdf` | `#eee8d5` | `#93a1a1` | `#eee8d5` | `#93a1a1` | `#586e75` | `#657b83` | `#839496` | `#d33682` | `#859900` | `#b58900` | `#dc322f` | `#268bd2` | `#2aa198` | `#cb4b16` |
| kanagawa | `#7e9cd8` | `#1f1f28` | `Reset` | `#363646` | `#32384b` | `#2a2a37` | `#363646` | `#1f1f28` | `#727169` | `#87867d` | `#dcd7ba` | `#c8c3aa` | `#957fb8` | `#76946a` | `#c0a36e` | `#c34043` | `#7e9cd8` | `#7fb4ca` | `#ffa066` |
| kanagawa-lotus | `#4d699b` | `#f2ecbc` | `Reset` | `#d5cea3` | `#dcd5ac` | `#dcd5ac` | `#c9cbd1` | `#d5cea3` | `#a09cac` | `#8a8980` | `#545464` | `#43436c` | `#624c83` | `#6f894e` | `#77713f` | `#c84053` | `#4d699b` | `#4e8ca2` | `#cc6d00` |
| rose-pine | `#c4a7e7` | `#191724` | `Reset` | `#26233a` | `#3b344b` | `#1f1d2e` | `#26233a` | `#26233a` | `#6e6a86` | `#908caa` | `#e0def4` | `#c8c5dc` | `#c4a7e7` | `#31748f` | `#f6c177` | `#eb6f92` | `#31748f` | `#9ccfd8` | `#ea9a97` |
| rose-pine-dawn | `#907aa9` | `#faf4ed` | `Reset` | `#e3d9cf` | `#f2e9e1` | `#f2e9e1` | `#fffaf3` | `#f2e9e1` | `#9893a5` | `#797593` | `#464261` | `#797593` | `#907aa9` | `#286983` | `#ea9d34` | `#b4637a` | `#286983` | `#56949f` | `#d7827e` |
| vesper | `#ffc799` | `#1a1a1a` | `Reset` | `#101010` | `#232323` | `#232323` | `#282828` | `#101010` | `#5c5c5c` | `#7e7e7e` | `#ffffff` | `#a0a0a0` | `#ffd1a8` | `#99ffe4` | `#ffc799` | `#ff8080` | `#b0b0b0` | `#66ddcc` | `#ffc799` |
