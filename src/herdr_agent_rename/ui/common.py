"""Helpers shared by the popup dialogs."""

from __future__ import annotations

import unicodedata

from prompt_toolkit.data_structures import Size
from prompt_toolkit.utils import get_cwidth

RESIZE_MESSAGE = "Enlarge the terminal to continue. Press Esc to cancel."
MIN_WIDTH = 40


def display_text(value: str) -> str:
    """Make controls visible without letting API text change terminal state."""
    replacements = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    result = []
    for char in value:
        if char in replacements:
            result.append(replacements[char])
        elif unicodedata.category(char).startswith("C"):
            number = ord(char)
            result.append(f"\\x{number:02x}" if number < 256 else f"\\u{number:04x}")
        else:
            result.append(char)
    return "".join(result)


def wrap_cells(text: str, width: int) -> list[str]:
    """Wrap by terminal cells (not code points), preferring spaces."""
    width = max(1, width)
    wrapped = []
    for paragraph in text.splitlines():
        while paragraph:
            cells = 0
            cut = 0
            for char in paragraph:
                next_cells = cells + get_cwidth(char)
                if next_cells > width:
                    break
                cut += 1
                cells = next_cells
            cut = max(1, cut)
            if cut < len(paragraph):
                space = paragraph.rfind(" ", 0, cut + 1)
                if space > 0:
                    cut = space
            wrapped.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip()
    return wrapped


def bounded_text(text: str, width: int, lines: int) -> str:
    width = max(1, width)
    wrapped = wrap_cells(text, width)
    if len(wrapped) > lines:
        wrapped = wrapped[:lines]
        while wrapped[-1] and get_cwidth(wrapped[-1]) > max(0, width - 3):
            wrapped[-1] = wrapped[-1][:-1]
        wrapped[-1] += "." * min(width, 3)
    return "\n".join(wrapped)


def pad_cells(text: str, width: int) -> str:
    return text + " " * max(0, width - get_cwidth(text))


def resize_text(size: Size, busy: bool) -> str:
    """Message for a terminal that is too small, shrinking to fit any size."""
    message = "Renaming agent... Enlarge the terminal to see the result." if busy else RESIZE_MESSAGE
    bounded = bounded_text(message, max(1, size.columns), max(1, size.rows))
    if not bounded.endswith("." * min(max(1, size.columns), 3)):
        return message
    if busy:
        return "Working" if size.columns >= 7 else "Wait"[: size.columns]
    if size.rows >= 2 and size.columns >= 8:
        return "Resize\nEsc exit"
    if size.rows >= 2 and size.columns >= 4:
        return "Size\nEsc"
    return "Esc: cancel" if size.columns >= 11 else "Esc exit" if size.columns >= 8 else "Esc"[: size.columns]
