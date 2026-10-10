"""Workspace sync dialog: preview table, confirmation, progress and outcomes.

One popup application for the four ``SyncRunner`` modes:

* confirm: scrollable table, totals, ``[ Yes ]  [ No ]`` (No selected first).
  Nothing is written before Yes. Yes calls ``runner.run`` exactly once.
* running: ``[ Stop ]`` replaces Yes/No. Stop, Esc and Ctrl+C only request a
  cancellation; the popup stays open until the in-flight row is reconciled.
* done: per-row outcomes, the summary line and a single ``[ Close ]``.
* info / error: the same table (or the error text) with ``[ Close ]`` only.

Layout (rows from the top): title, column header, table, detail of the cursor
row (full names), totals, message, buttons, hints. Only the table scrolls.
The Tab column collapses first and the Pane column second on narrow terminals.
Everything is rendered from local state; painting never touches the API.
"""

from __future__ import annotations

import sys
import traceback
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

from ..sync import RowOutcome, RowResult, RowStatus, SyncProgress, SyncReport, SyncRow, SyncRunner
from ..theme import Theme, load_theme
from .common import MIN_WIDTH, bounded_text, display_text, pad_cells, resize_text, wrap_cells

MIN_ROWS = 10
BUTTON_GAP = 2
MISSING = "—"
UNNAMED = "(unnamed)"
ARROW = "→"
STATUS_CAP = 24
STATUS_MIN = 10
SIDE_MIN = 9

PLANNED = {
    RowStatus.ELIGIBLE: ("change", "warn"),
    RowStatus.MATCHING: ("matches", "ok"),
    RowStatus.NO_PANE_NAME: ("no pane name", "dim"),
    RowStatus.NO_AGENT: ("no agent", "dim"),
    RowStatus.INVALID_LABEL: ("invalid name", "bad"),
    RowStatus.CONFLICT: ("conflict", "bad"),
    RowStatus.LOOKUP_FAILED: ("lookup failed", "bad"),
}
OUTCOME_STYLE = {
    RowOutcome.SYNCED: "ok", RowOutcome.MATCHING: "ok", RowOutcome.SKIPPED: "warn",
    RowOutcome.FAILED: "bad", RowOutcome.UNCERTAIN: "bad", RowOutcome.NOT_ATTEMPTED: "dim",
}
OUTCOME_TEXT = {
    RowOutcome.SYNCED: "synced", RowOutcome.MATCHING: "already matching",
    RowOutcome.UNCERTAIN: "uncertain", RowOutcome.NOT_ATTEMPTED: "not attempted",
}


def fit(text: str, width: int) -> str:
    """Pad or cut to exactly ``width`` cells, marking a cut with an ellipsis."""
    width = max(0, width)
    if get_cwidth(text) <= width:
        return pad_cells(text, width)
    out, cells = "", 0
    for char in text:
        step = get_cwidth(char)
        if cells + step > width - 1:
            break
        out += char
        cells += step
    return pad_cells(out + "…", width)


def tab_text(row: SyncRow) -> str:
    label = display_text(row.tab_label) if row.tab_label else ""
    number = str(row.tab_number) if row.tab_number is not None else ""
    if number and label and number != label:
        return f"{number} {label}"
    return label or number or MISSING


def agent_text(row: SyncRow) -> str:
    if row.status is RowStatus.NO_AGENT:
        return MISSING
    return display_text(row.agent_name) if row.agent_name else UNNAMED


def label_text(row: SyncRow) -> str:
    return display_text(row.label) if row.label else MISSING


