"""Reusable real-Herdr test helpers (used by tools/next_suite.py).

Everything here drives ONE isolated, named Herdr session created by
``verify_herdr.Verification`` (temporary XDG dirs, own socket). It must only run
inside the bubblewrap sandbox of tools/verify_sandbox.py; never point it at a
live Herdr.

Coordinates: ``screen`` cells are 0-based (x, y) on the client screen. Popup
mouse events are written as SGR reports at client coordinates; Herdr translates
them to popup-PTY-relative coordinates. The popup frame is located from the
captured screen by its title, so nothing depends on hard-coded positions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from verify_herdr import Verification

POPUP_TITLES = ("Rename agent", "Sync agent names", "Sync agent name")
TOP_LEFT, TOP_RIGHT = "┌╭", "┐╮"
BOTTOM_LEFT, BOTTOM_RIGHT = "└╰", "┘╯"
PALETTES = {  # panel_bg / accent from docs/r1-source-findings.md (hex without '#')
    "catppuccin": {"panel_bg": "181825", "accent": "89b4fa", "page": "#1e1e2e", "text": "#cdd6f4"},
    "dracula": {"panel_bg": "282a36", "accent": "bd93f9", "page": "#282a36", "text": "#f8f8f2"},
}


def find(obj, key):
    """First value stored under ``key`` anywhere in a JSON structure."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find(value, key)
            if found is not None:
                return found
    return None


class Frame:
    """Outer popup frame on the client screen (0-based)."""

    def __init__(self, x, y, width, height, title):
        self.x, self.y, self.width, self.height, self.title = x, y, width, height, title

    @property
    def inner(self):
        """(x, y, w, h) of the popup PTY: 1 border left, 2 (gutter+border) right."""
        return self.x + 1, self.y + 1, self.width - 3, self.height - 2

    def to_client(self, col, row):
        """Popup-PTY-relative 0-based (col, row) -> client screen cell."""
        return self.inner[0] + col, self.inner[1] + row

    def __repr__(self):
        return f"Frame(x={self.x}, y={self.y}, w={self.width}, h={self.height}, title={self.title!r})"


