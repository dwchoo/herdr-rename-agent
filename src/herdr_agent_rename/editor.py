"""Single-line text editor model for the rename dialog.

Ported from herdrdev/herdr v0.9.3 ``src/client/shell/text_editor.rs``
(commit 7b116c05bfda646af39d2524c54e70c751f57ee8), Apache-2.0.

Local changes:

* Python port; the cursor is an index into a ``str`` (code points), always at
  a grapheme-cluster boundary (segmented with ``regex`` ``\\X``).
* Control characters other than CR, LF and Tab are replaced with U+FFFD
  instead of being dropped (decision D7), so validation still rejects them.
* Keys are prompt_toolkit-style identifiers (see ``KEY_OPERATIONS``) instead
  of crossterm key events.
* Cell widths come from ``wcwidth`` instead of ``unicode-width``; graphemes
  with no printable width (``wcswidth`` < 0) count as one cell.

Keys the terminal cannot tell apart: Backspace and Ctrl+H arrive as the same
byte, which prompt_toolkit reports as ``c-h`` (``backspace`` is an alias), so
both delete one grapheme. Ctrl+Backspace is not distinguishable from them (or
from Ctrl+W, depending on the terminal) and therefore is not bound separately;
Alt+Backspace (``escape c-h`` / ``escape backspace``) kills a word.
Enter, Esc and Ctrl+C belong to the overlay and are not handled here.

Word movement quirk kept from Herdr: whitespace never ends a run, so a word
motion absorbs the whitespace next to it and the word on its far side.
"""

import unicodedata
from collections.abc import Callable

import regex
from wcwidth import wcswidth

REPLACEMENT = "�"
_SPACE_CONTROLS = {"\n", "\t"}


def _graphemes(text: str) -> list[str]:
    return regex.findall(r"\X", text)


def _width(grapheme: str) -> int:
    width = wcswidth(grapheme)
    return 1 if width < 0 else width


def _word_class(grapheme: str) -> int:
    char = grapheme[0] if grapheme else " "
    if char.isspace():
        return 0
    if char.isalnum() or char == "_":
        return 1
    return 2


def normalize_input(text: str) -> str:
    """CRLF, CR, LF and Tab become one space; other controls become U+FFFD."""
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if char == "\r":
            if text[index : index + 1] == "\n":
                index += 1
            out.append(" ")
        elif char in _SPACE_CONTROLS:
            out.append(" ")
        elif unicodedata.category(char) == "Cc":
            out.append(REPLACEMENT)
        else:
            out.append(char)
    return "".join(out)


