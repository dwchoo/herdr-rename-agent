"""Workspace sync dialog: real VT100 key and mouse streams, layout and cleanup."""

import asyncio
import contextlib
import io
import threading

from dialog_case import DialogCase
from herdr_agent_rename.models import HerdrError
from herdr_agent_rename.sync import RowOutcome, SyncRunner, build_plan
from herdr_agent_rename.theme import PALETTES, theme_from_config
from herdr_agent_rename.ui import SyncDialog, run_sync_dialog
from herdr_agent_rename.ui.sync import column_widths, fit
from sync_support import Fake, agent, observed, pane, world
from ui_support import RecordingOutput, TermOutput, eventually, sgr_press

SPECS = (
    ("w1:p1", "reviewer", None, "t1"),
    ("w1:p2", "coder", "coder", "t1"),
    ("w1:p3", "test_runner", "tester", "t2"),
    ("w1:p4", None, "tester2", "t2"),
    ("w1:p5", "Writer!", "writer", "t3"),
    ("w1:p6", "shell", "-", "t3"),
)
TABS = [{"tab_id": "w1:%s" % t, "workspace_id": "w1", "label": l, "number": n}
        for n, (t, l) in enumerate((("t1", "main"), ("t2", "tests"), ("t3", "docs")), 1)]


