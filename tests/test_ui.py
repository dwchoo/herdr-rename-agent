"""Real VT100 key streams exercise focus, paste, layout and terminal cleanup."""

import asyncio
import io
import re
import unittest

import pyte
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.utils import get_cwidth

from herdr_agent_rename.ui import Dialog, RESIZE_MESSAGE, display_text


class RecordingOutput(DummyOutput):
    def __init__(self, *, columns=53, rows=9):
        self.size = Size(rows=rows, columns=columns)
        self.events = []

    def get_size(self):
        return self.size

    def enable_bracketed_paste(self):
        self.events.append("paste-on")

    def disable_bracketed_paste(self):
        self.events.append("paste-off")

    def enter_alternate_screen(self):
        self.events.append("screen-on")

    def quit_alternate_screen(self):
        self.events.append("screen-off")

    def show_cursor(self):
        self.events.append("cursor-on")


class FakeController:
    def __init__(self, *, mode="open", initial="agent", proposed="backend-api", error=""):
        self.mode = mode
        self.initial_agent_name = initial
        self.proposed_name = proposed
        self.message = error
        self.state = "error" if error else ("confirming" if mode == "sync" else "editing")
        self.can_submit = not error
        self.busy = False
        self.attempts = []
        self.writes = []
        self.started = asyncio.Event()
        self.release = None
        self.failure = ""
        self.exception = None

    async def submit(self, name=None):
        self.attempts.append(name)
        self.busy = True
        self.state = "submitting"
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        self.busy = False
        if self.exception:
            raise self.exception
        name = self.proposed_name if name is None else name
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
            self.state = "error"
            self.message = "Enter an agent name." if not name else "Use 1–32 characters: a lowercase letter first, then lowercase letters, digits, underscores, or hyphens."
            return False
        if self.failure:
            self.state = "error"
            self.message = self.failure
            return False
        self.writes.append(name)
        self.state = "closed"
        return True

    def cancel(self):
        if self.busy:
            return False
        self.state = "closed"
        return True


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


class DialogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe_context = create_pipe_input()
        self.pipe = self.pipe_context.__enter__()
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RuntimeError):
                pass
        self.pipe_context.__exit__(None, None, None)

    async def start(self, controller=None, output=None):
        controller = controller or FakeController()
        output = output or RecordingOutput(columns=57 if controller.mode == "sync" else 53, rows=12 if controller.mode == "sync" else 9)
        dialog = Dialog(controller, input=self.pipe, output=output)
        task = asyncio.create_task(dialog.run_async())
        self.tasks.append(task)
        await eventually(lambda: dialog.app.is_running and dialog.app.render_counter > 0)
        return dialog, task, controller, output

    async def keys(self, data, predicate):
        self.pipe.send_text(data)
        await eventually(predicate)

    async def test_cursor_editing_and_input_enter_applies_literal_name(self):
        dialog, task, controller, _ = await self.start()
        await self.keys("\x1b[H\x1b[C\x1b[3~x\x1b[F\x7f", lambda: dialog.buffer.text == "axen")
        self.assertEqual(dialog.buffer.cursor_position, 4)
        await self.keys("\r", task.done)
        self.assertTrue(await task)
        self.assertEqual(controller.writes, ["axen"])

    async def test_tab_reverse_tab_and_button_enter_cancel(self):
        dialog, task, controller, _ = await self.start()
        self.assertTrue(dialog.app.layout.has_focus(dialog.input_control))
        await self.keys("\t", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        await self.keys("\t", lambda: dialog.app.layout.has_focus(dialog.cancel_button.control))
        await self.keys("\x1b[Z", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        await self.keys("\x1b[Z", lambda: dialog.app.layout.has_focus(dialog.input_control))
        await self.keys("\x1b[Z\r", task.done)
        self.assertFalse(await task)
        self.assertEqual(controller.attempts, [])

    async def test_sync_defaults_to_no_and_tab_then_yes_applies(self):
        dialog, task, controller, _ = await self.start(FakeController(mode="sync"))
        self.assertTrue(dialog.app.layout.has_focus(dialog.cancel_button.control))
        await self.keys("\t", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        await self.keys("\r", task.done)
        self.assertTrue(await task)
        self.assertEqual(controller.attempts, [None])
        self.assertEqual(controller.writes, ["backend-api"])

    async def test_sync_default_enter_writes_nothing(self):
        _, task, controller, _ = await self.start(FakeController(mode="sync"))
        await self.keys("\r", task.done)
        self.assertFalse(await task)
        self.assertEqual(controller.attempts, [])

    async def test_escape_and_control_c_cancel_without_submitting(self):
        for key in ("\x1b", "\x03"):
            _, task, controller, _ = await self.start()
            await self.keys(key, task.done)
            self.assertFalse(await task)
            self.assertEqual(controller.attempts, [])

    async def test_bracketed_paste_keeps_newlines_carriage_returns_unicode_controls(self):
        for pasted in ("valid\nname", "valid\r\nname", "naïve", "agent\x1b[31m\x00"):
            dialog, task, controller, _ = await self.start(FakeController(initial=""))
            await self.keys("\x1b[200~" + pasted + "\x1b[201~", lambda: dialog.buffer.text == pasted)
            self.assertEqual(controller.attempts, [])
            self.assertEqual(dialog.input_control.create_content(57, 1).line_count, 1)
            await self.keys("\r", lambda: controller.state == "error")
            self.assertEqual(dialog.buffer.text, pasted)
            self.assertEqual(controller.attempts, [pasted])
            self.assertEqual(controller.writes, [])
            await self.keys("\x03", task.done)
            self.assertFalse(await task)

    async def test_long_input_scrolls_and_is_preserved_after_validation_error(self):
        name = "a" * 200
        dialog, task, controller, _ = await self.start(FakeController(initial=""))
        await self.keys(name, lambda: dialog.buffer.text == name and dialog.input_window.horizontal_scroll > 0)
        self.assertGreater(dialog.input_window.horizontal_scroll, 100)
        await self.keys("\x1b[H", lambda: dialog.buffer.cursor_position == 0 and dialog.input_window.horizontal_scroll == 0)
        await self.keys("\r", lambda: controller.state == "error")
        self.assertEqual(dialog.buffer.text, name)
        await self.keys("\x03", task.done)

    async def test_busy_latch_blocks_repeated_enter_edit_and_cancellation(self):
        controller = FakeController()
        controller.release = asyncio.Event()
        dialog, task, _, _ = await self.start(controller)
        self.pipe.send_text("\r\r\r\x03\x1b")
        await controller.started.wait()
        await asyncio.sleep(0.1)
        self.assertEqual(controller.attempts, ["agent"])
        self.assertFalse(task.done())
        self.assertFalse(dialog.apply_button.enabled)
        self.pipe.send_text("changed\t\r")
        await asyncio.sleep(0.03)
        self.assertEqual(dialog.buffer.text, "agent")
        controller.release.set()
        self.assertTrue(await asyncio.wait_for(task, 2))
        self.assertEqual(controller.writes, ["agent"])

    async def test_error_retains_input_and_explicit_retry(self):
        controller = FakeController(initial="original")
        controller.failure = "This agent name is already in use."
        dialog, task, _, _ = await self.start(controller)
        await self.keys("\x15new-name\r", lambda: controller.state == "error")
        self.assertEqual(dialog.buffer.text, "new-name")
        self.assertIn(controller.failure, dialog._status_text()[0][1])
        self.assertFalse(task.done())
        controller.failure = ""
        await self.keys("\r", task.done)
        self.assertTrue(await task)
        self.assertEqual(controller.writes, ["new-name"])
        self.assertEqual(controller.attempts, ["new-name", "new-name"])

    async def test_initial_error_disables_yes_and_keeps_no_working(self):
        dialog, task, controller, _ = await self.start(FakeController(mode="sync", error="This pane has no name."))
        await self.keys("\t", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        self.pipe.send_text("\r")
        await asyncio.sleep(0.03)
        self.assertEqual(controller.attempts, [])
        self.assertFalse(dialog.apply_button.enabled)
        await self.keys("\t\r", task.done)
        self.assertFalse(await task)

    async def test_tiny_screen_blocks_affirmative_and_allows_cancel(self):
        for mode, columns, rows in (("open", 39, 9), ("open", 53, 8), ("sync", 57, 11), ("sync", 8, 2)):
            dialog, task, controller, _ = await self.start(FakeController(mode=mode), RecordingOutput(columns=columns, rows=rows))
            self.assertFalse(dialog.has_room)
            self.assertEqual(dialog._resize_text(), "Resize\nEsc exit" if columns == 8 else RESIZE_MESSAGE)
            self.assertTrue(dialog.app.layout.has_focus(dialog._resize_control))
            self.pipe.send_text("\t\r\x1b[Z\r")
            await asyncio.sleep(0.03)
            self.assertEqual(controller.attempts, [])
            await self.keys("\x03", task.done)
            self.assertFalse(await task)

    async def test_resize_preserves_input_and_previous_focus(self):
        dialog, task, controller, output = await self.start()
        await self.keys("-new\t", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        output.size = Size(rows=4, columns=20)
        dialog.app.invalidate()
        await eventually(lambda: dialog.app.layout.has_focus(dialog._resize_control))
        output.size = Size(rows=9, columns=53)
        dialog.app.invalidate()
        await eventually(lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        self.assertEqual(dialog.buffer.text, "agent-new")
        await self.keys("\r", task.done)
        self.assertEqual(controller.writes, ["agent-new"])

    async def test_output_modes_restored_after_success_cancel_and_exception(self):
        for outcome in ("success", "cancel", "exception"):
            controller = FakeController()
            if outcome == "exception":
                controller.exception = RuntimeError("unexpected failure")
            _, task, _, output = await self.start(controller)
            await self.keys("\x03" if outcome == "cancel" else "\r", task.done)
            if outcome == "exception":
                with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
                    await task
            else:
                self.assertEqual(await task, outcome == "success")
            self.assertIn("paste-on", output.events)
            self.assertIn("paste-off", output.events)
            self.assertIn("screen-on", output.events)
            self.assertIn("screen-off", output.events)
            self.assertIn("cursor-on", output.events)

    async def test_long_diagnostic_shows_explicit_ellipsis_with_buttons_visible(self):
        controller = FakeController(error="Could not rename the agent. " + "Details " * 100)
        dialog, task, _, _ = await self.start(controller)
        message = dialog._status_text()[0][1]
        self.assertEqual(len(message.splitlines()), 4)
        self.assertTrue(message.endswith("..."))
        self.assertIn(dialog.cancel_button.window, list(dialog.app.layout.find_all_windows()))
        await self.keys("\x03", task.done)

    async def test_rendered_popup_geometry_contains_full_names_errors_and_buttons(self):
        message = "Use 1–32 characters: a lowercase letter first, then lowercase letters, digits, underscores, or hyphens."
        for mode, columns, rows in (("open", 53, 9), ("sync", 57, 12), ("sync", 40, 12)):
            stream = io.StringIO()
            output = Vt100_Output(stream, get_size=lambda: Size(rows, columns), term="xterm-256color", enable_cpr=False)
            controller = FakeController(mode=mode, initial="a" * 32, proposed="b" * 32, error=message)
            _, task, _, _ = await self.start(controller, output)
            screen = pyte.Screen(columns, rows)
            pyte.Stream(screen).feed(stream.getvalue())
            rendered = "\n".join(screen.display)
            self.assertNotIn("Sync agent name" if mode == "sync" else "Rename agent", rendered)
            self.assertIn("The pane name will be used" if mode == "sync" else "Name", screen.display[0])
            self.assertIn("a" * 32, rendered)
            if mode == "sync":
                self.assertIn("b" * 32, rendered)
            self.assertIn("hyphens.", rendered)
            self.assertIn("[ Yes ]  [ No ]" if mode == "sync" else "[ Apply ]  [ Cancel ]", rendered)
            self.assertNotIn("Window too small", rendered)
            await self.keys("\x03", task.done)

    def test_wrapping_counts_wide_unicode_cells_and_marks_truncation(self):
        rendered = Dialog._bounded_text("界" * 60, 38, 2)
        self.assertTrue(rendered.endswith("..."))
        self.assertEqual(len(rendered.splitlines()), 2)
        self.assertTrue(all(get_cwidth(line) <= 38 for line in rendered.splitlines()))

    async def test_wide_unicode_cursor_uses_terminal_cells_and_horizontal_scroll(self):
        stream = io.StringIO()
        output = Vt100_Output(stream, get_size=lambda: Size(9, 53), term="xterm-256color", enable_cpr=False)
        dialog, task, controller, _ = await self.start(FakeController(initial=""), output)
        pasted = "abc界def"
        await self.keys("\x1b[200~" + pasted + "\x1b[201~", lambda: dialog.buffer.text == pasted)
        await eventually(lambda: dialog.app.render_counter >= 2)
        screen = pyte.Screen(53, 9)
        pyte.Stream(screen).feed(stream.getvalue())
        self.assertEqual(screen.cursor.x, 1 + get_cwidth(pasted))
        self.assertEqual(screen.cursor.y, 1)
        self.assertEqual(dialog.buffer.cursor_position, len(pasted))
        await self.keys("\x1b[200~" + "界" * 100 + "\x1b[201~", lambda: dialog.input_window.horizontal_scroll > 100)
        self.assertEqual(dialog.buffer.text, pasted + "界" * 100)
        await self.keys("\x1b[H", lambda: dialog.buffer.cursor_position == 0 and dialog.input_window.horizontal_scroll == 0)
        await self.keys("\x03", task.done)
        self.assertEqual(controller.writes, [])

    async def test_invalid_long_sync_label_can_be_inspected_exactly_without_enabling_yes(self):
        label = "start-" + "界" * 80 + "-tail"
        stream = io.StringIO()
        output = Vt100_Output(stream, get_size=lambda: Size(12, 57), term="xterm-256color", enable_cpr=False)
        dialog, task, controller, _ = await self.start(FakeController(mode="sync", proposed=label, error="The pane name is not a valid agent name."), output)
        self.assertEqual(dialog.name_controls[1].value, label)
        self.assertEqual(dialog.name_controls[1].create_content(59, 1).get_line(0)[0][1], label + " ")
        self.assertTrue(dialog.names_overflow)
        self.assertFalse(dialog.apply_button.enabled)
        await self.keys("\x1b[F", lambda: dialog.name_windows[1].horizontal_scroll > 80)
        self.assertTrue(dialog.app.layout.has_focus(dialog.cancel_button.control))
        screen = pyte.Screen(57, 12)
        pyte.Stream(screen).feed(stream.getvalue())
        rendered = "\n".join(screen.display)
        self.assertIn("-tail", rendered)
        self.assertIn("Left/Right: scroll names.", rendered)
        self.assertIn("[ Yes ]  [ No ]", rendered)
        await self.keys("\x1b[H", lambda: dialog.name_windows[1].horizontal_scroll == 0)
        await self.keys("\t", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        self.pipe.send_text("\r")
        await asyncio.sleep(0.03)
        self.assertEqual(controller.attempts, [])
        await self.keys("\x03", task.done)

    async def test_extremely_small_rendered_view_still_displays_escape_cancellation(self):
        stream = io.StringIO()
        output = Vt100_Output(stream, get_size=lambda: Size(2, 8), term="xterm-256color", enable_cpr=False)
        _, task, controller, _ = await self.start(output=output)
        screen = pyte.Screen(8, 2)
        pyte.Stream(screen).feed(stream.getvalue())
        self.assertIn("Resize", screen.display[0])
        self.assertIn("Esc exit", screen.display[1])
        await self.keys("\x1b", task.done)
        self.assertEqual(controller.writes, [])

    def test_control_diagnostics_are_visible_and_cannot_emit_escape_sequences(self):
        self.assertEqual(display_text("a\n\r\t\x1b\x00\u202e"), r"a\n\r\t\x1b\x00\u202e")
