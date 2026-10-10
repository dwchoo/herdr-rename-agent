"""Single-pane sync confirmation dialog (to be replaced by workspace batch sync)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
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
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import ColorDepth, Output
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from ..theme import Theme, load_theme
from .common import MIN_WIDTH, RESIZE_MESSAGE, bounded_text, display_text, resize_text

if TYPE_CHECKING:
    from ..controller import Controller


MIN_SYNC_HEIGHT = 12
__all__ = ["SyncDialog", "RESIZE_MESSAGE", "display_text"]


class ReadOnlyNameControl(FormattedTextControl):
    """An exact source name with a movable viewing position, never an editor."""

    def __init__(self, dialog: SyncDialog, value: str) -> None:
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
    def __init__(self, dialog: SyncDialog, label: str, affirmative: bool) -> None:
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

    def _press(self, mouse_event: MouseEvent) -> Any:
        if (
            mouse_event.event_type != MouseEventType.MOUSE_DOWN
            or mouse_event.button != MouseButton.LEFT
        ):
            return NotImplemented
        self.dialog.press(self)
        return None

    def _text(self) -> list[Any]:
        style = "class:button"
        if not self.enabled:
            style += " class:disabled"
        elif self.dialog.app.layout.has_focus(self.control):
            style += " class:button.focused"
        return [(style, f"[ {self.label} ]", self._press)]


class SyncDialog:
    """One popup application; the controller owns all target/write decisions."""

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
        self._working = False
        self._was_small = False
        self._previous_focus: Any = None
        self._name_position = 0
        self.name_controls: list[ReadOnlyNameControl] = []
        self.name_windows: list[Window] = []
        self.apply_button = ActionButton(self, "Yes", True)
        self.cancel_button = ActionButton(self, "No", False)
        self.buttons = (self.apply_button, self.cancel_button)
        self._resize_control = FormattedTextControl(self._resize_text, focusable=True)
        self._resize_layout = Window(self._resize_control, wrap_lines=True, style="class:panel")
        self._normal_layout = self._build_layout()
        root = DynamicContainer(
            lambda: self._normal_layout if self.has_room else self._resize_layout
        )
        styles = self.theme.styles()
        self.app: Application[bool] = Application(
            layout=Layout(root, focused_element=self.cancel_button.control),
            key_bindings=self._bindings(),
            style=Style.from_dict(
                {
                    "panel": styles["panel"],
                    "button": styles["secondary"],
                    "button.focused": styles["primary"],
                    "disabled": styles["disabled"],
                    "error": styles["error"],
                    "hint": styles["hint"],
                }
            ),
            color_depth=ColorDepth.DEPTH_24_BIT,
            full_screen=True,
            mouse_support=True,
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
        return size.columns >= MIN_WIDTH and size.rows >= MIN_SYNC_HEIGHT

    def _label(self, text: Any, *, height: int = 1, style: str = "") -> Window:
        return Window(
            FormattedTextControl(text),
            height=height,
            wrap_lines=True,
            style=style,
        )

    def _build_layout(self) -> VSplit:
        spacer = lambda: Window(height=1)
        # Herdr draws the manifest entrypoint title in the outer popup border.
        rows = [
            self._label("The pane name will be used as the agent name.", height=2),
            self._name_field("Current agent name", self.controller.initial_agent_name or "(unnamed)"),
            self._name_field("New agent name", self.controller.proposed_name),
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
        return VSplit([Window(width=1), HSplit(rows), Window(width=1)], style="class:panel")

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
        return bounded_text(text, width, lines)

    def _status_text(self) -> list[tuple[str, str]]:
        state = str(self.controller.state)
        if self.busy:
            message = "Renaming agent..." if state.endswith("submitting") else "Checking the target..."
            style = "class:hint"
        elif self.controller.message:
            message = display_text(self.controller.message)
            style = "class:error"
        else:
            message = ""
            style = ""
        width = max(1, self.app.output.get_size().columns - 2)
        if self.names_overflow and not self.busy:
            text = self._bounded_text(message, width, 3)
            return [(style, text + "\n"), ("class:hint", "Home/End: scroll names.")]
        return [(style, self._bounded_text(message, width, 4))]

    def _resize_text(self) -> str:
        return resize_text(self.app.output.get_size(), self.busy)

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
            if await self.controller.submit(None):
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

    def press(self, button: ActionButton) -> None:
        """A left-button press on Yes or No selects and activates it."""
        if self.busy or not self.has_room:
            return
        self.app.layout.focus(button.control)
        if button.affirmative:
            self._affirm()
        else:
            self._cancel()

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
        idle = Condition(lambda: not self.busy and self.has_room)

        @keys.add("tab", eager=True)
        def _tab(event: Any) -> None:
            self._focus()

        @keys.add("s-tab", eager=True)
        def _backtab(event: Any) -> None:
            self._focus(reverse=True)

        @keys.add("left", filter=idle)
        @keys.add("right", filter=idle)
        def _switch(event: Any) -> None:
            self._focus()

        @keys.add("enter", eager=True)
        @keys.add(" ", filter=idle)
        def _enter(event: Any) -> None:
            self._activate()

        @keys.add("y", filter=idle)
        @keys.add("Y", filter=idle)
        def _yes(event: Any) -> None:
            self.app.layout.focus(self.apply_button.control)
            self._affirm()

        @keys.add("n", filter=idle)
        @keys.add("N", filter=idle)
        def _no(event: Any) -> None:
            self._cancel()

        @keys.add("escape", eager=True)
        @keys.add("c-c", eager=True)
        @keys.add(Keys.SIGINT, eager=True)
        def _escape(event: Any) -> None:
            self._cancel()

        @keys.add("home", filter=idle)
        def _inspect_home(event: Any) -> None:
            self._scroll_names("home")

        @keys.add("end", filter=idle)
        def _inspect_end(event: Any) -> None:
            self._scroll_names("end")

        @keys.add("up", filter=idle)
        def _inspect_up(event: Any) -> None:
            self._scroll_names(-8)

        @keys.add("down", filter=idle)
        def _inspect_down(event: Any) -> None:
            self._scroll_names(8)

        @keys.add(Keys.Any, filter=Condition(lambda: self.busy or not self.has_room))
        def _locked(event: Any) -> None:
            pass

        return keys

    async def run_async(self) -> bool:
        """Restore terminal modes on all exits, including unexpected failures."""
        return bool(await self.app.run_async(set_exception_handler=False))