class CountingRunner(SyncRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_calls = 0

    async def run(self, progress=None):
        self.run_calls += 1
        return await super().run(progress)


def make_runner(specs=SPECS, cls=CountingRunner, tabs=TABS, label="api"):
    panes, agents = world(*specs)
    first = build_plan("w1", panes, agents, tabs, None, label)
    plan = build_plan("w1", panes, agents, tabs, observed(panes, agents, first), label)
    fake = Fake(panes, agents)
    return cls(fake, plan), fake


def gate_renames(fake):
    """Block every rename in its worker thread until the test releases it."""
    entered, release = threading.Event(), threading.Event()

    def hook(f, pane_id, name):
        entered.set()
        assert release.wait(5)
        return f.apply(pane_id, name)

    fake.rename_hook = hook
    return entered, release


class SyncCase(DialogCase):
    dialog_class = SyncDialog
    default_size = (56, 19)

    async def start_term(self, runner, columns=56, rows=19, theme=None):
        output = TermOutput(columns, rows)
        kwargs = {"theme": theme} if theme else {}
        dialog, task, _, _ = await self.start(runner, output, **kwargs)
        return dialog, task, output

    async def frame(self, dialog, output):
        before = dialog.app.render_counter
        dialog.app.invalidate()
        await eventually(lambda: dialog.app.render_counter > before)
        return output.screen()

    async def wait_phase(self, dialog, phase):
        await eventually(lambda: dialog.phase == phase)
        await asyncio.sleep(0.03)


def find(screen, text):
    return next(y for y, line in enumerate(screen.display) if text in line)


class ConfirmTests(SyncCase):
    async def test_table_columns_unnamed_totals_and_buttons(self):
        runner, fake = make_runner()
        dialog, task, output = await self.start_term(runner, 80, 19)
        screen = await self.frame(dialog, output)
        self.assertIn('Sync agent names · workspace "api" · 6 panes', screen.display[0])
        header = screen.display[1]
        for column in ("Tab", "Pane", "Agent", "→", "Pane name", "Status"):
            self.assertIn(column, header)
        first = screen.display[2]
        self.assertIn("1 main", first)
        self.assertIn("(unnamed)", first)
        self.assertIn("reviewer", first)
        self.assertIn("change", first)
        self.assertIn("matches", screen.display[3])
        self.assertNotIn("(unnamed)", screen.display[3])
        self.assertIn("no pane name", screen.display[5])
        self.assertIn("—", screen.display[5])  # missing pane name
        self.assertIn("invalid name", screen.display[6])
        self.assertIn("no agent", screen.display[7])
        self.assertNotIn("(unnamed)", screen.display[7])
        self.assertIn("2 change · 1 matches · 3 skipped · 0 conflicts/errors", "\n".join(screen.display))
        self.assertIn("[ Yes ]  [ No ]", "\n".join(screen.display))
        self.assertIn("Y/N", "\n".join(screen.display))
        self.assertEqual(fake.writes, [])
        await self.keys("\x1b", task.done)
        self.assertFalse(await task)

    async def test_buttons_totals_and_hints_stay_visible_while_the_table_scrolls(self):
        specs = [("w1:p%d" % n, "name-%02d" % n, "old-%02d" % n) for n in range(1, 31)]
        runner, fake = make_runner(specs, tabs=[])
        dialog, task, output = await self.start_term(runner, 56, 14)
        screen = await self.frame(dialog, output)
        self.assertIn("name-01", "\n".join(screen.display))
        self.pipe.send_text("\x1b[6~\x1b[6~\x1b[6~")  # PageDown
        await eventually(lambda: dialog.top >= 10)
        screen = await self.frame(dialog, output)
        text = "\n".join(screen.display)
        self.assertNotIn("name-01", text)
        self.assertIn("[ Yes ]  [ No ]", text)
        self.assertIn("30 change", text)
        self.assertIn("Esc", screen.display[13])
        selected = f"name-{dialog.cursor + 1:02d}"
        self.assertTrue(any("▸" in line and selected in line for line in screen.display))
        self.assertIn(selected, text)
        self.pipe.send_text("\x1b[F")  # End
        await eventually(lambda: dialog.cursor == 29)
        self.pipe.send_text("\x1b[H")
        await eventually(lambda: dialog.cursor == 0 and dialog.top == 0)
        await self.keys("\x1b[B\x1b[B", lambda: dialog.cursor == 2)
        self.assertEqual(dialog.selected, "no")  # Up/Down never change the button
        self.pipe.send_text("\x1b[<65;10;5M" + "\x1b[<64;10;5M")  # wheel down, up
        await eventually(lambda: dialog.cursor == 2)
        self.assertEqual(fake.writes, [])
        await self.keys("\r", task.done)
        self.assertFalse(await task)
        self.assertEqual(runner.run_calls, 0)

    async def test_selected_row_shows_its_full_names(self):
        label = "start-" + "界" * 40 + "-tail"
        specs = [("w1:p1", label, "x"), ("w1:p2", "ok", "y")]
        runner, fake = make_runner(specs, tabs=[])
        dialog, task, output = await self.start_term(runner, 56, 19)
        screen = await self.frame(dialog, output)
        self.assertNotIn("-tail", "\n".join(screen.display[:10]).replace("…", ""))
        detail = "\n".join(screen.display[13:15])
        self.assertTrue("start-" in detail)
        await self.keys("\x1b", task.done)

    async def test_default_no_enter_and_other_cancel_keys_never_run(self):
        for key in ("\r", "n", "N", "\x1b", "\x03"):
            runner, fake = make_runner()
            _, task, _ = await self.start_term(runner)
            await self.keys(key, task.done)
            self.assertFalse(await task)
            self.assertEqual((runner.run_calls, fake.writes, runner.state), (0, [], "ready"))

    async def test_tab_right_left_and_shift_tab_select_yes(self):
        for key in ("\t", "\x1b[C", "\x1b[D", "\x1b[Z"):
            runner, fake = make_runner()
            dialog, task, _ = await self.start_term(runner)
            self.assertEqual(dialog.selected, "no")
            await self.keys(key, lambda: dialog.selected == "yes")
            await self.keys("\r", lambda: runner.state == "done")
            self.assertEqual(runner.run_calls, 1)
            self.assertEqual(fake.writes, [("w1:p1", "reviewer"), ("w1:p3", "test_runner")])
            await self.wait_phase(dialog, "done")
            await self.keys("\r", task.done)
            self.assertTrue(await task)

    async def test_y_and_uppercase_y_start_one_batch(self):
        for key in ("y", "Y"):
            runner, fake = make_runner()
            dialog, task, _ = await self.start_term(runner)
            self.pipe.send_text(key + "\r\ry" + key)
            await self.wait_phase(dialog, "done")
            self.assertEqual(runner.run_calls, 1)
            self.assertEqual(len(fake.writes), 2)
            await self.keys("\x1b", task.done)
            self.assertTrue(await task)

    async def test_mouse_press_on_yes_runs_once_even_when_repeated(self):
        runner, fake = make_runner()
        dialog, task, output = await self.start_term(runner)
        screen = await self.frame(dialog, output)
        row = find(screen, "[ Yes ]")
        x = screen.display[row].index("[ Yes ]") + 2
        self.pipe.send_text(sgr_press(x, row) + sgr_press(x, row))
        await self.wait_phase(dialog, "done")
        self.pipe.send_text(sgr_press(x, row))  # now the Close button's neighbourhood
        await asyncio.sleep(0.05)
        self.assertEqual(runner.run_calls, 1)
        self.assertEqual(len(fake.writes), 2)
        screen = await self.frame(dialog, output)
        row = find(screen, "[ Close ]")
        self.pipe.send_text(sgr_press(screen.display[row].index("[ Close ]") + 2, row))
        self.assertTrue(await asyncio.wait_for(task, 2))

    async def test_mouse_press_on_no_cancels_and_row_click_selects(self):
        runner, fake = make_runner()
        dialog, task, output = await self.start_term(runner)
        screen = await self.frame(dialog, output)
        self.pipe.send_text(sgr_press(10, 4))
        await eventually(lambda: dialog.cursor == 2)
        self.assertEqual(runner.run_calls, 0)
        row = find(screen, "[ No ]")
        self.pipe.send_text(sgr_press(screen.display[row].index("[ No ]") + 2, row))
        self.assertFalse(await asyncio.wait_for(task, 2))
        self.assertEqual((runner.run_calls, fake.writes), (0, []))


class RunTests(SyncCase):
    async def test_progress_then_done_summary_and_close(self):
        runner, fake = make_runner()
        entered, release = gate_renames(fake)
        dialog, task, output = await self.start_term(runner, 80, 19)
        self.pipe.send_text("y")
        await asyncio.to_thread(entered.wait, 3)
        await eventually(lambda: dialog.current == "w1:p1")
        screen = await self.frame(dialog, output)
        text = "\n".join(screen.display)
        self.assertIn("syncing…", screen.display[2])
        self.assertIn("[ Stop ]", text)
        self.assertNotIn("[ Yes ]", text)
        self.assertIn("Syncing 1 of 6", text)
        self.pipe.send_text("\r\r y\tn")  # nothing but Esc/Ctrl+C/Stop reacts while running
        await asyncio.sleep(0.05)
        self.assertFalse(runner.cancel_requested or task.done())
        release.set()
        await self.wait_phase(dialog, "done")
        self.assertEqual(runner.run_calls, 1)
        self.assertEqual(len(fake.writes), 2)
        await self.keys("\x1b", task.done)
        self.assertTrue(await task)

    async def test_full_batch_outcomes_and_return_value(self):
        runner, fake = make_runner()
        dialog, task, output = await self.start_term(runner, 80, 19)
        self.pipe.send_text("y")
        await self.wait_phase(dialog, "done")
        screen = await self.frame(dialog, output)
        text = "\n".join(screen.display)
        self.assertIn("synced", screen.display[2])
        self.assertIn("already matching", screen.display[3])
        self.assertIn("synced", screen.display[4])
        self.assertIn("skipped: no pane name", screen.display[5])
        self.assertIn("skipped: invalid name", screen.display[6])
        self.assertIn("skipped: no agent", screen.display[7])
        self.assertIn("2 synced, 1 already matching, 3 skipped", text)
        self.assertIn("[ Close ]", text)
        self.assertNotIn("[ Yes ]", text)
        self.assertNotIn("[ Stop ]", text)
        await self.keys("\r", task.done)
        self.assertTrue(await task)
        self.assertEqual(runner.report.summary(), "2 synced, 1 already matching, 3 skipped")

    async def test_cancel_during_run_waits_for_the_in_flight_row(self):
        for key in ("\x1b", "\x03"):
            runner, fake = make_runner()
            entered, release = gate_renames(fake)
            dialog, task, output = await self.start_term(runner, 80, 19)
            self.pipe.send_text("y")
            await asyncio.to_thread(entered.wait, 3)
            self.pipe.send_text(key)
            await eventually(lambda: runner.cancel_requested)
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            screen = await self.frame(dialog, output)
            self.assertIn("Stopping", "\n".join(screen.display))
            release.set()
            await self.wait_phase(dialog, "done")
            self.assertFalse(task.done())
            self.assertTrue(runner.report.canceled)
            self.assertEqual(fake.writes, [("w1:p1", "reviewer")])
            screen = await self.frame(dialog, output)
            self.assertIn("not attempted", screen.display[4])
            self.assertIn("Canceled.", "\n".join(screen.display))
            await self.keys("\x1b", task.done)
            self.assertTrue(await task)  # the first row was synced

    async def test_stop_by_mouse(self):
        runner, fake = make_runner()
        entered, release = gate_renames(fake)
        dialog, task, output = await self.start_term(runner, 80, 19)
        self.pipe.send_text("y")
        await asyncio.to_thread(entered.wait, 3)
        screen = await self.frame(dialog, output)
        row = find(screen, "[ Stop ]")
        self.pipe.send_text(sgr_press(screen.display[row].index("[ Stop ]") + 2, row))
        await eventually(lambda: runner.cancel_requested)
        release.set()
        await self.wait_phase(dialog, "done")
        await self.keys("\r", task.done)
        self.assertTrue(await task)

    async def test_a_batch_without_a_synced_row_returns_false(self):
        runner, fake = make_runner()
        runner, fake = make_runner([("w1:p1", "aa", "x")], tabs=[])
        fake.fail["pane_list"] = HerdrError("Could not reach the pane.", code="other")
        dialog, task, output = await self.start_term(runner, 80, 19)
        self.pipe.send_text("y")
        await self.wait_phase(dialog, "done")
        screen = await self.frame(dialog, output)
        self.assertIn("failed: Could not reach", screen.display[2])
        await self.keys("\r", task.done)
        self.assertFalse(await task)


class OtherModeTests(SyncCase):
    async def test_info_mode_has_only_close_and_never_writes(self):
        runner, fake = make_runner([("w1:p1", "Bad Name", "x"), ("w1:p2", None, "y")], tabs=[])
        self.assertEqual(runner.mode, "info")
        dialog, task, output = await self.start_term(runner, 56, 14)
        screen = await self.frame(dialog, output)
        text = "\n".join(screen.display)
        self.assertIn("[ Close ]", text)
        self.assertNotIn("[ Yes ]", text)
        self.assertNotIn("[ No ]", text)
        self.assertIn("invalid name", text)
        self.pipe.send_text("y\t\x1b[C" + sgr_press(1, 1))
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        await self.keys("\r", task.done)
        self.assertFalse(await task)
        self.assertEqual((runner.run_calls, fake.writes, fake.calls), (0, [], []))

    async def test_info_mode_closes_on_escape_and_click(self):
        for how in ("esc", "click"):
            runner, _ = make_runner([("w1:p1", "Bad Name", "x")], tabs=[])
            dialog, task, output = await self.start_term(runner, 56, 14)
            if how == "esc":
                await self.keys("\x1b", task.done)
            else:
                screen = await self.frame(dialog, output)
                row = find(screen, "[ Close ]")
                self.pipe.send_text(sgr_press(screen.display[row].index("[ Close ]") + 1, row))
            self.assertFalse(await asyncio.wait_for(task, 2))

    async def test_error_mode_shows_the_message_with_close(self):
        fake = Fake([], [])
        runner = CountingRunner(fake, None, "Could not connect to Herdr.\x1b[31m " + "details " * 30)
        dialog, task, output = await self.start_term(runner, 56, 14)
        screen = await self.frame(dialog, output)
        text = "\n".join(screen.display)
        self.assertIn("Could not connect to Herdr.", text)
        self.assertIn("\\x1b[31m", text)
        self.assertIn("[ Close ]", text)
        self.assertNotIn("[ Yes ]", text)
        self.pipe.send_text("y")
        await asyncio.sleep(0.03)
        await self.keys("\r", task.done)
        self.assertFalse(await task)
        self.assertEqual((runner.run_calls, fake.calls), (0, []))


class LayoutTests(SyncCase):
    async def test_narrow_terminal_collapses_tab_then_pane(self):
        runner, _ = make_runner()
        dialog, task, output = await self.start_term(runner, 80, 19)
        wide = (await self.frame(dialog, output)).display[1].split()
        self.assertEqual(wide[:2], ["Tab", "Pane"])
        await self.keys("\x1b", task.done)
        rows = make_runner()[0].plan.rows
        statuses = ["skipped: no pane name"]
        shown = [tuple(column_widths(avail, rows, statuses))[:2] for avail in range(30, 90)]
        self.assertEqual(shown[0], ("agent", "arrow"))
        self.assertIn(("pane", "agent"), shown)
        self.assertEqual(shown[-1], ("tab", "pane"))
        self.assertLess(shown.index(("pane", "agent")), shown.index(("tab", "pane")))
        runner, _ = make_runner()
        dialog, task, output = await self.start_term(runner, 40, 12)
        screen = await self.frame(dialog, output)
        narrow = screen.display[1].split()
        self.assertEqual(narrow[:2], ["Agent", "→"])
        self.assertIn("Status", narrow)
        self.assertIn("[ Yes ]  [ No ]", "\n".join(screen.display))
        self.assertIn("(unnamed)", screen.display[2])
        await self.keys("\x1b", task.done)

    def test_column_widths_always_fit(self):
        runner, _ = make_runner()
        for avail in range(36, 120):
            widths = column_widths(avail, runner.plan.rows, ["syncing…", "skipped: no pane name"])
            used = sum(widths.values()) + len(widths) - 1
            self.assertLessEqual(used, avail, avail)
        self.assertEqual(fit("界界界", 5), "界界…")
        self.assertEqual(fit("ab", 4), "ab  ")

    async def test_theme_colors_for_each_theme(self):
        for name in ("catppuccin", "dracula"):
            p = PALETTES[name]
            theme = theme_from_config({"theme": {"name": name}})
            runner, _ = make_runner()
            dialog, task, output = await self.start_term(runner, 80, 19, theme=theme)
            screen = await self.frame(dialog, output)
            cell = lambda x, y: screen.buffer[y][x]
            hexa = lambda token: p[token][1:]
            self.assertEqual((cell(2, 0).bg, cell(2, 0).fg, cell(2, 0).bold), (hexa("panel_bg"), hexa("text"), True))
            self.assertEqual(cell(79, 18).bg, hexa("panel_bg"))
            self.assertEqual(cell(5, 1).fg, hexa("subtext0"))
            self.assertEqual(cell(40, 2).bg, hexa("selection_bg"))  # cursor row
            self.assertEqual(cell(40, 4).bg, hexa("panel_bg"))
            row = find(screen, "[ No ]")
            x_no, x_yes = screen.display[row].index("[ No ]"), screen.display[row].index("[ Yes ]")
            self.assertEqual((cell(x_no, row).bg, cell(x_no, row).fg), (hexa("accent"), hexa("panel_bg")))
            self.assertEqual((cell(x_yes, row).bg, cell(x_yes, row).fg), (hexa("surface0"), hexa("text")))
            status_x = screen.display[4].index("change")
            self.assertEqual(cell(status_x, 4).fg, hexa("yellow"))
            self.pipe.send_text("\t")
            await eventually(lambda: dialog.selected == "yes")
            screen = await self.frame(dialog, output)
            self.assertEqual(screen.buffer[row][x_yes].bg, hexa("accent"))
            self.assertEqual(screen.buffer[row][x_no].bg, hexa("surface0"))
            await self.keys("\x1b", task.done)

    async def test_too_small_terminal_shows_message_blocks_actions_and_allows_escape(self):
        for columns, rows in ((56, 9), (39, 19), (8, 2)):
            runner, fake = make_runner()
            dialog, task, _, = (await self.start(runner, RecordingOutput(columns=columns, rows=rows)))[:3]
            self.assertFalse(dialog.has_room)
            self.pipe.send_text("\t\r\x1b[Zy " + sgr_press(1, 1))
            await asyncio.sleep(0.05)
            self.assertEqual((runner.run_calls, fake.writes), (0, []))
            await self.keys("\x1b", task.done)
            self.assertFalse(await task)

    async def test_resizing_back_restores_the_dialog(self):
        runner, _ = make_runner()
        output = TermOutput(56, 19)
        dialog, task, _, _ = await self.start(runner, output)
        output.size = type(output.size)(rows=5, columns=30)
        self.assertFalse(dialog.has_room)
        output.size = type(output.size)(rows=19, columns=56)
        self.assertTrue(dialog.has_room)
        await self.keys("\x1b", task.done)

    async def test_modes_restored_on_all_exits(self):
        class Exploding(CountingRunner):
            async def run(self, progress=None):
                raise RuntimeError("unexpected failure")

        for outcome in ("success", "cancel", "exception"):
            runner, fake = make_runner(cls=Exploding if outcome == "exception" else CountingRunner)
            _, task, _, output = await self.start(runner)
            if outcome == "cancel":
                await self.keys("\x1b", task.done)
                self.assertFalse(await task)
            elif outcome == "success":
                self.pipe.send_text("y")
                await eventually(lambda: runner.state == "done")
                await asyncio.sleep(0.05)
                self.pipe.send_text("\r")
                self.assertTrue(await asyncio.wait_for(task, 2))
            else:
                self.pipe.send_text("y")
                await eventually(lambda: runner.state == "done")
                await asyncio.sleep(0.05)
                self.pipe.send_text("\r")
                self.assertFalse(await asyncio.wait_for(task, 2))
            for event in ("mouse-on", "mouse-off", "screen-on", "screen-off", "cursor-on", "paste-off"):
                self.assertIn(event, output.events)

    async def test_unexpected_runner_error_shows_done_state_and_logs_after_exit(self):
        class Exploding(CountingRunner):
            async def run(self, progress=None):
                raise RuntimeError("unexpected failure")

        runner, fake = make_runner(cls=Exploding)
        dialog, task, output = await self.start_term(runner)
        self.pipe.send_text("y")
        await self.wait_phase(dialog, "done")
        screen = await self.frame(dialog, output)
        text = "\n".join(screen.display)
        self.assertIn("unexpected error", text)
        self.assertIn("Close", text)
        self.assertEqual({r.outcome for r in dialog.report.results} - {RowOutcome.MATCHING, RowOutcome.SKIPPED}, {RowOutcome.NOT_ATTEMPTED})
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.pipe.send_text("\r")
            self.assertFalse(await asyncio.wait_for(task, 2))
        self.assertIn("unexpected failure", stderr.getvalue())

    async def test_run_sync_dialog_entry_point(self):
        task = asyncio.create_task(run_sync_dialog(make_runner()[0]))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
