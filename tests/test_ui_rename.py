"""The rename dialog replays Herdr's pane rename overlay inside the popup PTY."""

import asyncio
import unittest

from prompt_toolkit.data_structures import Size

from dialog_case import DialogCase
from herdr_agent_rename.theme import PALETTES, theme_from_config
from herdr_agent_rename.ui import RenameDialog, run_dialog
from ui_support import (
    FakeController,
    RecordingOutput,
    TermOutput,
    eventually,
    sgr_press,
    sgr_release,
)

SAVE, CLEAR, CANCEL = " ↵ save ", " ^c clear ", " esc cancel "


class RenameCase(DialogCase):
    dialog_class = RenameDialog

    async def start_term(self, controller=None, theme=None, columns=54, rows=5):
        output = TermOutput(columns, rows)
        kwargs = {"theme": theme} if theme else {}
        dialog, task, controller, _ = await self.start(controller, output, **kwargs)
        return dialog, task, controller, output

    async def frame(self, dialog, output):
        """Wait for the pending render, then return the replayed screen."""
        before = dialog.app.render_counter
        dialog.app.invalidate()
        await eventually(lambda: dialog.app.render_counter > before)
        return output.screen()


class LayoutTests(RenameCase):
    async def test_exact_cells_and_colors_for_each_theme(self):
        for name in ("catppuccin", "dracula"):
            theme = theme_from_config({"theme": {"name": name}})
            p = PALETTES[name]
            dialog, task, _, output = await self.start_term(theme=theme)
            screen = await self.frame(dialog, output)
            self.assertEqual(screen.display[0], "rename agent".ljust(54))
            self.assertEqual(screen.display[1], " " * 54)
            self.assertEqual(screen.display[2], " agent".ljust(54))
            buttons = " " * 10 + SAVE + "  " + CLEAR + "  " + CANCEL + " " * 10
            self.assertEqual(len(buttons), 54)
            self.assertEqual(screen.display[3], buttons)
            self.assertEqual(screen.display[4], " " * 54)
            hexa = lambda token: p[token][1:]
            cell = lambda x, y: screen.buffer[y][x]
            self.assertEqual((cell(0, 0).bg, cell(0, 0).fg, cell(0, 0).bold), (hexa("panel_bg"), hexa("text"), True))
            self.assertEqual((cell(0, 1).bg, cell(53, 4).bg), (hexa("panel_bg"),) * 2)
            for x in (0, 1, 53):
                self.assertEqual((cell(x, 2).bg, cell(x, 2).fg), (hexa("surface0"), hexa("text")))
            for x in (10, 17):  # save: first and last cell
                self.assertEqual((cell(x, 3).bg, cell(x, 3).fg, cell(x, 3).bold), (hexa("accent"), hexa("panel_bg"), True))
            for x in (20, 29, 32, 43):
                self.assertEqual((cell(x, 3).bg, cell(x, 3).fg, cell(x, 3).bold), (hexa("surface0"), hexa("text"), True))
            for x in (9, 18, 19, 30, 31, 44):  # gaps keep the panel color
                self.assertEqual(cell(x, 3).bg, hexa("panel_bg"))
            # Text starts at column 1; cursor at its end.
            self.assertEqual((screen.cursor.x, screen.cursor.y), (1 + len("agent"), 2))
            self.pipe.send_text("\x1b")
            await task

    async def test_themes_without_color_use_terminal_default(self):
        theme = theme_from_config({"theme": {"name": "terminal"}})
        dialog, task, _, output = await self.start_term(theme=theme)
        screen = await self.frame(dialog, output)
        self.assertEqual(screen.buffer[3][10].bg, "blue")
        self.assertEqual(screen.buffer[0][0].bg, "default")
        self.pipe.send_text("\x1b")
        await task

    async def test_cursor_is_hidden_from_layout_math_with_wide_text(self):
        dialog, task, _, output = await self.start_term(FakeController(initial=""))
        await self.keys("\x1b[200~abc界def\x1b[201~", lambda: dialog.editor.text == "abc界def")
        screen = await self.frame(dialog, output)
        self.assertEqual(screen.cursor.x, 1 + 8)
        self.pipe.send_text("\x1b")
        await task

    async def test_long_text_scrolls_with_cursor_visible(self):
        name = "a" * 200
        dialog, task, _, output = await self.start_term(FakeController(initial=""))
        await self.keys(name, lambda: dialog.editor.text == name)
        screen = await self.frame(dialog, output)
        self.assertEqual(screen.display[2], (" " + "a" * 52).ljust(54))
        self.assertEqual(screen.cursor.x, 53)
        await self.keys("\x1b[H", lambda: dialog.editor.cursor == 0)
        screen = await self.frame(dialog, output)
        self.assertEqual(screen.cursor.x, 1)
        self.pipe.send_text("\x1b")
        await task

    async def test_taller_terminal_keeps_controls_in_place(self):
        dialog, task, _, output = await self.start_term(rows=9)
        screen = await self.frame(dialog, output)
        self.assertIn(SAVE, screen.display[3])
        self.assertEqual(screen.display[8], " " * 54)
        self.pipe.send_text("\x1b")
        await task

    async def test_wider_terminal_centers_buttons(self):
        dialog, task, _, output = await self.start_term(columns=60)
        screen = await self.frame(dialog, output)
        self.assertEqual(screen.display[3].index(SAVE), 13)
        self.pipe.send_text("\x1b")
        await task

    async def test_run_dialog_selects_rename_dialog(self):
        task = asyncio.create_task(run_dialog(FakeController(mode="sync")))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


