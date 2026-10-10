"""Single-pane sync dialog: real VT100 key and mouse streams, layout and cleanup."""

import asyncio
import re
import unittest

from prompt_toolkit.data_structures import Size
from prompt_toolkit.utils import get_cwidth

from dialog_case import DialogCase
from herdr_agent_rename.theme import PALETTES
from herdr_agent_rename.ui import RESIZE_MESSAGE, SyncDialog, display_text
from herdr_agent_rename.ui.common import bounded_text
from ui_support import FakeController, RecordingOutput, TermOutput, eventually, sgr_press


def sync(**kwargs):
    return FakeController(mode="sync", **kwargs)


class SyncTests(DialogCase):
    dialog_class = SyncDialog
    default_size = (57, 12)

    async def test_defaults_to_no_and_tab_then_yes_applies(self):
        dialog, task, controller, _ = await self.start(sync())
        self.assertTrue(dialog.app.layout.has_focus(dialog.cancel_button.control))
        await self.keys("\t", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        await self.keys("\r", task.done)
        self.assertTrue(await task)
        self.assertEqual(controller.attempts, [None])
        self.assertEqual(controller.writes, ["backend-api"])

    async def test_left_and_right_change_the_selected_button(self):
        dialog, task, controller, _ = await self.start(sync())
        await self.keys("\x1b[D", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        await self.keys("\x1b[C", lambda: dialog.app.layout.has_focus(dialog.cancel_button.control))
        await self.keys("\x1b[C", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        await self.keys("\r", task.done)
        self.assertTrue(await task)

    async def test_y_and_n_keys(self):
        _, task, controller, _ = await self.start(sync())
        await self.keys("y", task.done)
        self.assertTrue(await task)
        _, task, controller, _ = await self.start(sync())
        await self.keys("n", task.done)
        self.assertFalse(await task)
        self.assertEqual(controller.attempts, [])

    async def test_default_enter_writes_nothing(self):
        _, task, controller, _ = await self.start(sync())
        await self.keys("\r", task.done)
        self.assertFalse(await task)
        self.assertEqual(controller.attempts, [])

    async def test_escape_and_control_c_cancel(self):
        for key in ("\x1b", "\x03"):
            _, task, controller, _ = await self.start(sync())
            await self.keys(key, task.done)
            self.assertFalse(await task)
            self.assertEqual(controller.attempts, [])

    async def test_mouse_press_on_yes_and_no(self):
        output = TermOutput(57, 12)
        dialog, task, controller, _ = await self.start(sync(), output)
        screen = output.screen()
        row = next(y for y, line in enumerate(screen.display) if "[ Yes ]" in line)
        x_yes, x_no = screen.display[row].index("[ Yes ]"), screen.display[row].index("[ No ]")
        self.pipe.send_text(sgr_press(x_yes + 2, row))
        self.assertTrue(await asyncio.wait_for(task, 2))
        self.assertEqual(controller.writes, ["backend-api"])
        output = TermOutput(57, 12)
        dialog, task, controller, _ = await self.start(sync(), output)
        self.pipe.send_text(sgr_press(x_no + 2, row))
        self.assertFalse(await asyncio.wait_for(task, 2))
        self.assertEqual(controller.attempts, [])

    async def test_initial_error_disables_yes_and_keeps_no_working(self):
        dialog, task, controller, _ = await self.start(sync(error="This pane has no name."))
        await self.keys("\t", lambda: dialog.app.layout.has_focus(dialog.apply_button.control))
        self.pipe.send_text("\ry")
        await asyncio.sleep(0.05)
        self.assertEqual(controller.attempts, [])
        self.assertFalse(dialog.apply_button.enabled)
        await self.keys("\t\r", task.done)
        self.assertFalse(await task)

    async def test_busy_latch_blocks_repeats_and_cancellation(self):
        controller = sync()
        controller.release = asyncio.Event()
        dialog, task, _, _ = await self.start(controller)
        self.pipe.send_text("\x1b[D\r\r\r\x03\x1b")
        await controller.started.wait()
        await asyncio.sleep(0.1)
        self.assertEqual(controller.attempts, [None])
        self.assertFalse(task.done())
        controller.release.set()
        self.assertTrue(await asyncio.wait_for(task, 2))

    async def test_tiny_screen_blocks_affirmative_and_allows_escape(self):
        for columns, rows in ((57, 11), (39, 12), (8, 2)):
            dialog, task, controller, _ = await self.start(sync(), RecordingOutput(columns=columns, rows=rows))
            self.assertFalse(dialog.has_room)
            self.assertEqual(dialog._resize_text(), "Resize\nEsc exit" if columns == 8 else RESIZE_MESSAGE)
            self.pipe.send_text("\t\r\x1b[Z\ry" + sgr_press(1, 1))
            await asyncio.sleep(0.05)
            self.assertEqual(controller.attempts, [])
            await self.keys("\x1b", task.done)
            self.assertFalse(await task)

    async def test_modes_restored_on_all_exits(self):
        for outcome in ("success", "cancel", "exception"):
            controller = sync()
            if outcome == "exception":
                controller.exception = RuntimeError("unexpected failure")
            _, task, _, output = await self.start(controller)
            await self.keys("\x1b" if outcome == "cancel" else "y", task.done)
            if outcome == "exception":
                with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
                    await task
            else:
                self.assertEqual(await task, outcome == "success")
            for event in ("mouse-on", "mouse-off", "screen-on", "screen-off", "cursor-on"):
                self.assertIn(event, output.events)

    async def test_long_diagnostic_is_bounded_with_buttons_visible(self):
        controller = sync(error="Could not rename the agent. " + "Details " * 100)
        dialog, task, _, _ = await self.start(controller)
        message = dialog._status_text()[0][1]
        self.assertEqual(len(message.splitlines()), 4)
        self.assertTrue(message.endswith("..."))
        self.assertIn(dialog.cancel_button.window, list(dialog.app.layout.find_all_windows()))
        await self.keys("\x1b", task.done)

    async def test_rendered_geometry_and_theme_colors(self):
        message = "Use 1-32 characters: a lowercase letter first, then lowercase letters, digits, underscores, or hyphens."
        for columns in (57, 40):
            output = TermOutput(columns, 12)
            controller = sync(initial="a" * 32, proposed="b" * 32, error=message)
            _, task, _, _ = await self.start(controller, output)
            screen = output.screen()
            rendered = "\n".join(screen.display)
            self.assertIn("The pane name will be used", screen.display[0])
            self.assertIn("a" * 32, rendered)
            self.assertIn("b" * 32, rendered)
            self.assertIn("hyphens.", rendered)
            self.assertIn("[ Yes ]  [ No ]", rendered)
            palette = PALETTES["catppuccin"]
            self.assertEqual(screen.buffer[0][0].bg, palette["panel_bg"][1:])
            self.assertEqual(screen.buffer[0][0].fg, palette["text"][1:])
            row = next(y for y, line in enumerate(screen.display) if "[ No ]" in line)
            cell = screen.buffer[row][screen.display[row].index("[ No ]")]
            self.assertEqual((cell.bg, cell.fg), (palette["accent"][1:], palette["panel_bg"][1:]))  # focused
            await self.keys("\x1b", task.done)

    async def test_invalid_long_label_can_be_scrolled_without_enabling_yes(self):
        label = "start-" + "界" * 80 + "-tail"
        output = TermOutput(57, 12)
        dialog, task, controller, _ = await self.start(
            sync(proposed=label, error="The pane name is not a valid agent name."), output
        )
        self.assertEqual(dialog.name_controls[1].value, label)
        self.assertTrue(dialog.names_overflow)
        self.assertFalse(dialog.apply_button.enabled)
        await self.keys("\x1b[F", lambda: dialog.name_windows[1].horizontal_scroll > 80)
        self.assertTrue(dialog.app.layout.has_focus(dialog.cancel_button.control))
        rendered = "\n".join(output.screen().display)
        self.assertIn("-tail", rendered)
        self.assertIn("Home/End: scroll names.", rendered)
        await self.keys("\x1b[H", lambda: dialog.name_windows[1].horizontal_scroll == 0)
        self.assertEqual(controller.attempts, [])
        await self.keys("\x1b", task.done)

    async def test_extremely_small_view_still_shows_escape_hint(self):
        output = TermOutput(8, 2)
        _, task, controller, _ = await self.start(sync(), output)
        screen = output.screen()
        self.assertIn("Resize", screen.display[0])
        self.assertIn("Esc exit", screen.display[1])
        await self.keys("\x1b", task.done)
        self.assertEqual(controller.writes, [])


class HelperTests(unittest.TestCase):
    def test_wrapping_counts_wide_unicode_cells_and_marks_truncation(self):
        rendered = bounded_text("界" * 60, 38, 2)
        self.assertTrue(rendered.endswith("..."))
        self.assertEqual(len(rendered.splitlines()), 2)
        self.assertTrue(all(get_cwidth(line) <= 38 for line in rendered.splitlines()))

    def test_control_diagnostics_are_visible_and_cannot_emit_escape_sequences(self):
        self.assertEqual(display_text("a\n\r\t\x1b\x00\u202e"), r"a\n\r\t\x1b\x00\u202e")
