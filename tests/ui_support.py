"""Shared fakes for the PTY-driven dialog tests."""

import asyncio
import io
import re

import pyte
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output


class RecordingOutput(DummyOutput):
    def __init__(self, *, columns=54, rows=5):
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

    def enable_mouse_support(self):
        self.events.append("mouse-on")

    def disable_mouse_support(self):
        self.events.append("mouse-off")


class TermOutput(Vt100_Output):
    """A real VT100 stream that a pyte screen can replay."""

    def __init__(self, columns=54, rows=5):
        self.stream = io.StringIO()
        super().__init__(
            self.stream, get_size=lambda: self.size, term="xterm-256color", enable_cpr=False
        )
        self.size = Size(rows=rows, columns=columns)
        self.columns, self.rows = columns, rows

    def screen(self):
        screen = pyte.Screen(self.columns, self.rows)
        pyte.Stream(screen).feed(self.stream.getvalue())
        return screen


class FakeController:
    """Mirrors Controller's UI-facing surface; trims and clears like the backend."""

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
        name = self.proposed_name if name is None else name.strip()
        if name and not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
            self.state = "error"
            self.message = "Use 1-32 characters: a lowercase letter first, then lowercase letters, digits, underscores, or hyphens."
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


def sgr_press(x, y, button=0):
    """SGR mouse press at 0-based dialog cell (x, y)."""
    return f"\x1b[<{button};{x + 1};{y + 1}M"


def sgr_release(x, y, button=0):
    return f"\x1b[<{button};{x + 1};{y + 1}m"