class KeyTests(RenameCase):
    async def test_enter_submits_raw_text_including_padding(self):
        dialog, task, controller, _ = await self.start(FakeController(initial="  ab "))
        await self.keys("\r", task.done)
        self.assertTrue(await task)
        self.assertEqual(controller.attempts, ["  ab "])
        self.assertEqual(controller.writes, ["ab"])

    async def test_empty_text_is_submitted_for_the_backend_to_clear(self):
        dialog, task, controller, _ = await self.start(FakeController(initial="old"))
        await self.keys("\x03", lambda: dialog.editor.text == "")
        await self.keys("\r", task.done)
        self.assertTrue(await task)
        self.assertEqual(controller.attempts, [""])

    async def test_escape_cancels_without_submitting(self):
        _, task, controller, _ = await self.start()
        await self.keys("\x1b", task.done)
        self.assertFalse(await task)
        self.assertEqual(controller.attempts, [])

    async def test_ctrl_c_only_clears_the_field(self):
        dialog, task, controller, _ = await self.start()
        await self.keys("\x03", lambda: dialog.editor.text == "")
        self.assertEqual(dialog.editor.cursor, 0)
        self.assertFalse(task.done())
        await self.keys("xy", lambda: dialog.editor.text == "xy")
        self.pipe.send_text("\x1b")
        await task

    async def test_editing_keys_follow_key_operations(self):
        cases = [
            ("\x1b[H" "X", "Xagent"),                   # home
            ("\x01" "X", "Xagent"),                     # ctrl+a
            ("\x1b[D" "X", "agenXt"),                  # left
            ("\x02" "X", "agenXt"),                    # ctrl+b
            ("\x1b[H\x1b[C" "X", "aXgent"),             # right
            ("\x7f", "agen"),                           # backspace
            ("\x08", "agen"),                           # ctrl+h
            ("\x1b[H\x1b[3~", "gent"),                  # delete
            ("\x1b[H\x04", "gent"),                     # ctrl+d
            ("\x1b[D\x15", "t"),                        # ctrl+u
            ("\x1b[H\x1b[C\x0b", "a"),                  # ctrl+k
            ("\x17", ""),                               # ctrl+w
            ("\x1b\x7f", ""),                           # alt+backspace
            ("\x15\x19", "agent"),                     # ctrl+u, ctrl+y
        ]
        for data, expected in cases:
            dialog, task, controller, _ = await self.start(FakeController(initial="agent"))
            self.pipe.send_text(data)
            await eventually(lambda: dialog.editor.text == expected)
            self.assertEqual(controller.attempts, [], data)
            self.pipe.send_text("\x1b")
            await task

    async def test_alt_word_keys_and_unbound_keys_are_ignored(self):
        dialog, task, controller, _ = await self.start(FakeController(initial="one two"))
        await self.keys("\x1bb" "X", lambda: dialog.editor.text == "one Xtwo")
        await self.keys("\x1bd", lambda: dialog.editor.text == "one X")
        for ignored in ("\t", "\x1b[Z", "\x1b[A", "\x1b[B", "\x1bx", "\x1b[5~", "\x1bOP", "\x0c", "\x12"):
            self.pipe.send_text(ignored)
        await asyncio.sleep(0.15)
        self.assertEqual(dialog.editor.text, "one X")
        self.assertFalse(task.done())
        self.pipe.send_text("\x1b")
        await task

    async def test_bracketed_paste_uses_editor_insert(self):
        for pasted, expected in (("a\nb", "a b"), ("x\r\ny\tz", "x y z"), ("naïve", "naïve"), ("a\x00b", "a�b")):
            dialog, task, controller, _ = await self.start(FakeController(initial=""))
            self.pipe.send_text("\x1b[200~" + pasted + "\x1b[201~")
            await eventually(lambda: dialog.editor.text == expected)
            self.assertEqual(controller.attempts, [])
            self.pipe.send_text("\x1b")
            await task

    async def test_open_has_no_replace_on_type(self):
        dialog, task, _, _ = await self.start()
        self.assertFalse(dialog.editor.replace_on_type)
        self.assertEqual(dialog.editor.cursor, 5)
        await self.keys("z", lambda: dialog.editor.text == "agentz")
        self.pipe.send_text("\x1b")
        await task

    async def test_default_emacs_bindings_do_not_interfere(self):
        dialog, task, _, _ = await self.start(FakeController(initial="ab"))
        self.pipe.send_text("\x14\x0e\x10\x13\x1f")  # ctrl+t n p s _
        await asyncio.sleep(0.1)
        self.assertEqual(dialog.editor.text, "ab")
        self.pipe.send_text("\x1b")
        await task


