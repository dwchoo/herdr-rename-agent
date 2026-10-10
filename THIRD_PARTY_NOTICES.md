# Third-party notices

## Herdr

Portions of this project are derived from Herdr, an Apache-2.0 licensed terminal workspace manager.

- Project: <https://github.com/herdrdev/herdr>
- Version: v0.9.3, commit `7b116c05bfda646af39d2524c54e70c751f57ee8`
- License: Apache License 2.0 (full text in the upstream repository; this project's own [LICENSE](LICENSE) is the same license)

Herdr is a binary-only crate, so nothing is imported as a library. The following logic was read in the upstream source and re-implemented in Python. No upstream file is copied verbatim.

| Upstream source | Ported to | What was ported |
| --- | --- | --- |
| `src/client/shell/text_editor.rs` | `src/herdr_agent_rename/editor.py` | Single-line editor model: grapheme-cluster cursor movement, word movement rules, kill/yank keys and the single local kill buffer, CR/LF/Tab-to-space insertion, replace-on-type flag, viewport rule (at most `width - 1` cells before the cursor). |
| `src/client/shell/overlays.rs` (rename overlay renderer and its `panel`/`row`/`button` helpers) | `src/herdr_agent_rename/ui/rename.py` | Layout of the pane rename overlay (title, input and button rows, 54x5 content, button widths 8/10/12 with 2-cell gaps), button labels (` ↵ save `, ` ^c clear `, ` esc cancel `) and styles. |
| `src/client/shell/overlay_input.rs`, `src/client/shell/mouse.rs` | `src/herdr_agent_rename/ui/rename.py` | Key routing (Enter saves, Esc cancels, Ctrl+C clears the field) and press-activated mouse behavior (save, clear, anything else cancels). |
| `src/app/state.rs` (palettes) and `src/app/mod.rs`, `src/config/theme.rs` (theme resolution) | `src/herdr_agent_rename/theme.py` | Palette token values of the built-in themes, theme name aliases and sibling pairs, `[theme]` / `[theme.custom]` / legacy `[ui] accent` override order, and color string parsing. |

## Local changes

- Python instead of Rust. Cursor positions index a Python `str` at grapheme boundaries (segmented with the `regex` package, `\X`). Cell widths come from `wcwidth` instead of `unicode-width`.
- Control characters other than CR, LF and Tab become U+FFFD instead of being dropped, so validation still rejects them.
- Keys are prompt_toolkit identifiers instead of crossterm key events. Ctrl+Backspace is not bound separately because terminals do not distinguish it.
- The rename dialog adds a status/error row, a busy state, a resize message for small terminals, and the title `rename agent`. It runs inside a Herdr plugin popup, which draws its own border.
- Themes: when `auto_switch = true`, the dark variant is assumed because a plugin cannot read the host terminal appearance.
- The workspace sync dialog, planner and executor are original to this project and use only Herdr's public CLI/API.

Source headers: `editor.py`, `theme.py` and `ui/rename.py` name the upstream files, version `v0.9.3`, commit and license.

## Python dependencies

Installed from hash-locked `requirements.lock`; not vendored in this repository: `prompt_toolkit` (BSD-3-Clause), `wcwidth` (MIT), `regex` (Apache-2.0), `tomlkit` (MIT). Test-only: `pytest`, `pyte`, `Pillow`.