def column_widths(avail: int, rows: tuple[SyncRow, ...], statuses: list[str]) -> dict[str, int]:
    """Cell widths of the visible columns; hidden columns are absent."""
    natural = {
        "tab": min(14, max([3] + [get_cwidth(tab_text(r)) for r in rows])),
        "pane": min(10, max([4] + [get_cwidth(r.pane_id) for r in rows])),
        "agent": max([5] + [get_cwidth(agent_text(r)) for r in rows]),
        "label": max([9] + [get_cwidth(label_text(r)) for r in rows]),
    }
    status_natural = min(STATUS_CAP, max([6] + [get_cwidth(s) for s in statuses]))
    for shown in (("tab", "pane"), ("pane",), ()):
        fixed = sum(natural[name] for name in shown)
        columns = len(shown) + 4  # agent, arrow, label, status
        rest = avail - fixed - 1 - (columns - 1)
        if rest - STATUS_MIN >= 2 * SIDE_MIN or not shown:
            break
    wanted = min(2 * 12, natural["agent"] + natural["label"])
    status = min(status_natural, max(STATUS_MIN, rest - wanted)) if rest > 0 else STATUS_MIN
    sides = max(2 * SIDE_MIN, rest - status)
    agent, label = natural["agent"], natural["label"]
    if agent + label > sides:
        if agent < sides // 2:
            label = sides - agent
        elif label < sides // 2:
            agent = sides - label
        else:
            agent, label = sides // 2, sides - sides // 2
    widths = {name: natural[name] for name in shown}
    widths.update({"agent": agent, "arrow": 1, "label": label, "status": status})
    return widths


class SyncControl(FormattedTextControl):
    def __init__(self, dialog: SyncDialog) -> None:
        super().__init__(text="", focusable=True, show_cursor=False)
        self.dialog = dialog

    def create_content(self, width: int, height: int) -> UIContent:
        return self.dialog.render(width, height)

    def mouse_handler(self, mouse_event: MouseEvent) -> Any:
        kind = mouse_event.event_type
        if kind == MouseEventType.SCROLL_UP:
            self.dialog.move(-3)
        elif kind == MouseEventType.SCROLL_DOWN:
            self.dialog.move(3)
        elif kind == MouseEventType.MOUSE_DOWN and mouse_event.button == MouseButton.LEFT:
            self.dialog.press(mouse_event.position.x, mouse_event.position.y)
        else:
            return NotImplemented
        return None