class MouseTests(RenameCase):
    async def start_mouse(self, controller=None):
        dialog, task, controller, output = await self.start_term(controller)
        await self.frame(dialog, output)
        return dialog, task, controller, output

    async def test_press_on_save_submits(self):
        for x in (10, 17):
            dialog, task, controller, _ = await self.start_mouse()
            self.pipe.send_text(sgr_press(x, 3))
            self.assertTrue(await asyncio.wait_for(task, 2))
            self.assertEqual(controller.attempts, ["agent"])

    async def test_press_on_clear_clears_and_stays_open(self):
        for x in (20, 29):
            dialog, task, controller, _ = await self.start_mouse()
            await self.keys(sgr_press(x, 3), lambda: dialog.editor.text == "")
            self.assertFalse(task.done())
            self.assertEqual(controller.attempts, [])
            self.pipe.send_text("\x1b")
            await task

    async def test_press_anywhere_else_cancels(self):
        spots = [(32, 3), (43, 3), (0, 0), (5, 2), (53, 4), (19, 3), (9, 3), (44, 3), (30, 1)]
        for x, y in spots:
            dialog, task, controller, _ = await self.start_mouse()
            self.pipe.send_text(sgr_press(x, y))
            self.assertFalse(await asyncio.wait_for(task, 2), (x, y))
            self.assertEqual(controller.attempts, [])

    async def test_release_move_scroll_and_other_buttons_are_ignored(self):
        dialog, task, controller, _ = await self.start_mouse()
        self.pipe.send_text(sgr_release(10, 3) + "\x1b[<35;11;4M" + "\x1b[<64;11;4M" + sgr_press(10, 3, 2))
        await asyncio.sleep(0.15)
        self.assertFalse(task.done())
        self.assertEqual(controller.attempts, [])
        self.pipe.send_text("\x1b")
        await task

    async def test_mouse_after_wide_text_still_hits_buttons(self):
        dialog, task, controller, _ = await self.start_mouse(FakeController(initial="界界界"))
        self.pipe.send_text(sgr_press(10, 3))
        await eventually(lambda: controller.attempts == ["界界界"])
        self.pipe.send_text("\x1b")
        await task

    async def test_blocked_controller_ignores_save_press(self):
        dialog, task, controller, output = await self.start_term(FakeController(error="This pane has no agent."))
        await self.frame(dialog, output)
        self.pipe.send_text(sgr_press(10, 3))
        await asyncio.sleep(0.15)
        self.assertFalse(task.done())
        self.assertEqual(controller.attempts, [])
        self.pipe.send_text("\r\x1b")
        await task
        self.assertEqual(controller.attempts, [])


