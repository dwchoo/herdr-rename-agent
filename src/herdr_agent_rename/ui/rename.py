"""Rename dialog: the layout and input model of Herdr's pane rename overlay.

Reference: ``src/client/shell/overlays.rs`` (rename overlay) in Herdr v0.9.3.
The overlay is drawn inside the plugin popup's content area (54x5 for a popup
of manifest width 57 and height 7): title on row 0, the input on row 2 and the
three buttons on row 3, with the exact native labels and offsets.

Intentional differences from the native overlay:

* Row 4 (and further rows, when the terminal is taller) shows a one-line
  status or error instead of nothing. A message longer than the line scrolls
  as a marquee (or wraps when extra rows exist), so it is fully readable
  without hiding the controls. The entered text is kept after an error.
* While a request runs, all input is locked and the status says so.
* The save button is dimmed when the controller cannot submit.
* The title reads "rename agent"; the popup frame outside it is Herdr's own.
* Too-small terminals show a resize message instead of the dialog.
"""

from __future__ import annotations

import asyncio
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import DynamicContainer, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import ColorDepth, Output
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from ..controller import Controller
from ..editor import TextEditor, _graphemes, _width
from ..theme import Theme, load_theme
from .common import MIN_WIDTH, display_text, pad_cells, resize_text, wrap_cells

PANEL_ROWS = 5
TITLE = "rename agent"
TITLE_ROW, INPUT_ROW, BUTTON_ROW, STATUS_ROW = 0, 2, 3, 4
BUTTONS = (
    (" ↵ save ", "primary"),
    (" ^c clear ", "secondary"),
    (" esc cancel ", "secondary"),
)
BUTTON_GAP = 2
MARQUEE_INTERVAL = 0.15
MARQUEE_HOLD = 8
_NOT_EDIT_KEYS = frozenset(
    {"escape", "c-c", "c-m", "c-j", "<bracketed-paste>", "<vt100-mouse-event>",
     "<windows-mouse-event>", "<cursor-position-response>", "<ignore>", "<sigint>", "<any>"}
)