class SyncDialog:
    def __init__(
        self,
        runner: SyncRunner,
        *,
        input: Input | None = None,
        output: Output | None = None,
        theme: Theme | None = None,
    ) -> None:
        self.runner = runner
        self.plan = runner.plan
        self.rows: tuple[SyncRow, ...] = runner.plan.rows if runner.plan is not None else ()
        self.theme = theme or load_theme()
        self.selected = "no"  # confirm-mode button: "yes" or "no"
        self.cursor = 0
        self.top = 0
        self.follow = False
        self.results: dict[str, RowResult] = {}
        self.current: str | None = None
        self.done_count = 0
        self._started = False
        self._finished = False
        self._cancel_pending = False
        self.report = None
        self.crash: Exception | None = None
        self._body = 1
        self._table: tuple[int, int] = (0, 0)  # first table row, row count
        self._button_row = 0
        self._buttons: list[tuple[str, int, int]] = []
        self.control = SyncControl(self)
        self._resize_control = FormattedTextControl(self._resize_text, focusable=True)
        self._main = Window(self.control, style="class:panel", wrap_lines=False)
        self._resize = Window(self._resize_control, style="class:panel", wrap_lines=True)
        root = DynamicContainer(lambda: self._main if self.has_room else self._resize)
        self.app: Application[bool] = Application(
            layout=Layout(root, focused_element=self._main),
            key_bindings=self._bindings(),
            style=Style.from_dict(self._styles()),
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

    def _styles(self) -> dict[str, str]:
        base = self.theme.styles()
        p, spec = self.theme.palette, self.theme.spec
        select = p["selection_bg"] or p["surface0"]
        styles = {
            "panel": base["panel"], "title": base["title"], "hint": base["hint"],
            "error": base["error"], "primary": base["primary"], "secondary": base["secondary"],
            "disabled": base["disabled"],
            "head": spec(p["subtext0"], p["panel_bg"], True),
            "detail": spec(p["text"], p["surface0"]),
            "selected": spec(p["text"], select, True),
        }
        for name, token in (("ok", "green"), ("warn", "yellow"), ("bad", "red"),
                            ("dim", "overlay1"), ("run", "accent")):
            styles[name] = spec(p[token], p["panel_bg"])
            styles["sel." + name] = spec(p[token], select, True)
        return styles

    # State ---------------------------------------------------------------

    @property
    def phase(self) -> str:
        if self.runner.mode != "confirm":
            return self.runner.mode  # "info" or "error"
        if not self._started:
            return "confirm"
        return "done" if self._finished else "running"

    @property
    def has_room(self) -> bool:
        app = getattr(self, "app", None)
        if app is None:
            return True
        size = app.output.get_size()
        return size.columns >= MIN_WIDTH and size.rows >= MIN_ROWS

    @property
    def result(self) -> bool:
        return bool(self.report and self.report.counts[RowOutcome.SYNCED])

    # Row text ------------------------------------------------------------

    def status_of(self, row: SyncRow) -> tuple[str, str]:
        """Status column text and style name from local state only."""
        if self.current == row.pane_id and row.pane_id not in self.results:
            return "syncing…", "run"
        result = self.results.get(row.pane_id)
        if result is None:
            return PLANNED[row.status]
        outcome, style = result.outcome, OUTCOME_STYLE[result.outcome]
        if outcome in OUTCOME_TEXT:
            return OUTCOME_TEXT[outcome], style
        if outcome is RowOutcome.SKIPPED and row.status not in (RowStatus.ELIGIBLE, RowStatus.MATCHING):
            return "skipped: " + PLANNED[row.status][0], style
        return f"{outcome.value}: {display_text(result.detail)}", style

    def detail_of(self, row: SyncRow) -> str:
        result = self.results.get(row.pane_id)
        reason = result.detail if result else row.detail
        head = f"{tab_text(row)} · {row.pane_id} · {agent_text(row)} {ARROW} {label_text(row)}"
        return head + (" · " + display_text(reason) if reason else "")

    def totals(self, width: int) -> str:
        counts = {status: 0 for status in RowStatus}
        for row in self.rows:
            counts[row.status] += 1
        change, matches = counts[RowStatus.ELIGIBLE], counts[RowStatus.MATCHING]
        skipped = counts[RowStatus.NO_PANE_NAME] + counts[RowStatus.NO_AGENT] + counts[RowStatus.INVALID_LABEL]
        trouble = counts[RowStatus.CONFLICT] + counts[RowStatus.LOOKUP_FAILED]
        for labels in (("change", "matches", "skipped", "conflicts/errors"),
                       ("change", "match", "skip", "conflicts"),
                       ("chg", "ok", "skip", "err")):
            text = " · ".join(f"{n} {t}" for n, t in zip((change, matches, skipped, trouble), labels))
            if get_cwidth(text) <= width:
                return text
        return text

    def message(self) -> tuple[str, str]:
        phase = self.phase
        if phase == "confirm":
            count = len(self.plan.eligible) if self.plan else 0
            return "hint", f"Yes renames {count} agent{'s' if count != 1 else ''} to the pane names shown."
        if phase == "info":
            return "hint", "Nothing can be synced. Review the rows above, then close."
        if phase == "running":
            if self.runner.cancel_requested or self._cancel_pending:
                return "hint", "Stopping after the current pane…"
            return "hint", f"Syncing {min(self.done_count + 1, len(self.rows))} of {len(self.rows)}…"
        if phase == "done":
            report = self.report
            if self.crash is not None:
                return "error", ("An unexpected error stopped the sync; the remaining panes were not attempted. "
                                 + (report.summary() if report else ""))
            text = report.summary() if report else ""
            if report and report.canceled:
                text = "Canceled. " + text
            if report and report.stopped:
                text += ". Stopped: " + display_text(report.stopped)
            bad = report and (report.counts[RowOutcome.FAILED] or report.counts[RowOutcome.UNCERTAIN])
            return ("error" if bad else "hint"), text or "Nothing was changed."
        return "error", ""

    def hints(self, width: int) -> str:
        phase = self.phase
        if phase == "confirm":
            options = ("↑↓ PgUp/PgDn scroll · ←→/Tab select · Y/N · Enter · Esc",
                       "↑↓ PgUp/PgDn scroll · ←→ select · Y/N · Esc",
                       "↑↓ scroll · ←→ select · Y/N · Esc",
                       "↑↓ ←→ Y/N Enter Esc")
        elif phase == "running":
            options = ("↑↓ PgUp/PgDn scroll · Esc: stop after this pane",
                       "↑↓ scroll · Esc: stop", "Esc: stop")
        else:
            options = ("↑↓ PgUp/PgDn scroll · Enter/Esc: close", "↑↓ scroll · Enter: close", "Enter: close")
        return next((o for o in options if get_cwidth(o) <= width), options[-1])

    # Rendering -----------------------------------------------------------

    def _button_labels(self) -> list[tuple[str, str, str]]:
        phase = self.phase
        if phase == "confirm":
            return [("yes", "[ Yes ]", "primary" if self.selected == "yes" else "secondary"),
                    ("no", "[ No ]", "primary" if self.selected == "no" else "secondary")]
        if phase == "running":
            stopping = self.runner.cancel_requested or self._cancel_pending
            return [("stop", "[ Stopping… ]" if stopping else "[ Stop ]", "disabled" if stopping else "primary")]
        return [("close", "[ Close ]", "primary")]

    def render(self, width: int, height: int) -> UIContent:
        width, height = max(1, width), max(1, height)
        blank = [("class:panel", " " * width)]
        lines: list[list[tuple[str, str]]] = [list(blank) for _ in range(height)]
        inner = max(1, width - 2)
        detail_rows = 2 if height >= 14 else 1
        table_rows = max(1, height - 6 - detail_rows)
        first_table = 2
        self._table = (first_table, table_rows)
        row_no = lambda n: min(n, height - 1)

        title = "Sync agent names"
        if self.plan is not None:
            label = display_text(self.plan.workspace_label) if self.plan.workspace_label else ""
            if label:
                title += f' · workspace "{label}"'
            title += f" · {len(self.rows)} pane{'s' if len(self.rows) != 1 else ''}"
        lines[0] = [("class:title", pad_cells(" " + fit(title, inner).rstrip(), width))]

        if self.phase == "error":
            wrapped = wrap_cells(display_text(self.runner.error or "The workspace could not be synced."), inner)
            for index, text in enumerate(wrapped[:table_rows + 1]):
                lines[row_no(1 + index)] = [("class:error", pad_cells(" " + text, width))]
        else:
            statuses = [self.status_of(r)[0] for r in self.rows]
            widths = column_widths(inner - 1, self.rows, statuses)
            self.cursor = min(max(0, self.cursor), max(0, len(self.rows) - 1))
            self.top = min(max(0, self.top), max(0, len(self.rows) - table_rows))
            if self.cursor < self.top:
                self.top = self.cursor
            elif self.cursor >= self.top + table_rows:
                self.top = self.cursor - table_rows + 1
            titles = {"tab": "Tab", "pane": "Pane", "agent": "Agent", "arrow": ARROW,
                      "label": "Pane name", "status": "Status"}
            header = "  " + " ".join(fit(titles[name], w) for name, w in widths.items())
            lines[1] = [("class:head", pad_cells(header, width))]
            for offset in range(table_rows):
                index = self.top + offset
                if index >= len(self.rows):
                    break
                lines[first_table + offset] = self._table_row(self.rows[index], widths, width, index == self.cursor)
            if not self.rows:
                lines[first_table] = [("class:hint", pad_cells(" No panes in this workspace.", width))]
            if self.rows:
                detail = bounded_text(self.detail_of(self.rows[self.cursor]), inner, detail_rows).split("\n")
                for offset in range(detail_rows):
                    text = detail[offset] if offset < len(detail) else ""
                    lines[first_table + table_rows + offset] = [("class:detail", pad_cells(" " + text, width))]

        base = first_table + table_rows + detail_rows
        lines[base] = [("class:hint", pad_cells(" " + self.totals(inner), width))]
        style, text = self.message()
        wrapped = wrap_cells(text, inner)
        shown = fit(wrapped[0], inner).rstrip() if wrapped else ""
        lines[base + 1] = [("class:" + style, pad_cells(" " + shown, width))]

        labels = self._button_labels()
        total = sum(get_cwidth(t) for _, t, _ in labels) + BUTTON_GAP * (len(labels) - 1)
        x = max(0, (width - total) // 2)
        row: list[tuple[str, str]] = []
        self._buttons = []
        position = 0
        for name, text, style in labels:
            row.append(("class:panel", " " * (x - position)))
            row.append(("class:" + style, text))
            self._buttons.append((name, x, x + get_cwidth(text)))
            x += get_cwidth(text)
            position = x
            x += BUTTON_GAP
        row.append(("class:panel", " " * max(0, width - position)))
        self._button_row = base + 2
        lines[row_no(base + 2)] = row
        lines[row_no(base + 3)] = [("class:hint", pad_cells(" " + self.hints(inner), width))]
        return UIContent(
            get_line=lambda number: lines[number],
            line_count=height,
            cursor_position=Point(x=0, y=0),
            show_cursor=False,
        )

    def _table_row(self, row: SyncRow, widths: dict[str, int], width: int, selected: bool) -> list[tuple[str, str]]:
        base = "selected" if selected else "panel"
        status, style = self.status_of(row)
        cells = {
            "tab": tab_text(row), "pane": row.pane_id, "agent": agent_text(row),
            "arrow": ARROW if row.label and row.status is not RowStatus.NO_AGENT else "",
            "label": label_text(row), "status": status,
        }
        fragments: list[tuple[str, str]] = [(f"class:{base}", "▸ " if selected else "  ")]
        used = 2
        names = list(widths)
        for index, name in enumerate(names):
            if index:
                fragments.append((f"class:{base}", " "))
                used += 1
            cell_style = base
            if name == "status":
                cell_style = ("sel." if selected else "") + style
            elif name in ("agent", "label") and cells[name] in (MISSING, UNNAMED):
                cell_style = ("sel." if selected else "") + "dim"
            fragments.append((f"class:{cell_style}", fit(cells[name], widths[name])))
            used += widths[name]
        fragments.append((f"class:{base}", " " * max(0, width - used)))
        return fragments

    def _resize_text(self) -> str:
        return resize_text(self.app.output.get_size(), self.phase == "running")

    def _handle_resize(self, app: Application[bool]) -> None:
        target = self._main if self.has_room else self._resize
        if app.layout.current_window is not target:
            app.layout.focus(target)

    # Navigation ----------------------------------------------------------

    def move(self, delta: int, absolute: bool = False) -> None:
        if not self.has_room or not self.rows:
            return
        self.follow = False
        target = delta if absolute else self.cursor + delta
        self.cursor = min(max(0, target), len(self.rows) - 1)
        self.app.invalidate()

    def _page(self) -> int:
        return max(1, self._table[1] - 1)

    def _select(self) -> None:
        if self.phase == "confirm" and self.has_room:
            self.selected = "yes" if self.selected == "no" else "no"
            self.app.invalidate()

    # Actions -------------------------------------------------------------

    def _stop(self) -> None:
        """Ask for a cancellation; the popup closes only after the batch ends."""
        if not self.runner.request_cancel():
            self._cancel_pending = True
        self.app.invalidate()

    def _activate(self, name: str) -> None:
        phase = self.phase
        if phase == "confirm":
            if name == "yes" and self.has_room:
                self._start()
            elif name == "no":
                self.app.exit(result=False)
        elif phase == "running":
            if name == "stop":
                self._stop()
        elif name == "close":
            self.app.exit(result=self.result)

    def _enter(self) -> None:
        # Enter never stops a running batch: a repeated Enter must be harmless.
        if self.phase == "confirm":
            self._activate(self.selected)
        elif self.phase != "running":
            self._activate("close")

    def _escape(self) -> None:
        phase = self.phase
        if phase == "confirm":
            self.app.exit(result=False)
        elif phase == "running":
            self._stop()
        else:
            self.app.exit(result=self.result)

    def _start(self) -> None:
        if self._started or self.phase != "confirm":
            return
        # Latch before scheduling: several keys can arrive in one batch.
        self._started = True
        self.follow = True
        self.app.invalidate()
        self.app.create_background_task(self._run())

    async def _run(self) -> None:
        try:
            self.report = await self.runner.run(self._progress)
            self.report = self.report or self.runner.report
        except Exception as exc:
            # Never exit with a traceback: show what was done and close cleanly.
            self.crash = exc
            self.runner.state = "done"
            outcomes = []
            for row in self.rows:
                result = self.results.get(row.pane_id) or RowResult(
                    row, RowOutcome.NOT_ATTEMPTED, "Stopped by an unexpected error.")
                self.results[row.pane_id] = result
                outcomes.append(result)
            self.report = SyncReport(tuple(outcomes), False, "An unexpected error occurred.")
            self._finished = True
            self.current = None
            self.app.invalidate()
            return
        self._finished = True
        self.current = None
        self.app.invalidate()

    def _progress(self, event: SyncProgress) -> None:
        if self._cancel_pending:
            self._cancel_pending = not self.runner.request_cancel()
        if event.result is None:
            self.current = event.row.pane_id
            if self.follow:
                self.cursor = event.index
        else:
            self.results[event.row.pane_id] = event.result
            self.done_count = event.index + 1
        self.app.invalidate()

    def press(self, x: int, y: int) -> None:
        """Left-button press at a dialog cell: a button, else select a row."""
        if y == self._button_row:
            for name, start, end in self._buttons:
                if start <= x < end:
                    self._activate(name)
                    return
            return
        first, count = self._table
        index = self.top + y - first
        if first <= y < first + count and 0 <= index < len(self.rows):
            self.move(index, absolute=True)

    # Keys ----------------------------------------------------------------

    def _bindings(self) -> KeyBindings:
        keys = KeyBindings()
        room = Condition(lambda: self.has_room)
        confirm = Condition(lambda: self.phase == "confirm" and self.has_room)

        @keys.add("up", filter=room)
        def _up(event: Any) -> None:
            self.move(-1)

        @keys.add("down", filter=room)
        def _down(event: Any) -> None:
            self.move(1)

        @keys.add("pageup", filter=room)
        def _page_up(event: Any) -> None:
            self.move(-self._page())

        @keys.add("pagedown", filter=room)
        def _page_down(event: Any) -> None:
            self.move(self._page())

        @keys.add("home", filter=room)
        def _home(event: Any) -> None:
            self.move(0, absolute=True)

        @keys.add("end", filter=room)
        def _end(event: Any) -> None:
            self.move(len(self.rows), absolute=True)

        @keys.add("left", filter=confirm)
        @keys.add("right", filter=confirm)
        @keys.add("tab", filter=confirm, eager=True)
        @keys.add("s-tab", filter=confirm, eager=True)
        def _switch(event: Any) -> None:
            self._select()

        @keys.add("enter", filter=room, eager=True)
        @keys.add("c-j", filter=room)
        @keys.add(" ", filter=room)
        def _activate(event: Any) -> None:
            self._enter()

        @keys.add("y", filter=confirm)
        @keys.add("Y", filter=confirm)
        def _yes(event: Any) -> None:
            self.selected = "yes"
            self._start()

        @keys.add("n", filter=confirm)
        @keys.add("N", filter=confirm)
        def _no(event: Any) -> None:
            self.app.exit(result=False)

        @keys.add("escape", eager=True)
        @keys.add("c-c", eager=True)
        @keys.add("<sigint>", eager=True)
        def _cancel(event: Any) -> None:
            self._escape()

        @keys.add("<any>")
        def _ignore(event: Any) -> None:
            pass

        return keys

    async def run_async(self) -> bool:
        """Restore terminal modes on all exits, including unexpected failures."""
        result = bool(await self.app.run_async(set_exception_handler=False))
        if self.crash is not None:
            traceback.print_exception(self.crash, file=sys.stderr)
        return result