class StatusTests(RenameCase):
    async def test_error_line_keeps_text_and_uses_error_color(self):
        controller = FakeController(initial="")
        controller.failure = "Name taken."
        dialog, task, _, output = await self.start_term(controller)
        await self.keys("ab\r", lambda: controller.state == "error")
        screen = await self.frame(dialog, output)
        self.assertTrue(screen.display[4].startswith("Name taken."))
        self.assertEqual(screen.buffer[4][0].fg, PALETTES["catppuccin"]["red"][1:])
        self.assertTrue(screen.display[2].startswith(" ab"))
        self.assertEqual(dialog.editor.text, "ab")
        controller.failure = ""
        await self.keys("\r", task.done)
        self.assertTrue(await task)
        self.assertEqual(controller.writes, ["ab"])

    async def test_long_message_scrolls_until_fully_shown_and_controls_stay(self):
        message = "Use 1-32 characters: a lowercase letter first, then lowercase letters, digits, underscores, or hyphens."
        dialog, task, _, output = await self.start_term(FakeController(initial="", error=message))
        seen = set()
        for tick in range(0, len(message) + 8 + 5):
            dialog._tick = tick
            screen = await self.frame(dialog, output)
            seen.add(screen.display[4].rstrip())
            self.assertIn(SAVE, screen.display[3])
            self.assertIn(CANCEL, screen.display[3])
        self.assertIn(message[:54].rstrip(), seen)
        self.assertTrue(any(line.endswith("hyphens.") for line in seen))
        self.assertGreater(len(seen), 40)
        self.pipe.send_text("\x1b")
        await task

    async def test_long_message_wraps_when_rows_allow(self):
        message = "word " * 20
        dialog, task, _, output = await self.start_term(FakeController(initial="", error=message), rows=8)
        screen = await self.frame(dialog, output)
        self.assertEqual(" ".join(line.strip() for line in screen.display[4:6]), message.strip())
        self.pipe.send_text("\x1b")
        await task

    async def test_save_dimmed_when_blocked(self):
        dialog, task, _, output = await self.start_term(FakeController(error="No agent."))
        screen = await self.frame(dialog, output)
        self.assertEqual(screen.buffer[3][10].fg, PALETTES["catppuccin"]["overlay0"][1:])
        self.assertEqual(screen.buffer[3][10].bg, PALETTES["catppuccin"]["surface0"][1:])
        self.pipe.send_text("\x1b")
        await task

    async def test_busy_lock_blocks_duplicate_submits_edits_mouse_and_cancel(self):
        controller = FakeController()
        controller.release = asyncio.Event()
        dialog, task, _, output = await self.start_term(controller)
        self.pipe.send_text("\r\r\r\x03\x1b")
        await controller.started.wait()
        self.pipe.send_text(sgr_press(10, 3) + sgr_press(20, 3) + sgr_press(0, 0) + "changed")
        await asyncio.sleep(0.15)
        self.assertEqual(controller.attempts, ["agent"])
        self.assertFalse(task.done())
        self.assertEqual(dialog.editor.text, "agent")
        screen = await self.frame(dialog, output)
        self.assertTrue(screen.display[4].startswith("Renaming agent..."))
        controller.release.set()
        self.assertTrue(await asyncio.wait_for(task, 2))
        self.assertEqual(controller.writes, ["agent"])


class SafetyTests(RenameCase):
    async def test_small_terminal_blocks_everything_but_escape(self):
        for columns, rows in ((39, 5), (54, 4), (8, 2)):
            dialog, task, controller, _ = await self.start(output=RecordingOutput(columns=columns, rows=rows))
            self.assertFalse(dialog.has_room)
            self.pipe.send_text("\r\x03x" + sgr_press(1, 1))
            await asyncio.sleep(0.1)
            self.assertEqual(controller.attempts, [])
            self.assertEqual(dialog.editor.text, "agent")
            self.assertFalse(task.done())
            await self.keys("\x1b", task.done)
            self.assertFalse(await task)

    async def test_resize_round_trip_keeps_text(self):
        dialog, task, controller, output = await self.start_term()
        await self.keys("-new", lambda: dialog.editor.text == "agent-new")
        output.size = Size(rows=3, columns=20)
        dialog.app.invalidate()
        await eventually(lambda: dialog.app.layout.current_window is dialog._resize)
        output.size = Size(rows=5, columns=54)
        dialog.app.invalidate()
        await eventually(lambda: dialog.app.layout.current_window is dialog._main)
        await self.keys("\r", task.done)
        self.assertEqual(controller.writes, ["agent-new"])

    async def test_tiny_view_renders_escape_hint(self):
        output = TermOutput(8, 2)
        dialog, task, _, _ = await self.start(output=output)
        screen = await self.frame(dialog, output)
        self.assertIn("Resize", screen.display[0])
        self.assertIn("Esc exit", screen.display[1])
        self.pipe.send_text("\x1b")
        await task

    async def test_modes_restored_after_success_cancel_and_exception(self):
        for outcome in ("success", "cancel", "exception"):
            controller = FakeController()
            if outcome == "exception":
                controller.exception = RuntimeError("unexpected failure")
            _, task, _, output = await self.start(controller)
            await self.keys("\x1b" if outcome == "cancel" else "\r", task.done)
            if outcome == "exception":
                with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
                    await task
            else:
                self.assertEqual(await task, outcome == "success")
            for event in ("mouse-on", "mouse-off", "paste-on", "paste-off", "screen-on", "screen-off", "cursor-on"):
                self.assertIn(event, output.events, (outcome, event))

    async def test_real_stream_enables_and_disables_mouse_reporting(self):
        dialog, task, _, output = await self.start_term()
        self.pipe.send_text("\x1b")
        await task
        stream = output.stream.getvalue()
        self.assertIn("\x1b[?1000h", stream)
        self.assertIn("\x1b[?1006h", stream)
        self.assertIn("\x1b[?1000l", stream)
        self.assertIn("\x1b[?2004l", stream)
        self.assertIn("\x1b[?1049l", stream)


if __name__ == "__main__":
    unittest.main()