class TextEditor:
    """Text, grapheme-aligned cursor, replace-on-type flag and kill buffer."""

    def __init__(self, text: str = "", replace_on_type: bool = False) -> None:
        self.text = ""
        self.cursor = 0
        self.replace_on_type = False
        self.killed = ""
        self.insert(text)
        self.replace_on_type = replace_on_type

    def clear(self) -> None:
        self.text = ""
        self.cursor = 0
        self.replace_on_type = False

    def trim_and_accept(self) -> None:
        """Strip surrounding whitespace, keeping the cursor on the same text."""
        leading = len(self.text) - len(self.text.lstrip())
        self.text = self.text.strip()
        self.cursor = min(max(self.cursor - leading, 0), len(self.text))
        self.replace_on_type = False
        self._repair_cursor()

    def _repair_cursor(self) -> None:
        """Snap forward to a grapheme boundary after an edit joined clusters."""
        index = 0
        for grapheme in _graphemes(self.text):
            if index >= self.cursor:
                self.cursor = index
                return
            index += len(grapheme)
        self.cursor = len(self.text)

    def insert(self, text: str) -> bool:
        normalized = normalize_input(text)
        if not normalized:
            return False
        content_changed = not self.replace_on_type or self.text != normalized
        if self.replace_on_type:
            self.clear()
        self.text = self.text[: self.cursor] + normalized + self.text[self.cursor :]
        self.cursor += len(normalized)
        self._repair_cursor()
        return content_changed

    def _previous(self) -> int:
        graphemes = _graphemes(self.text[: self.cursor])
        return self.cursor - len(graphemes[-1]) if graphemes else 0

    def _next(self) -> int:
        graphemes = _graphemes(self.text[self.cursor :])
        return self.cursor + (len(graphemes[0]) if graphemes else 0)

    def _word_boundary(self, backward: bool) -> int:
        boundary = self.cursor
        run = 0
        if backward:
            index = self.cursor
            for grapheme in reversed(_graphemes(self.text[: self.cursor])):
                index -= len(grapheme)
                current = _word_class(grapheme)
                if run != 0 and current != run:
                    break
                run = current
                boundary = index
        else:
            index = self.cursor
            for grapheme in _graphemes(self.text[self.cursor :]):
                current = _word_class(grapheme)
                if run != 0 and current != run:
                    break
                run = current
                index += len(grapheme)
                boundary = index
        return boundary

    def _remove(self, start: int, end: int, kill: bool) -> None:
        self.replace_on_type = False
        if start == end:
            return
        if kill:
            self.killed = self.text[start:end]
        self.text = self.text[:start] + self.text[end:]
        self.cursor = start
        self._repair_cursor()

    def _move(self, position: int) -> None:
        self.cursor = position
        self.replace_on_type = False

    def move_left(self) -> None:
        self._move(self._previous())

    def move_right(self) -> None:
        self._move(self._next())

    def move_home(self) -> None:
        self._move(0)

    def move_end(self) -> None:
        self._move(len(self.text))

    def move_word_left(self) -> None:
        self._move(self._word_boundary(True))

    def move_word_right(self) -> None:
        self._move(self._word_boundary(False))

    def backspace(self) -> None:
        if self.replace_on_type:
            self.clear()
        else:
            self._remove(self._previous(), self.cursor, False)

    def delete(self) -> None:
        self._remove(self.cursor, self._next(), False)

    def kill_to_start(self) -> None:
        self._remove(0, self.cursor, True)

    def kill_to_end(self) -> None:
        self._remove(self.cursor, len(self.text), True)

    def kill_word_left(self) -> None:
        self._remove(self._word_boundary(True), self.cursor, True)

    def kill_word_right(self) -> None:
        self._remove(self.cursor, self._word_boundary(False), True)

    def yank(self) -> bool:
        return self.insert(self.killed)

    def viewport(self, width: int) -> tuple[str, int]:
        """Visible slice and cursor column: at most ``width - 1`` cells precede
        the cursor, and the remaining cells show the text after it."""
        if width <= 0:
            return "", 0
        start = self.cursor
        cells = 0
        index = self.cursor
        for grapheme in reversed(_graphemes(self.text[: self.cursor])):
            following = cells + _width(grapheme)
            if following >= width:
                break
            index -= len(grapheme)
            start = index
            cells = following
        end = self.cursor
        used = cells
        for grapheme in _graphemes(self.text[self.cursor :]):
            used += _width(grapheme)
            if used > width:
                break
            end += len(grapheme)
        return self.text[start:end], cells

    def handle_key(self, key: str) -> bool | None:
        """Apply a key; ``None`` if unbound, else whether text or length changed.

        A single printable character is inserted. Mirrors Herdr: the result is
        true when the content changed (movement alone reports false).
        """
        if len(key) == 1:
            if unicodedata.category(key) == "Cc":
                return None
            return self.insert(key) or False
        operation = KEY_OPERATIONS.get(key)
        if operation is None:
            return None
        previous_len = len(self.text)
        changed = operation(self)
        return bool(changed) or len(self.text) != previous_len


KEY_OPERATIONS: dict[str, Callable[[TextEditor], object]] = {
    "left": TextEditor.move_left,
    "c-b": TextEditor.move_left,
    "right": TextEditor.move_right,
    "c-f": TextEditor.move_right,
    "home": TextEditor.move_home,
    "c-a": TextEditor.move_home,
    "end": TextEditor.move_end,
    "c-e": TextEditor.move_end,
    "escape b": TextEditor.move_word_left,
    "escape f": TextEditor.move_word_right,
    "backspace": TextEditor.backspace,
    "c-h": TextEditor.backspace,
    "delete": TextEditor.delete,
    "c-d": TextEditor.delete,
    "c-u": TextEditor.kill_to_start,
    "c-k": TextEditor.kill_to_end,
    "c-w": TextEditor.kill_word_left,
    "escape backspace": TextEditor.kill_word_left,
    "escape c-h": TextEditor.kill_word_left,
    "escape d": TextEditor.kill_word_right,
    "c-y": TextEditor.yank,
}
