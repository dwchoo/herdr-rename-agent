"""Base class that runs a dialog against a pipe input."""

import asyncio
import unittest

from prompt_toolkit.input import create_pipe_input

from herdr_agent_rename.theme import theme_from_config
from ui_support import FakeController, RecordingOutput, eventually


class DialogCase(unittest.IsolatedAsyncioTestCase):
    dialog_class = None
    default_size = (54, 5)

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

    async def start(self, controller=None, output=None, **kwargs):
        controller = controller or FakeController()
        output = output or RecordingOutput(columns=self.default_size[0], rows=self.default_size[1])
        kwargs.setdefault("theme", theme_from_config({}))
        dialog = self.dialog_class(controller, input=self.pipe, output=output, **kwargs)
        task = asyncio.create_task(dialog.run_async())
        self.tasks.append(task)
        await eventually(lambda: dialog.app.is_running and dialog.app.render_counter > 0)
        return dialog, task, controller, output

    async def keys(self, data, predicate):
        self.pipe.send_text(data)
        await eventually(predicate)
