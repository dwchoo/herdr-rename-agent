"""Compact, keyboard-driven terminal dialogs inside Herdr's popup border."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    DynamicContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl, UIContent
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.output import Output
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

if TYPE_CHECKING:
    from .controller import Controller


RESIZE_MESSAGE = "Enlarge the terminal to continue. Press Esc to cancel."
MIN_WIDTH = 40
MIN_EDIT_HEIGHT = 9
MIN_SYNC_HEIGHT = 12


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


class NameControl(BufferControl):
    """Display the untouched buffer on one horizontally scrolling line.

    Invalid pasted newlines are shown as visible escapes. The document keeps
    the original text, so validation never sees a silently modified name.
    """

    def create_content(self, width: int, height: int) -> UIContent:
        text = display_text(self.buffer.text)
        cursor = len(display_text(self.buffer.text[: self.buffer.cursor_position]))
        return UIContent(
            get_line=lambda _: [("class:input", text + " ")],
            line_count=1,
            cursor_position=Point(x=cursor, y=0),
            show_cursor=True,
        )


class ReadOnlyNameControl(FormattedTextControl):
    """An exact source name with a movable viewing position, never an editor."""

    def __init__(self, dialog: Dialog, value: str) -> None:
        super().__init__(text="", focusable=False)
        self.dialog = dialog
        self.value = display_text(value)

    def create_content(self, width: int, height: int) -> UIContent:
        return UIContent(
            get_line=lambda _: [("", self.value + " ")],
            line_count=1,
            cursor_position=Point(x=min(self.dialog._name_position, len(self.value)), y=0),
            show_cursor=False,
        )


class ActionButton:
    def __init__(self, dialog: Dialog, label: str, affirmative: bool) -> None:
        self.dialog = dialog
        self.label = label
        self.affirmative = affirmative
        self.control = FormattedTextControl(self._text, focusable=True)
        self.window = Window(self.control, width=len(label) + 4, height=1)

    @property
    def enabled(self) -> bool:
        if self.dialog.busy:
            return False
        return not self.affirmative or (
            self.dialog.has_room and self.dialog.controller.can_submit
        )

    def _text(self) -> list[tuple[str, str]]:
        style = "class:button"
        if not self.enabled:
            style += " class:disabled"
        elif self.dialog.app.layout.has_focus(self.control):
            style += " class:button.focused"
        return [(style, f"[ {self.label} ]")]


class Dialog:
    """One popup application; the controller owns all target/write decisions."""

    def __init__(
        self,
        controller: Controller,
        *,
        input: Input | None = None,
        output: Output | None = None,
    ) -> None:
        self.controller = controller
        self._working = False
        self._was_small = False
        self._previous_focus: Any = None
        self._name_position = 0
        self.name_controls: list[ReadOnlyNameControl] = []
        self.name_windows: list[Window] = []
        self.buffer = Buffer(
            name="agent-name",
            multiline=False,
            document=Document(
                controller.initial_agent_name,
                cursor_position=len(controller.initial_agent_name),
            ),
            read_only=Condition(lambda: self.busy or not self.has_room),
        )
        self.input_control = NameControl(buffer=self.buffer)
        self.input_window = Window(
            self.input_control,
            height=1,
            wrap_lines=False,
            style="class:input",
        )
        sync = controller.mode == "sync"
        self.apply_button = ActionButton(self, "Yes" if sync else "Apply", True)
        self.cancel_button = ActionButton(self, "No" if sync else "Cancel", False)
        self.buttons = (self.apply_button, self.cancel_button)
        self._resize_control = FormattedTextControl(self._resize_text, focusable=True)
        self._resize_layout = Window(self._resize_control, wrap_lines=True)
        self._normal_layout = self._build_layout(sync)
        root = DynamicContainer(
            lambda: self._normal_layout if self.has_room else self._resize_layout
        )
        self.app: Application[bool] = Application(
            layout=Layout(root, focused_element=self.cancel_button.control if sync else self.input_control),
            key_bindings=self._bindings(),
            style=Style.from_dict(
                {
                    "input": "reverse",
                    "button": "",
                    "button.focused": "reverse bold",
                    "disabled": "ansibrightblack",
                    "error": "ansired",
                    "hint": "ansibrightblack",
                }
            ),
            full_screen=True,
            mouse_support=False,
            erase_when_done=True,
            before_render=self._handle_resize,
            input=input,
            output=output,
        )
        # Esc is a cancellation key, while VT100 arrow/Shift+Tab sequences
        # still need the parser to receive their remaining bytes.
        self.app.ttimeoutlen = 0.05
        self.app.timeoutlen = 0.05

    @property
    def busy(self) -> bool:
        return self._working or self.controller.busy

    @property
    def has_room(self) -> bool:
        # During Application construction, use the requested output if present;
        # thereafter the application owns the terminal connection.
        app = getattr(self, "app", None)
        if app is None:
            return True
        size = app.output.get_size()
        height = MIN_SYNC_HEIGHT if self.controller.mode == "sync" else MIN_EDIT_HEIGHT
        return size.columns >= MIN_WIDTH and size.rows >= height

    def _label(self, text: Any, *, height: int = 1, style: str = "") -> Window:
        return Window(
            FormattedTextControl(text),
            height=height,
            wrap_lines=True,
            style=style,
        )

    def _build_layout(self, sync: bool) -> VSplit:
        spacer = lambda: Window(height=1)
        # Herdr draws the manifest entrypoint title in the outer popup border.
        rows = []
        if sync:
            rows += [
                self._label("The pane name will be used as the agent name.", height=2),
                self._name_field("Current agent name", self.controller.initial_agent_name or "(unnamed)"),
                self._name_field("New agent name", self.controller.proposed_name),
            ]
        else:
            rows += [self._label("Name"), self.input_window]
        rows += [
            spacer(),
            self._label(self._status_text, height=4),
            VSplit(
                [
                    Window(width=Dimension(weight=1)),
                    self.apply_button.window,
                    Window(width=2),
                    self.cancel_button.window,
                ],
                height=1,
            ),
            Window(height=Dimension(weight=1)),
        ]
        return VSplit([Window(width=1), HSplit(rows), Window(width=1)])

    def _name_field(self, label: str, value: str) -> HSplit:
        control = ReadOnlyNameControl(self, value)
        window = Window(control, height=1, wrap_lines=False)
        self.name_controls.append(control)
        self.name_windows.append(window)
        return HSplit([self._label(f"{label}:"), window])

    @property
    def names_overflow(self) -> bool:
        width = max(1, self.app.output.get_size().columns - 2)
        return any(get_cwidth(control.value) > width for control in self.name_controls)

    @staticmethod
    def _bounded_text(text: str, width: int, lines: int) -> str:
        # Count terminal cells, rather than codepoints: server diagnostics and
        # invalid pane labels can contain wide Unicode characters.
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
        if len(wrapped) > lines:
            wrapped = wrapped[:lines]
            while wrapped[-1] and get_cwidth(wrapped[-1]) > max(0, width - 3):
                wrapped[-1] = wrapped[-1][:-1]
            wrapped[-1] += "." * min(width, 3)
        return "\n".join(wrapped)

    def _status_text(self) -> list[tuple[str, str]]:
        state = str(self.controller.state)
        if self.busy:
            message = "Renaming agent..." if state.endswith("submitting") else "Checking the target..."
            style = "class:hint"
        elif self.controller.message:
            message = display_text(self.controller.message)
            style = "class:error"
        elif self.controller.mode == "open" and not self.controller.initial_agent_name:
            message = "This agent has no name. Enter a name."
            style = "class:hint"
        else:
            message = ""
            style = ""
        width = max(1, self.app.output.get_size().columns - 2)
        if self.names_overflow and not self.busy:
            text = self._bounded_text(message, width, 3)
            return [(style, text + "\n"), ("class:hint", "Left/Right: scroll names.")]
        return [(style, self._bounded_text(message, width, 4))]

    def _resize_text(self) -> str:
        size = self.app.output.get_size()
        message = "Renaming agent... Enlarge the terminal to see the result." if self.busy else RESIZE_MESSAGE
        bounded = self._bounded_text(message, max(1, size.columns), max(1, size.rows))
        if not bounded.endswith("." * min(max(1, size.columns), 3)):
            return message
        if self.busy:
            return "Working" if size.columns >= 7 else "Wait"[: size.columns]
        if size.rows >= 2 and size.columns >= 8:
            return "Resize\nEsc exit"
        if size.rows >= 2 and size.columns >= 4:
            return "Size\nEsc"
        return "Esc: cancel" if size.columns >= 11 else "Esc exit" if size.columns >= 8 else "Esc"[: size.columns]

    def _handle_resize(self, app: Application[bool]) -> None:
        small = not self.has_room
        if small and not self._was_small:
            self._previous_focus = app.layout.current_control
            app.layout.focus(self._resize_control)
        elif not small and self._was_small:
            app.layout.focus(self._previous_focus or self.cancel_button.control)
        self._was_small = small

    def _focus(self, reverse: bool = False) -> None:
        if self.busy or not self.has_room:
            return
        controls = [button.control for button in self.buttons]
        if self.controller.mode == "open":
            controls.insert(0, self.input_control)
        current = self.app.layout.current_control
        index = controls.index(current) if current in controls else 0
        self.app.layout.focus(controls[(index + (-1 if reverse else 1)) % len(controls)])

    def _cancel(self) -> None:
        if not self.busy and self.controller.cancel():
            self.app.exit(result=False)

    def _affirm(self) -> None:
        if not self.apply_button.enabled:
            return
        # Latch before scheduling the coroutine: multiple keys can be processed
        # in the same input batch before Controller.submit gets CPU time.
        self._working = True
        self.app.invalidate()
        self.app.create_background_task(self._submit())

    async def _submit(self) -> None:
        try:
            name = self.buffer.text if self.controller.mode == "open" else None
            if await self.controller.submit(name):
                self.app.exit(result=True)
        except Exception as exc:
            self.app.exit(exception=exc)
        finally:
            self._working = False
            self.app.invalidate()

    def _activate(self) -> None:
        if self.busy or not self.has_room:
            return
        if self.app.layout.has_focus(self.cancel_button.control):
            self._cancel()
        else:
            self._affirm()

    def _scroll_names(self, amount: int | str) -> None:
        maximum = max((len(control.value) for control in self.name_controls), default=0)
        if amount == "home":
            self._name_position = 0
        elif amount == "end":
            self._name_position = maximum
        else:
            self._name_position = min(maximum, max(0, self._name_position + int(amount)))
        self.app.invalidate()

    def _bindings(self) -> KeyBindings:
        keys = KeyBindings()

        @keys.add("tab", eager=True)
        def _tab(event: Any) -> None:
            self._focus()

        @keys.add("s-tab", eager=True)
        def _backtab(event: Any) -> None:
            self._focus(reverse=True)

        @keys.add("enter", eager=True)
        def _enter(event: Any) -> None:
            self._activate()

        @keys.add(" ", filter=Condition(lambda: self.controller.mode == "sync" or not self.app.layout.has_focus(self.input_control)))
        def _space(event: Any) -> None:
            self._activate()

        @keys.add("escape", eager=True)
        @keys.add("c-c", eager=True)
        @keys.add(Keys.SIGINT, eager=True)
        def _escape(event: Any) -> None:
            self._cancel()

        editable = Condition(lambda: self.controller.mode == "open" and self.app.layout.has_focus(self.input_control) and not self.busy and self.has_room)

        @keys.add(Keys.BracketedPaste, eager=True)
        def _paste(event: Any) -> None:
            if editable():
                self.buffer.insert_text(event.data)

        @keys.add("left", filter=editable)
        def _left(event: Any) -> None:
            self.buffer.cursor_position = max(0, self.buffer.cursor_position - 1)

        @keys.add("right", filter=editable)
        def _right(event: Any) -> None:
            self.buffer.cursor_position = min(len(self.buffer.text), self.buffer.cursor_position + 1)

        @keys.add("home", filter=editable)
        def _home(event: Any) -> None:
            self.buffer.cursor_position = 0

        @keys.add("end", filter=editable)
        def _end(event: Any) -> None:
            self.buffer.cursor_position = len(self.buffer.text)

        @keys.add("c-d", filter=editable)
        def _delete(event: Any) -> None:
            self.buffer.delete()

        inspecting = Condition(lambda: self.controller.mode == "sync" and not self.busy and self.has_room)

        @keys.add("left", filter=inspecting)
        def _inspect_left(event: Any) -> None:
            self._scroll_names(-1)

        @keys.add("right", filter=inspecting)
        def _inspect_right(event: Any) -> None:
            self._scroll_names(1)

        @keys.add("home", filter=inspecting)
        def _inspect_home(event: Any) -> None:
            self._scroll_names("home")

        @keys.add("end", filter=inspecting)
        def _inspect_end(event: Any) -> None:
            self._scroll_names("end")

        @keys.add(Keys.Any, filter=Condition(lambda: self.busy or not self.has_room))
        def _locked(event: Any) -> None:
            pass

        return keys

    async def run_async(self) -> bool:
        """Restore terminal modes on all exits, including unexpected failures."""
        return bool(await self.app.run_async(set_exception_handler=False))


async def run_dialog(controller: Controller) -> bool:
    return await Dialog(controller).run_async()