def button_rects(width: int) -> list[tuple[int, int]]:
    """Start and end columns (end exclusive) of save, clear and cancel."""
    total = sum(get_cwidth(label) for label, _ in BUTTONS) + BUTTON_GAP * (len(BUTTONS) - 1)
    x = max(0, (width - total) // 2)
    rects = []
    for label, _ in BUTTONS:
        rects.append((x, x + get_cwidth(label)))
        x += get_cwidth(label) + BUTTON_GAP
    return rects


class RenameControl(FormattedTextControl):
    def __init__(self, dialog: RenameDialog) -> None:
        super().__init__(text="", focusable=True, show_cursor=True)
        self.dialog = dialog

    def create_content(self, width: int, height: int) -> UIContent:
        return self.dialog.render(width, height)

    def mouse_handler(self, mouse_event: MouseEvent) -> Any:
        # Native activation is on press of the left button only.
        if (
            mouse_event.event_type != MouseEventType.MOUSE_DOWN
            or mouse_event.button != MouseButton.LEFT
        ):
            return NotImplemented
        self.dialog.press(mouse_event.position.x, mouse_event.position.y)
        return None


class RenameDialog:
    def __init__(
        self,
        controller: Controller,
        *,
        input: Input | None = None,
        output: Output | None = None,
        theme: Theme | None = None,
    ) -> None:
        self.controller = controller
        self.theme = theme or load_theme()
        self.editor = TextEditor(controller.initial_agent_name, replace_on_type=False)
        self._working = False
        self._width = 0
        self._tick = 0
        self._marquee_text = ""
        self._marquee_active = False
        self.control = RenameControl(self)
        self._resize_control = FormattedTextControl(self._resize_text, focusable=True)
        self._main = Window(self.control, style="class:panel", wrap_lines=False)
        self._resize = Window(self._resize_control, style="class:panel", wrap_lines=True)
        root = DynamicContainer(lambda: self._main if self.has_room else self._resize)
        self.app: Application[bool] = Application(
            layout=Layout(root, focused_element=self._main),
            key_bindings=self._bindings(),
            style=Style.from_dict(self.theme.styles()),
            color_depth=ColorDepth.DEPTH_24_BIT,
            full_screen=True,
            mouse_support=True,
            erase_when_done=True,
            before_render=self._handle_resize,
            input=input,
            output=output,
        )
        # Esc cancels, while escape sequences still need their remaining bytes.
        self.app.ttimeoutlen = 0.05
        self.app.timeoutlen = 0.05

    # State ---------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._working or self.controller.busy

    @property
    def has_room(self) -> bool:
        app = getattr(self, "app", None)
        if app is None:
            return True
        size = app.output.get_size()
        return size.columns >= MIN_WIDTH and size.rows >= PANEL_ROWS

    @property
    def editable(self) -> bool:
        return not self.busy and self.has_room

    @property
    def can_save(self) -> bool:
        return self.editable and self.controller.can_submit

    # Rendering -----------------------------------------------------------

    def _status(self, width: int, rows: int) -> tuple[str, list[str]]:
        state = str(self.controller.state)
        self._marquee_active = False
        if self.busy:
            text = "Renaming agent..." if state.endswith("submitting") else "Checking the target..."
            return "class:hint", [text]
        message = display_text(self.controller.message or "")
        if not message:
            return "class:hint", []
        style = "class:error" if state.endswith("error") else "class:hint"
        wrapped = wrap_cells(message, width)
        if len(wrapped) <= rows:
            return style, wrapped
        # Too long for the free rows: scroll the single line instead of
        # truncating, so the whole message can be read.
        self._marquee_active = True
        if message != self._marquee_text:
            self._marquee_text, self._tick = message, 0
        cycle = message + "   "
        offset = max(0, self._tick - MARQUEE_HOLD) % len(cycle)
        line, cells = "", 0
        while cells < width:
            char = cycle[offset % len(cycle)]
            offset += 1
            if cells + get_cwidth(char) > width:
                break
            line += char
            cells += get_cwidth(char)
        return style, [line]

    def render(self, width: int, height: int) -> UIContent:
        self._width = width
        blank = ("class:panel", " " * width)
        lines: list[list[tuple[str, str]]] = [[blank] for _ in range(max(height, PANEL_ROWS))]
        lines[TITLE_ROW] = [("class:title", pad_cells(TITLE, width))]
        visible, cells = self.editor.viewport(width - 1)
        before = 0
        used = 0
        for grapheme in _graphemes(visible):
            if used >= cells:
                break
            used += _width(grapheme)
            before += len(grapheme)
        lines[INPUT_ROW] = [("class:input", pad_cells(" " + visible, width))]
        row: list[tuple[str, str]] = []
        position = 0
        for (label, style), (start, end) in zip(BUTTONS, button_rects(width)):
            if start > position:
                row.append(("class:panel", " " * (start - position)))
            if style == "primary" and not self.controller.can_submit:
                style = "disabled"
            row.append((f"class:{style}", label))
            position = end
        row.append(("class:panel", " " * max(0, width - position)))
        lines[BUTTON_ROW] = row
        style, texts = self._status(width, max(1, height - STATUS_ROW))
        for index, text in enumerate(texts[: max(1, height - STATUS_ROW)]):
            lines[STATUS_ROW + index] = [(style, pad_cells(text, width))]
        return UIContent(
            get_line=lambda number: lines[number],
            line_count=len(lines),
            cursor_position=Point(x=1 + before, y=INPUT_ROW),
            show_cursor=self.editable,
        )

    def _resize_text(self) -> str:
        return resize_text(self.app.output.get_size(), self.busy)

    def _handle_resize(self, app: Application[bool]) -> None:
        target = self._main if self.has_room else self._resize
        if app.layout.current_window is not target:
            app.layout.focus(target)

    # Actions -------------------------------------------------------------

    def _cancel(self) -> None:
        if not self.busy and self.controller.cancel():
            self.app.exit(result=False)

    def _clear(self) -> None:
        if self.editable:
            self.editor.clear()
            self.app.invalidate()

    def _save(self) -> None:
        if not self.can_save:
            return
        # Latch before scheduling: several Enter keys can arrive in one batch.
        self._working = True
        self.app.invalidate()
        self.app.create_background_task(self._submit())

    async def _submit(self) -> None:
        try:
            # The controller trims, and decides whether an empty name clears it.
            if await self.controller.submit(self.editor.text):
                self.app.exit(result=True)
        except Exception as exc:
            self.app.exit(exception=exc)
        finally:
            self._working = False
            self.app.invalidate()

    def press(self, x: int, y: int) -> None:
        """Left-button press at a dialog cell: save, clear, else cancel."""
        if self.busy or not self.has_room:
            return
        save, clear, _ = button_rects(self._width)
        if y == BUTTON_ROW and save[0] <= x < save[1]:
            self._save()
        elif y == BUTTON_ROW and clear[0] <= x < clear[1]:
            self._clear()
        else:
            self._cancel()

    async def _marquee(self) -> None:
        while True:
            await asyncio.sleep(MARQUEE_INTERVAL)
            if self._marquee_active:
                self._tick += 1
                self.app.invalidate()

    # Keys ----------------------------------------------------------------

    def _edit(self, name: str) -> None:
        if self.editable:
            self.editor.handle_key(name)
            self.app.invalidate()

    def _bindings(self) -> KeyBindings:
        from prompt_toolkit.keys import Keys

        keys = KeyBindings()

        @keys.add("c-m")
        def _enter(event: Any) -> None:
            self._save()

        @keys.add("c-j")
        def _linefeed(event: Any) -> None:
            self._save()

        @keys.add("escape")
        @keys.add(Keys.SIGINT)
        def _escape(event: Any) -> None:
            self._cancel()

        @keys.add("c-c")
        def _control_c(event: Any) -> None:
            self._clear()

        @keys.add(Keys.BracketedPaste, eager=True)
        def _paste(event: Any) -> None:
            if self.editable:
                self.editor.insert(event.data)
                self.app.invalidate()

        def named(press: Any) -> str:
            return press.key.value if isinstance(press.key, Keys) else press.key

        def generic(event: Any) -> None:
            self._edit(" ".join(named(press) for press in event.key_sequence))

        # Bind every key explicitly so prompt_toolkit's Emacs bindings never run.
        for key in Keys:
            if key.value not in _NOT_EDIT_KEYS:
                keys.add(key)(generic)
        keys.add(Keys.Escape, Keys.Any)(generic)

        @keys.add(Keys.Any)
        def _text(event: Any) -> None:
            data = event.key_sequence[-1].data
            if self.editable and data:
                if len(data) == 1:
                    self.editor.handle_key(data)
                else:
                    self.editor.insert(data)
                self.app.invalidate()

        return keys

    async def run_async(self) -> bool:
        """Restore terminal modes on all exits, including unexpected failures."""
        return bool(
            await self.app.run_async(
                pre_run=lambda: self.app.create_background_task(self._marquee()),
                set_exception_handler=False,
            )
        )
