#!/usr/bin/env python3
"""Opt-in actual Herdr verification in a separate named session.

Starts two idle Codex processes without a prompt. Only this tool's temporary
session, configuration and test agents are changed. Requires requirements-test.lock.
Captures are reconstructed from the actual Herdr client's VT100 cell stream.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import tomllib

import pyte
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


class TerminalScreen(pyte.Screen):
    def report_device_status(self, mode, private=False):
        if not private:
            return super().report_device_status(mode)


class Verification:
    def __init__(self, destination: Path, *, codex_arguments=("--no-alt-screen", "--sandbox", "read-only")):
        self.base = Path(tempfile.mkdtemp(prefix="herdr-agent-rename-verify-"))
        self.name = "agent-rename-verification"
        self.destination = destination
        self.codex_arguments = tuple(codex_arguments)
        destination.mkdir(parents=True, exist_ok=True)
        self.binary = shutil.which("herdr")
        self.config = self.base / "config/herdr/config.toml"
        self.config.parent.mkdir(parents=True)
        self.config.write_text('''onboarding = false
[update]
version_check = false
manifest_check = false
[terminal]
default_shell = "/bin/bash"
shell_mode = "non_login"
[[keys.command]]
key = "prefix+shift+a"
type = "popup"
description = "Rename current agent"
command = 'printf "New agent name: "; IFS= read -r name; if [ -n "$name" ]; then herdr agent rename "$HERDR_ACTIVE_PANE_ID" "$name"; fi'
''')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR")}
        self.env.update(XDG_CONFIG_HOME=str(self.base / "config"), XDG_STATE_HOME=str(self.base / "state"),
                        HERDR_CONFIG_PATH=str(self.config), TERM="xterm-256color", COLORTERM="truecolor", SHELL="/bin/bash")
        self.screen = TerminalScreen(100, 32)
        self.stream = pyte.Stream(self.screen)
        self.pid = self.fd = None
        self.results = []
        self.default_background, self.default_foreground = "#1e1e2e", "#cdd6f4"  # PNG fallback colors

    def cli(self, *argv, expected=True):
        if argv == ("plugin", "list"):
            argv = (*argv, "--json")
        result = subprocess.run([self.binary, "--session", self.name, *argv], env=self.env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        if expected and result.returncode:
            raise RuntimeError(f"{argv}: {result.stdout} {result.stderr}")
        if argv == ("plugin", "list") and result.stdout.strip() == "No plugins installed.":
            return {"id": "cli:plugin", "result": {"type": "plugin_list", "plugins": []}}
        return json.loads(result.stdout or result.stderr)

    def record(self, name, passed, details=""):
        self.results.append({"scenario": name, "result": "Passed" if passed else "Failed", "details": details})
        print(f"{self.results[-1]['result']}: {name}", flush=True)
        if not passed:
            raise AssertionError(name + ": " + details)

    def start(self):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(ROOT)
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 100, 0, 0))
            os.execvpe(self.binary, [self.binary, "--session", self.name], self.env)
        self.pump(1)
        self.manage("install")
        commands = tomllib.loads(self.config.read_text())["keys"]["command"]
        self.record("One-command install migrates legacy popup", {item["command"] for item in commands} == {"local.agent-rename.open", "local.agent-rename.sync"})
        installed = self.config.read_bytes()
        records = list((self.base / "state/herdr/plugins/local.agent-rename/installations").glob("*.json"))
        before_record = records[0].read_bytes()
        self.manage("install")
        self.record("Repeated install keeps one binding per action and recovery record", self.config.read_bytes() == installed and records[0].read_bytes() == before_record)
        self.manage("install", "--dry-run")
        self.manage("uninstall", "--dry-run")
        self.record("Install/uninstall previews leave config and registration intact", self.config.read_bytes() == installed and len(self.cli("plugin", "list")["result"]["plugins"]) == 1)
        panes = self.cli("pane", "list")["result"]["panes"]
        self.pane = panes[0]["pane_id"]
        self.cli("pane", "rename", self.pane, "backend-api")
        self.pump(.2)

    def manage(self, operation, *arguments):
        result = subprocess.run([str(ROOT / "bin" / operation), "--session", self.name, *arguments],
                                env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=25)
        if result.returncode:
            raise RuntimeError(f"{operation}: {result.stdout} {result.stderr}")
        return result.stdout

    def pump(self, duration=.3):
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
            self.stream.feed(data.decode("utf-8", "replace"))
            if b"\x1b[6n" in data:
                os.write(self.fd, f"\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R".encode())
            if b"\x1b[c" in data:
                os.write(self.fd, b"\x1b[?1;2c")

    def keys(self, value, delay=.4):
        os.write(self.fd, value.encode())
        self.pump(delay)

    def text(self):
        return "\n".join(self.screen.display)

    def wait(self, text, *, present=True, timeout=8):
        deadline = time.monotonic() + timeout
        while (text in self.text()) != present:
            if time.monotonic() > deadline:
                raise TimeoutError(f"Waiting for {text!r}, present={present}\n{self.text()}")
            self.pump(.1)

    def capture(self, name):
        (self.destination / f"{name}.txt").write_text(self.text() + "\n")
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
        cell_w, cell_h = 9, 18
        img = Image.new("RGB", (self.screen.columns * cell_w, self.screen.lines * cell_h), self.default_background)
        draw = ImageDraw.Draw(img)
        colors = {"default": "#cdd6f4", "black": "#45475a", "red": "#f38ba8", "green": "#a6e3a1", "brown": "#f9e2af", "blue": "#89b4fa", "magenta": "#cba6f7", "cyan": "#94e2d5", "white": "#cdd6f4"}
        def color(value, background=False):
            if value == "default":
                return self.default_background if background else self.default_foreground
            return colors.get(value, "#" + value if len(value) == 6 else "#cdd6f4")
        for row, line in self.screen.buffer.items():
            for col, char in line.items():
                fg, bg = color(char.fg), color(char.bg, True)
                if char.reverse:
                    fg, bg = bg, fg
                x, y = col * cell_w, row * cell_h
                draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), fill=bg)
                draw.text((x, y), char.data, fill=fg, font=font)
        img.save(self.destination / f"{name}.png")

    def agent(self):
        return self.cli("agent", "get", self.pane)["result"]["agent"]

    def popup(self, key, title):
        self.keys("\x02", .15)
        self.keys(key)
        self.pump(.5)
        self.wait(title)
        self.record(title + " shortcut opens popup", True)

    def close(self, title):
        self.keys("\x1b")
        self.wait(title, present=False)

    def resize(self, cols, rows):
        self.screen.resize(lines=rows, columns=cols)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        # Linux sends SIGWINCH to the controlling foreground process group.
        import signal
        os.kill(self.pid, signal.SIGWINCH)
        self.pump(.5)

    def run(self):
        self.start()
        self.record("Manifest link and action discovery", len(self.cli("plugin", "action", "list", "--plugin", "local.agent-rename")["result"]["actions"]) == 2)
        self.popup("A", "Rename agent")
        self.wait("No running agent")
        self.capture("missing-agent")
        self.keys("\r")
        self.record("Missing agent disables Apply", "Rename agent" in self.text())
        self.close("Rename agent")
        start = subprocess.Popen([self.binary, "--session", self.name, "agent", "start", "verifier", "--kind", "codex", "--pane", self.pane,
                                  "--timeout", "10000", "--", *self.codex_arguments], env=self.env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 14
        while start.poll() is None and time.monotonic() < deadline:
            self.pump(.2)
            # A trust prompt concerns only this repository's dedicated test pane.
            if "Do you trust" in self.text() or "trust this directory" in self.text():
                self.keys("\r")
        stdout, stderr = start.communicate(timeout=2)
        detected = self.cli("agent", "get", self.pane, expected=False).get("result", {}).get("agent", {}).get("agent") == "codex"
        if not detected:
            self.capture("codex-startup-failed")
        self.record("Dedicated idle Codex agent detected", detected, stdout + stderr + ("\n" + self.text() if not detected else ""))
        self.cli("agent", "rename", self.pane, "verifier")
        before = self.agent()
        process = self.cli("pane", "process-info", "--pane", self.pane)["result"]["process_info"]
        self.keys("\x02", .15)
        self.keys("P")
        self.wait("rename pane")
        self.capture("native-rename-pane")
        self.close("rename pane")
        self.popup("A", "Rename agent")
        self.wait("verifier")
        self.capture("plugin-rename-agent")
        self.keys("\x05\x15renamed-verifier\r", 1)
        self.wait("Rename agent", present=False)
        self.record("Apply renames only target agent", self.agent().get("name") == "renamed-verifier" and self.cli("pane", "get", self.pane)["result"]["pane"]["label"] == "backend-api")
        self.popup("S", "Sync agent name")
        self.wait("backend-api")
        self.capture("plugin-sync-agent")
        self.record("Sync has no write before Yes", self.agent().get("name") == "renamed-verifier")
        self.keys("\r")
        self.wait("Sync agent name", present=False)
        self.record("Default No sends no rename", self.agent().get("name") == "renamed-verifier")
        self.popup("S", "Sync agent name")
        self.keys("\t\r", 1)
        self.wait("Sync agent name", present=False)
        self.record("Yes syncs explicit label without renaming pane", self.agent().get("name") == "backend-api" and self.cli("pane", "get", self.pane)["result"]["pane"]["label"] == "backend-api")
        self.popup("A", "Rename agent")
        self.keys("\x05\x15\x1b[200~invalid\nname\x1b[201~\r")
        self.wait("Use 1")
        self.capture("invalid-paste")
        self.record("Pasted newline stays invalid without rename", self.agent().get("name") == "backend-api" and "invalid\\nname" in self.text())
        self.keys("\x03")
        self.wait("Rename agent", present=False)
        self.popup("S", "Sync agent name")
        self.cli("pane", "rename", self.pane, "source-changed")
        self.keys("\t\r", .8)
        self.wait("pane name has changed")
        self.capture("changed-source")
        self.record("Changed source confirmation rejected", self.agent().get("name") == "backend-api")
        self.close("Sync agent name")
        self.cli("pane", "rename", self.pane, "backend-api")
        self.popup("S", "Sync agent name")
        self.cli("agent", "rename", self.pane, "externally-changed")
        self.keys("\t\r", .8)
        self.wait("agent name has changed")
        self.capture("changed-agent-name")
        self.record("Changed agent name confirmation rejected", self.agent().get("name") == "externally-changed")
        self.close("Sync agent name")
        self.cli("agent", "rename", self.pane, "backend-api")
        self.cli("pane", "rename", self.pane, "Backend API")
        self.popup("S", "Sync agent name")
        self.wait("not a valid agent name")
        self.keys("\t\r")
        self.capture("invalid-pane-label")
        self.record("Invalid pane label disables Yes", self.agent().get("name") == "backend-api" and "Sync agent name" in self.text())
        self.close("Sync agent name")
        self.cli("pane", "rename", self.pane, "--clear")
        self.popup("S", "Sync agent name")
        self.wait("This pane has no name")
        self.keys("\t\r")
        self.record("Missing pane label disables Yes", self.agent().get("name") == "backend-api" and "Sync agent name" in self.text())
        self.close("Sync agent name")
        self.cli("pane", "rename", self.pane, "source-changed")
        self.popup("A", "Rename agent")
        self.resize(38, 10)
        self.wait("Enlarge the terminal")
        self.capture("small-terminal")
        self.keys("\r")
        self.record("Small terminal disables Apply", self.agent().get("name") == "backend-api")
        self.keys("\x1b")
        self.resize(100, 32)
        self.wait("Rename agent", present=False)
        after_process = self.cli("pane", "process-info", "--pane", self.pane)["result"]["process_info"]
        self.record("Original agent process and focus survive popups", process["foreground_process_group_id"] == after_process["foreground_process_group_id"] and self.agent()["focused"] and before["terminal_id"] == self.agent()["terminal_id"])
        fixture = ROOT / "tests/fixtures/runtime-0.9.3.json"
        fixture.write_text(json.dumps({"provenance": "Actual isolated Herdr 0.9.3 with idle Codex; dynamic ids retained", "agent": self.agent(), "pane": self.cli("pane", "get", self.pane)["result"]["pane"], "process_info": after_process}, indent=2) + "\n")
        other = self.cli("pane", "split", self.pane, "--direction", "right", "--no-focus")["result"]["pane"]["pane_id"]
        self.cli("agent", "start", "reserved-name", "--kind", "codex", "--pane", other, "--timeout", "5000", "--", *self.codex_arguments, expected=False)
        self.record("Second dedicated agent detected", self.cli("agent", "get", other)["result"]["agent"].get("agent") == "codex")
        self.cli("agent", "rename", other, "reserved-name")
        self.popup("A", "Rename agent")
        self.cli("pane", "focus", "--pane", self.pane, "--direction", "right")
        self.keys("\x05\x15focus-safe\r", .8)
        self.wait("Rename agent", present=False)
        self.record("Invocation target survives later focus change", self.agent().get("name") == "focus-safe" and self.cli("agent", "get", other)["result"]["agent"]["name"] == "reserved-name")
        self.cli("pane", "focus", "--pane", other, "--direction", "left")
        self.popup("A", "Rename agent")
        self.keys("\x05\x15reserved-name\r", .8)
        self.wait("already in use")
        self.capture("duplicate-name")
        self.record("Duplicate name retains dialog and input", self.agent().get("name") == "focus-safe" and "reserved-name" in self.text())
        self.keys("\x03")
        self.wait("Rename agent", present=False)
        self.popup("A", "Rename agent")
        self.keys("\x05\x15moved-name")
        moved = self.cli("pane", "move", self.pane, "--new-tab", "--no-focus")["result"]["move_result"]["pane"]["pane_id"]
        self.keys("\r", .8)
        self.wait("target pane has moved")
        self.capture("moved-target")
        self.record("Moved target rejected through old alias", self.cli("agent", "get", moved)["result"]["agent"]["name"] == "focus-safe")
        self.close("Rename agent")
        self.pane = other
        self.popup("A", "Rename agent")
        self.keys("\x05\x15exited-name")
        self.cli("pane", "close", other)
        self.pump(.5)
        if "Rename agent" in self.text():
            self.keys("\r", .8)
            self.wait("target pane no longer exists")
            self.close("Rename agent")
        self.capture("exited-target")
        self.record("Exited target closes safely without redirect", self.cli("agent", "get", moved)["result"]["agent"]["name"] == "focus-safe", "Herdr closes the popup when closing its last-pane tab; no affirmative key is sent after host closure.")
        self.pane = moved
        self.config.write_text(self.config.read_text() + '''
# User change made after installation; preserve this comment.
[ui]
sidebar_width = 31
[[keys.command]]
key = "prefix+alt+y"
type = "shell"
description = "User binding after installation"
command = "true"
''')
        self.manage("uninstall")
        restored = tomllib.loads(self.config.read_text())
        bindings = restored["keys"]["command"]
        self.record("Uninstall preserves later config values comments and user bindings", restored["ui"]["sidebar_width"] == 31 and any(item["command"] == "true" for item in bindings) and "preserve this comment" in self.config.read_text())
        self.record("Uninstall restores only displaced legacy binding", len(bindings) == 2 and any(item["type"] == "popup" and "HERDR_ACTIVE_PANE_ID" in item["command"] for item in bindings) and not any(item["command"].startswith("local.agent-rename.") for item in bindings))
        self.record("Uninstall removes registration and resolved recovery record", not self.cli("plugin", "list")["result"]["plugins"] and not list((self.base / "state/herdr/plugins/local.agent-rename/installations").glob("*.json")))
        uninstalled = self.config.read_bytes()
        self.manage("uninstall")
        self.record("Repeated uninstall is harmless", self.config.read_bytes() == uninstalled)
        self.cli("plugin", "link", str(ROOT))
        original_config = self.config.read_bytes()
        self.cli("plugin", "unlink", "local.agent-rename")
        self.record("Unlink preserves config and checkout", self.config.read_bytes() == original_config and (ROOT / "herdr-plugin.toml").exists())
        self.record("Registry clean after unlink", self.cli("plugin", "list")["result"]["plugins"] == [])

    def finish(self):
        try:
            self.cli("session", "stop", self.name, expected=False)
        except Exception:
            pass
        if self.fd is not None:
            os.close(self.fd)
        if self.pid is not None:
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
        (self.destination.parent / "actual-results.json").write_text(json.dumps({"herdr_version": "0.9.3", "codex_arguments": self.codex_arguments, "terminal": "PTY xterm-256color, truecolor", "cells": "100x32", "theme": "default catppuccin", "captures": "Reconstructed from actual client VT100 cells; no OS compositor screenshot", "scenarios": self.results}, indent=2) + "\n")
        shutil.rmtree(self.base)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Start a temporary Herdr session and two idle Codex processes")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to perform opt-in isolated UI verification.")
    verification = Verification(ROOT / "docs/captures")
    try:
        verification.run()
    finally:
        verification.finish()


if __name__ == "__main__":
    main()