class Lab(Verification):
    """Verification session plus layout, agent, popup, keyboard and mouse helpers."""

    def __init__(self, destination: Path, *, theme: str | None = None, **kwargs):
        super().__init__(destination, **kwargs)
        self.theme = theme or "catppuccin"
        if theme:
            self.config.write_text(self.config.read_text() + f'\n[theme]\nname = "{theme}"\n')
        palette = PALETTES.get(self.theme, PALETTES["catppuccin"])
        self.palette = palette
        self.default_background, self.default_foreground = palette["page"], palette["text"]
        self.layout: dict[str, str] = {}
        self.client_size = (100, 32)
        self.captured: list[str] = []
        self.png_keep: set[str] = set()
        self.raw = b""
        # Test-only CLI shim, installed inside this disposable session. Herdr overwrites HERDR_BIN_PATH for plugin
        # processes with its own executable, so a sitecustomize.py on PYTHONPATH (server environment only) points the
        # plugin's Python processes at the shim before the plugin code runs. See tools/herdr_shim.py.
        self.shim_dir = self.base / "shim"
        self.shim_dir.mkdir()
        self.shim_exe = self.shim_dir / "herdr"
        shim_py = Path(__file__).resolve().with_name("herdr_shim.py")
        self.shim_exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "{shim_py}" "$@"\n')
        self.shim_exe.chmod(0o700)
        (self.shim_dir / "site").mkdir()
        (self.shim_dir / "site/sitecustomize.py").write_text(
            "import os\nif os.environ.get('HERDR_SHIM_EXE'):\n    os.environ['HERDR_BIN_PATH'] = os.environ['HERDR_SHIM_EXE']\n")
        self.shim_log = self.shim_dir / "calls.jsonl"
        self.shim_delay_file = self.shim_dir / "rename-delay-ms"

    def launch(self):
        """Start the client/server with the shim as the plugin's Herdr binary (harness calls keep using the real one)."""
        plain = self.env
        self.env = dict(plain, HERDR_SHIM_EXE=str(self.shim_exe), HERDR_SHIM_DIR=str(self.shim_dir),
                        HERDR_SHIM_REAL=self.binary, PYTHONPATH=str(self.shim_dir / "site"))
        try:
            super().launch()
        finally:
            self.env = plain

    # ---- shim ----------------------------------------------------------------
    def set_rename_delay(self, ms):
        if ms:
            self.shim_delay_file.write_text(str(int(ms)))
        else:
            self.shim_delay_file.unlink(missing_ok=True)

    # ---- shim fault injection (rule format: see tools/herdr_shim.py) -----------
    def set_faults(self, *rules):
        """Activate fault rules for the plugin's CLI calls (cleared by ``reset``); counters start from zero."""
        (self.shim_dir / "fault-state.json").unlink(missing_ok=True)
        (self.shim_dir / "faults.json").write_text(json.dumps(list(rules)))

    def clear_faults(self):
        for name in ("faults.json", "fault-state.json", "fault-state.json.tmp"):
            (self.shim_dir / name).unlink(missing_ok=True)

    def fault_events(self, since=0, phase="fault"):
        return self.shim_calls(since, phase=phase)

    def clear_handoff_debris(self):
        """Remove handoff files / tamper leftovers that the plugin did not consume (private state dir of the sandbox)."""
        for record in self.shim_calls(0, phase="handoff"):
            directory = Path(record.get("env", {}).get("HERDR_PLUGIN_STATE_DIR", ""))
            if directory.is_dir():
                for entry in directory.iterdir():
                    if entry.name.startswith(("agent-rename-handoff-", "decoy-", "alias-")):
                        entry.unlink(missing_ok=True)

    def shim_calls(self, since=0, phase="start"):
        """Shim log records (dicts with ``argv``) from index ``since``; one per invocation for phase 'start'."""
        try:
            lines = self.shim_log.read_text().splitlines()
        except OSError:
            return []
        records = [json.loads(line) for line in lines if line.strip()]
        return [r for r in records[since:] if phase is None or r["phase"] == phase]

    def shim_mark(self):
        try:
            return len(self.shim_log.read_text().splitlines())
        except OSError:
            return 0

    def pump(self, duration=.3):
        """Like Verification.pump, but also keeps the raw client byte stream (for OSC notifications)."""
        import select
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if not select.select([self.fd], [], [], min(.05, max(0, deadline - time.monotonic())))[0]:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            self.raw = (self.raw + data)[-200000:]
            self.stream.feed(data.decode("utf-8", "replace"))
            if b"\x1b[6n" in data:
                os.write(self.fd, f"\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R".encode())
            if b"\x1b[c" in data:
                os.write(self.fd, b"\x1b[?1;2c")

    def raw_has(self, text):
        return text.encode() in self.raw

    # ---- CLI -------------------------------------------------------------
    def run_cli(self, *argv, timeout=15):
        """Run a herdr CLI command; never raises on non-zero exit. Returns dict with rc/json."""
        result = subprocess.run([self.binary, "--session", self.name, *argv], env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
        out = {"argv": list(argv), "rc": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
        try:
            out["json"] = json.loads(out["stdout"] or out["stderr"])
        except ValueError:
            out["json"] = None
        return out

    def panes(self, workspace=None):
        args = ("pane", "list", *(("--workspace", workspace) if workspace else ()))
        return (find(self.run_cli(*args)["json"], "panes") or [])

    def agents(self):
        return find(self.run_cli("agent", "list")["json"], "agents") or []

    def agent_name(self, key):
        """Explicit agent name for a layout key, or None (unnamed / no agent)."""
        pane = self.layout[key]
        return next((a.get("name") for a in self.agents() if a["pane_id"] == pane), None)

    def has_agent(self, key):
        return any(a["pane_id"] == self.layout[key] for a in self.agents())

    def pane_label(self, key):
        return next((p.get("label") for p in self.panes() if p["pane_id"] == self.layout[key]), None)

    def names(self):
        return {k: self.agent_name(k) for k in self.layout}

    def fingerprint(self):
        """Names + labels + agent revisions; equal fingerprints mean no observable write."""
        agents = {a["pane_id"]: (a.get("name"), a.get("revision"), a.get("state_change_seq")) for a in self.agents()}
        labels = {p["pane_id"]: p.get("label") for p in self.panes()}
        return {"agents": agents, "labels": labels}

    def revisions(self):
        return {k: next((a.get("revision") for a in self.agents() if a["pane_id"] == p), None) for k, p in self.layout.items()}

    # ---- layout and agents -------------------------------------------------
    def start_agent(self, pane, name):
        p = subprocess.Popen([self.binary, "--session", self.name, "agent", "start", name, "--kind", "codex", "--pane", pane,
                              "--timeout", "10000", "--", *self.codex_arguments], env=self.env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        end = time.monotonic() + 18
        while p.poll() is None and time.monotonic() < end:
            self.pump(.2)
            if "trust" in self.text():
                self.keys("\r")
        p.communicate(timeout=4)
        if not self.run_cli("agent", "get", pane)["rc"] == 0:
            raise RuntimeError(f"idle agent did not start in {pane}\n{self.text()}")

    def build_layout(self):
        """Workspace 1 / tab 1: a(agent) b(agent) c(no agent); tab 2: d(agent) e(agent) g(no agent);
        workspace 2: f(agent, control). Only a is focused; every agent is idle (no prompt)."""
        L = self.layout
        L["a"] = self.pane
        ws1 = find(self.run_cli("workspace", "list")["json"], "workspace_id")
        L["b"] = find(self.run_cli("pane", "split", L["a"], "--direction", "right", "--no-focus")["json"], "pane_id")
        L["c"] = find(self.run_cli("pane", "split", L["a"], "--direction", "down", "--no-focus")["json"], "pane_id")
        tab = self.run_cli("tab", "create", "--workspace", ws1, "--label", "second", "--no-focus")
        tab2 = find(tab["json"], "tab_id")
        d = find(tab["json"], "pane_id") or find(tab["json"], "root_pane")
        if not d:
            d = next(p["pane_id"] for p in self.panes(ws1) if p.get("tab_id") == tab2)
        L["d"] = d
        L["e"] = find(self.run_cli("pane", "split", d, "--direction", "down", "--no-focus")["json"], "pane_id")
        L["g"] = find(self.run_cli("pane", "split", d, "--direction", "right", "--no-focus")["json"], "pane_id")
        ws = self.run_cli("workspace", "create", "--label", "second-ws", "--no-focus")
        ws2 = find(ws["json"], "workspace_id")
        L["f"] = next(p["pane_id"] for p in self.panes(ws2))
        self.ws1, self.ws2, self.tab2 = ws1, ws2, tab2
        for key, name in (("a", "alpha"), ("b", "bravo"), ("d", "delta"), ("e", "echo"), ("f", "foxtrot")):
            self.start_agent(L[key], name)
        self.agent_keys = ("a", "b", "d", "e", "f")

    def set_state(self, spec):
        """spec: {key: (label|None, agent_name|None)}; unspecified panes become unlabeled / unnamed.
        All agent names are cleared first so reassignments never collide."""
        for key in self.agent_keys:
            self.run_cli("agent", "rename", self.layout[key], "--clear")
        for key, pane in self.layout.items():
            self.run_cli("pane", "rename", pane, "--clear")
        for key, (label, name) in spec.items():
            if label is not None:
                self.run_cli("pane", "rename", self.layout[key], label)
            if name is not None:
                result = self.run_cli("agent", "rename", self.layout[key], name)
                if result["rc"]:
                    raise RuntimeError(f"fixture rename {key}={name!r} failed: {result['stderr']}")

    def reset(self, spec=None, size=(100, 32)):
        """Return the session to a known state: no popup, client size, focus on a, labels/names from spec."""
        self.clear_faults()
        self.close_popups()
        self.set_rename_delay(0)
        self.clear_handoff_debris()
        if tuple(self.client_size) != tuple(size):
            self.resize(*size)
            self.client_size = tuple(size)
        self.set_state(spec or {})
        self.run_cli("workspace", "focus", self.ws1)
        self.run_cli("agent", "focus", self.layout["a"])
        self.pump(.4)

    # ---- screen ------------------------------------------------------------
    def lines(self):
        return list(self.screen.display)

    def locate(self, text, region=None):
        """First (x, y) of text on the screen, optionally within a Frame."""
        for y, line in enumerate(self.lines()):
            if region and not region.y <= y < region.y + region.height:
                continue
            start = region.x if region else 0
            x = line.find(text, start, region.x + region.width if region else None)
            if x >= 0:
                return x, y
        return None

    def has(self, text):
        return text in self.text()

    def until(self, condition, timeout=8.0, step=.1):
        end = time.monotonic() + timeout
        while True:
            value = condition()
            if value:
                return value
            if time.monotonic() > end:
                return False
            self.pump(step)

    def wait_text(self, text, timeout=6.0, present=True):
        return bool(self.until(lambda: (text in self.text()) == present, timeout, .05))

    def shot(self, name):
        """Always a TXT capture; a PNG only for names listed in ``png_keep`` (key UI states)."""
        self.capture(name)
        if name not in self.png_keep:
            (self.destination / f"{name}.png").unlink(missing_ok=True)
        self.captured.append(name)

    # ---- popups ------------------------------------------------------------
    def frame(self, titles=POPUP_TITLES):
        lines = self.lines()
        for y, line in enumerate(lines):
            for title in titles:
                at = line.find(title)
                if at < 0:
                    continue
                left = max((line.rfind(c, 0, at) for c in TOP_LEFT), default=-1)
                rights = [i for i in (line.find(c, at) for c in TOP_RIGHT) if i >= 0]
                if left < 0 or not rights:
                    continue
                right = min(rights)
                for by in range(y + 1, len(lines)):
                    row = lines[by]
                    if len(row) > right and row[left] in BOTTOM_LEFT and row[right] in BOTTOM_RIGHT:
                        return Frame(left, y, right - left + 1, by - y + 1, title)
        return None

    def popup_open(self):
        return self.frame() is not None

    def open_popup(self, key, timeout=6.0):
        """Ctrl+B then ``key`` (A = rename, S = sync); returns the Frame, or None if no popup appeared."""
        self.keys("\x02", .15)
        os.write(self.fd, key.encode())
        self.pump(.1)
        found = self.until(self.frame, timeout, .05)
        self.pump(.4)  # let the dialog render its content
        return self.frame() if found else None

    def close_popups(self, tries=6):
        for attempt in range(tries):
            if not self.popup_open():
                return
            self.keys("\x1b" if attempt % 2 == 0 else "\x03", .5)
            if self.popup_open() and attempt == 2:
                self.keys("\r", .5)  # e.g. a Close button
        if self.popup_open():
            raise RuntimeError("popup could not be closed\n" + self.text())

    def dismiss(self):
        """Close any popup left open after an action (Close button / Esc)."""
        self.until(lambda: not self.popup_open(), 1.0)
        self.close_popups()

    def manage_at(self, root, operation, *arguments, expected=True):
        """Run ``<root>/bin/<operation>`` against this session; returns (rc, stdout, stderr)."""
        result = subprocess.run([str(Path(root) / "bin" / operation), "--session", self.name, *arguments],
                                env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40)
        if expected and result.returncode:
            raise RuntimeError(f"{operation}: {result.stdout} {result.stderr}")
        return result.returncode, result.stdout, result.stderr

    def set_size(self, cols, rows):
        self.resize(cols, rows)
        self.client_size = (cols, rows)

    # ---- input -------------------------------------------------------------
    def paste(self, text, delay=.4):
        self.keys("\x1b[200~" + text + "\x1b[201~", delay)

    def sgr(self, x, y, button=0, release=False):
        os.write(self.fd, f"\x1b[<{button};{x + 1};{y + 1}{'m' if release else 'M'}".encode())
        self.pump(.06)

    def click(self, x, y, *, double=False, settle=.5):
        """Left press+release at 0-based client cell; ``double`` repeats it quickly."""
        for _ in range(2 if double else 1):
            self.sgr(x, y)
            self.sgr(x, y, release=True)
        self.pump(settle)

    def click_popup(self, col, row, frame, **kwargs):
        self.click(*frame.to_client(col, row), **kwargs)

    def text_cell(self, *candidates, frame=None):
        """Client cell at the middle of the first candidate text found in the popup frame."""
        frame = frame or self.frame()
        for text in candidates:
            at = self.locate(text, frame)
            if at:
                return at[0] + len(text) // 2, at[1]
        return None

    def click_text(self, *candidates, frame=None, **kwargs):
        cell = self.text_cell(*candidates, frame=frame)
        if cell is None:
            raise AssertionError(f"none of {candidates} visible in the popup\n{self.text()}")
        self.click(*cell, **kwargs)
        return cell

    def cell_background(self, x, y):
        char = self.screen.buffer[y][x]
        return char.bg

    def popup_background(self, frame):
        """Most common background color among the popup's interior cells."""
        counts: dict[str, int] = {}
        for y in range(frame.y + 1, frame.y + frame.height - 1):
            for x in range(frame.x + 1, frame.x + frame.width - 1):
                bg = self.screen.buffer[y][x].bg
                counts[bg] = counts.get(bg, 0) + 1
        return max(counts, key=counts.get)

    def finish(self):  # no old-style actual-results.json; the runner writes its own
        try:
            self.cli("session", "stop", self.name, expected=False)
        except Exception:
            pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        if self.pid is not None:
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
        shutil.rmtree(self.base, ignore_errors=True)
