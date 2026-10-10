#!/usr/bin/env python3
"""Stage R1 live probes against real Herdr 0.9.3 inside a disposable sandbox.

Usage (from the checkout):  .venv/bin/python tools/probe_r1.py --run [--output DIR]

Reuses the bubblewrap approach of tools/verify_sandbox.py (fresh HOME, separate
PID/network/mount namespaces, copied checkout, isolated named Herdr session, idle
Codex test agents with no model prompt). Nothing touches the host Herdr.

Probes (results are written to DIR/r1-data.json plus DIR/screens/*.txt):
  q1  pane/agent enumeration JSON (several tabs, splits, labels, agents, workspaces,
      plugin popups open)
  q2  agent-name uniqueness scope and `agent rename --clear`
  q3  mouse and keyboard delivery to a plugin popup (throwaway plugin whose popup
      is tools/mouseprobe.py)
  q4  popup PTY size for width/height as percentages and as cell counts
  q5  this repo's sync popup: which Yes gestures work
  q6  shortcut-to-first-frame latency of the sync popup

--stages q1,q2,... limits the run. Default DIR is docs/r1-live.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SANDBOX_ROOT = Path("/workspace/agent-rename")
OUT_IN_SANDBOX = "docs/r1-live"

MOUSE_PLUGIN = '''id = "local.mouse-probe"
name = "Mouse Probe"
version = "0.0.1"
min_herdr_version = "0.9.3"
platforms = ["linux"]
description = "Throwaway sandbox-only popup probe."

[[actions]]
id = "noop"
title = "noop"
description = "noop"
contexts = ["pane"]
command = ["/usr/bin/true"]

{panes}'''
PANE = '''[[panes]]
id = "{id}"
title = "{id}"
placement = "popup"
width = {width}
height = {height}
command = ["/usr/bin/python3", "{script}"]

'''


def find(obj, key):
    """First value stored under `key` anywhere in a JSON structure."""
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


def inside(stages: set[str]) -> None:
    from verify_herdr import Verification

    out = ROOT / OUT_IN_SANDBOX
    (out / "screens").mkdir(parents=True, exist_ok=True)
    codex_home = Path(os.environ["CODEX_HOME"])
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('''model = "gpt-6.1-sol"
model_provider = "offline-test"
disable_update_check = true
[model_providers.offline-test]
name = "Offline E2E test"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
''')
    v = Verification(out, codex_arguments=("--no-daemon", "--no-alt-screen", "--sandbox", "read-only"))
    data: dict = {"herdr": subprocess.check_output(["herdr", "--version"], text=True).strip(), "client_size": "100x32"}

    def raw(*argv, timeout=10):
        r = subprocess.run([v.binary, "--session", v.name, *argv], env=v.env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        return {"argv": ["herdr", *argv], "rc": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}

    def js(*argv):
        r = raw(*argv)
        try:
            r["json"] = json.loads(r["stdout"] or r["stderr"])
        except ValueError:
            pass
        return r

    def shot(name):
        (out / "screens" / f"{name}.txt").write_text(v.text() + "\n")

    def start_agent(pane, name):
        p = subprocess.Popen([v.binary, "--session", v.name, "agent", "start", name, "--kind", "codex", "--pane", pane,
                              "--timeout", "10000", "--", *v.codex_arguments], env=v.env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        end = time.monotonic() + 15
        while p.poll() is None and time.monotonic() < end:
            v.pump(.2)
            if "trust" in v.text():
                v.keys("\r")
        so, se = p.communicate(timeout=3)
        return {"stdout": so.strip(), "stderr": se.strip()}

    def agent_name(pane):
        return find(v.cli("agent", "get", pane, expected=False), "name")

    def set_state(pane, label, name):
        v.cli("pane", "rename", pane, label)
        v.cli("agent", "rename", pane, name)

    def wait_text(text, timeout=6):
        end = time.monotonic() + timeout
        while text not in v.text() and time.monotonic() < end:
            v.pump(.05)
        return text in v.text()

    def locate(text):
        for y, line in enumerate(v.screen.display):
            x = line.find(text)
            if x >= 0:
                return x, y
        return None

    def sgr_click(x, y):
        """x, y are 0-based screen cells; SGR is 1-based."""
        os.write(v.fd, f"\x1b[<0;{x + 1};{y + 1}M".encode())
        v.pump(.05)
        os.write(v.fd, f"\x1b[<0;{x + 1};{y + 1}m".encode())
        v.pump(.4)

    def close_popup_esc():
        v.keys("\x1b", .4)

    # ---- environment -------------------------------------------------------
    v.start()  # fork client, install plugin into the isolated session
    p1 = v.pane
    ws_list = js("workspace", "list")
    ws1 = find(ws_list["json"], "workspace_id")
    data["setup"] = {"workspace_list_initial": ws_list}
    data["setup"]["agent_p1"] = start_agent(p1, "alpha")
    v.cli("agent", "rename", p1, "alpha")
    v.cli("pane", "rename", p1, "tester")

    layout = {}
    r = js("pane", "split", p1, "--direction", "right", "--no-focus")
    p2 = find(r["json"], "pane_id")  # unlabeled, no agent
    r = js("pane", "split", p1, "--direction", "down", "--no-focus")
    p3 = find(r["json"], "pane_id")  # labeled, no agent
    v.cli("pane", "rename", p3, "docs-shell")
    r = js("tab", "create", "--workspace", ws1, "--label", "second", "--no-focus")
    layout["tab_create"] = r
    tab2 = find(r["json"], "tab_id")
    p4 = find(r["json"], "pane_id") or find(r["json"], "root_pane")
    if not p4:
        for item in js("pane", "list", "--workspace", ws1)["json"]["result"]["panes"]:
            if item.get("tab_id") == tab2:
                p4 = item["pane_id"]
    data["setup"]["agent_p4"] = start_agent(p4, "beta")
    v.cli("agent", "rename", p4, "beta")  # tab 2 agent, no pane label
    r = js("pane", "split", p4, "--direction", "down", "--no-focus")
    p5 = find(r["json"], "pane_id")
    v.cli("pane", "rename", p5, "tab2-extra")  # labeled, no agent
    r = js("workspace", "create", "--label", "second-ws", "--no-focus")
    layout["workspace_create"] = r
    ws2 = find(r["json"], "workspace_id")
    p6 = None
    for item in js("pane", "list", "--workspace", ws2)["json"]["result"]["panes"]:
        p6 = item["pane_id"]
    data["setup"]["agent_p6"] = start_agent(p6, "gamma")
    v.cli("agent", "rename", p6, "gamma")
    v.cli("pane", "rename", p6, "gamma-label")
    layout.update(ws1=ws1, ws2=ws2, tab2=tab2, panes=dict(p1=p1, p2=p2, p3=p3, p4=p4, p5=p5, p6=p6))
    data["layout"] = layout
    # Focus the first workspace's first tab and pane for shortcuts.
    for argv in (("workspace", "focus", ws1), ("pane", "focus", "--pane", p1)):
        js(*argv)
    v.pump(.5)
    shot("main-layout")

    # Throwaway plugin (sandbox only) for q3/q4.
    plugin_dir = v.base / "mouseprobe"
    plugin_dir.mkdir()
    log = v.base / "mouse.log"
    script = SANDBOX_ROOT / "tools/mouseprobe.py"
    sizes = {"m80": ('"80%"', '"70%"'), "m56": (56, 7)}
    panes = "".join(PANE.format(id=k, width=w, height=h, script=script) for k, (w, h) in sizes.items())
    (plugin_dir / "herdr-plugin.toml").write_text(MOUSE_PLUGIN.format(panes=panes))
    data["mouse_plugin_link"] = js("plugin", "link", str(plugin_dir))

    def open_probe(entry, *extra):
        log.write_text("")
        r = js("plugin", "pane", "open", "--plugin", "local.mouse-probe", "--entrypoint", entry, "--focus",
               "--env", f"MOUSEPROBE_LOG={log}", *extra)
        ok = wait_text("@@ORIGIN@@")
        v.pump(.3)
        return r, ok

    def close_probe():
        os.write(v.fd, b"QQQ")
        v.pump(.6)
        if "@@ORIGIN@@" in v.text():
            close_popup_esc()

    def stage(name, fn):
        if name in stages:
            try:
                data[name] = fn()
            except Exception as error:  # keep other stages running
                data[name] = {"error": repr(error), "screen": v.text()}
                shot(name + "-error")
            print("stage", name, "done", flush=True)

    # ---- q1 ----------------------------------------------------------------
    def q1():
        res = {}
        res["workspace_list"] = js("workspace", "list")
        res["tab_list"] = js("tab", "list")
        res["pane_list_ws1"] = js("pane", "list", "--workspace", ws1)
        res["pane_list_ws2"] = js("pane", "list", "--workspace", ws2)
        res["pane_list_all"] = js("pane", "list")
        res["agent_list"] = js("agent", "list")
        for key, pane in layout["panes"].items():
            res[f"pane_get_{key}"] = js("pane", "get", pane)
            res[f"agent_get_{key}"] = js("agent", "get", pane)
        # Popup panes present: the mouse probe popup and this plugin's sync popup.
        r, ok = open_probe("m80")
        res["open_probe_popup"] = {"opened": ok, "result": r}
        res["pane_list_ws1_with_popup"] = js("pane", "list", "--workspace", ws1)
        res["pane_list_all_with_popup"] = js("pane", "list")
        res["agent_list_with_popup"] = js("agent", "list")
        close_probe()
        v.keys("\x02", .15)
        v.keys("S")
        res["sync_popup_opened"] = wait_text("Sync agent name")
        v.pump(.5)
        res["pane_list_ws1_with_sync_popup"] = js("pane", "list", "--workspace", ws1)
        res["pane_list_all_with_sync_popup"] = js("pane", "list")
        shot("q1-sync-popup")
        close_popup_esc()
        res["plugin_pane_cli_help"] = raw("plugin", "pane", "open", "--help")
        return res

    # ---- q2 ----------------------------------------------------------------
    def q2():
        res = {}
        res["before"] = {"alpha_agent": js("agent", "get", p1), "gamma_agent": js("agent", "get", p6)}
        res["rename_other_workspace_to_alpha"] = js("agent", "rename", p6, "alpha")
        res["gamma_name_after_failed_rename"] = agent_name(p6)
        res["rename_other_tab_same_workspace_to_alpha"] = js("agent", "rename", p4, "alpha")
        res["rename_self_to_same_name"] = js("agent", "rename", p1, "alpha")
        res["rename_case_variant"] = js("agent", "rename", p6, "ALPHA")
        res["rename_uppercase_valid"] = js("agent", "rename", p6, "Gamma2")
        res["gamma_name_after_uppercase_try"] = agent_name(p6)
        res["rename_by_pane_label_target"] = js("agent", "rename", "tester", "alpha")
        res["clear_gamma"] = js("agent", "rename", p6, "--clear")
        res["gamma_after_clear"] = js("agent", "get", p6)
        res["clear_again_unnamed"] = js("agent", "rename", p6, "--clear")
        res["agent_list_after_clear"] = js("agent", "list")
        res["rename_to_freed_name_after_clear"] = js("agent", "rename", p6, "alpha")
        res["clear_alpha_holder"] = js("agent", "rename", p1, "--clear")
        res["rename_gamma_to_alpha_after_holder_cleared"] = js("agent", "rename", p6, "alpha")
        # restore
        v.cli("agent", "rename", p6, "gamma", expected=False)
        v.cli("agent", "rename", p1, "alpha", expected=False)
        res["restored"] = {"p1": agent_name(p1), "p6": agent_name(p6)}
        return res

    # ---- q3 / q4 -----------------------------------------------------------
    def box_of(origin):
        """Outer popup frame (x, y, width, height) around the probe's origin cell."""
        ox, oy = origin
        lines = v.screen.display
        top = next((y for y in range(oy, -1, -1) if lines[y][ox - 1:ox] in ("┌", "╭")), None)
        bottom = next((y for y in range(oy, len(lines)) if lines[y][ox - 1:ox] in ("└", "╰")), None)
        if top is None or bottom is None:
            return None
        right = next((x for x in range(ox, len(lines[top])) if lines[top][x] in ("┐", "╮")), None)
        return {"x": ox - 1, "y": top, "width": right - (ox - 1) + 1, "height": bottom - top + 1}

    def probe_popup(entry, label):
        res = {}
        r, ok = open_probe(entry)
        res["open"] = {"ok": ok, "result": r}
        shot(f"q3-{label}-open")
        origin = locate("@@ORIGIN@@")
        res["origin_cell"] = origin
        sz = [line for line in log.read_text().splitlines() if "START" in line]
        res["start_log"] = sz
        res["outer_box"] = box_of(origin) if origin else None
        res["origin_offset_in_box"] = [origin[0] - res["outer_box"]["x"], origin[1] - res["outer_box"]["y"]] if res["outer_box"] else None
        res["screen_size_line"] = [line.strip() for line in v.screen.display if "SIZE" in line]
        return res, origin

    def q3():
        res = {}
        pr, origin = probe_popup("m80", "m80")
        res.update(pr)
        if origin:
            ox, oy = origin
            before = len(log.read_text().splitlines())
            v.keys("a", .4)
            v.keys("\x1b[A", .4)
            v.keys("\t", .4)
            res["keyboard_log"] = log.read_text().splitlines()[before:]
            before = len(log.read_text().splitlines())
            click = (ox + 7, oy + 3)  # 7 cols right, 3 rows below the popup's (1,1)
            res["click_screen_cell_0based"] = click
            res["expected_popup_relative_1based"] = (7 + 1, 3 + 1)
            sgr_click(*click)
            res["mouse_click_log"] = log.read_text().splitlines()[before:]
            before = len(log.read_text().splitlines())
            os.write(v.fd, f"\x1b[<64;{click[0] + 1};{click[1] + 1}M".encode())  # wheel up
            v.pump(.4)
            res["wheel_log"] = log.read_text().splitlines()[before:]
            before = len(log.read_text().splitlines())
            outside = (0, 0)
            sgr_click(*outside)
            res["click_outside_popup_log"] = log.read_text().splitlines()[before:]
            res["popup_still_open_after_outside_click"] = "@@ORIGIN@@" in v.text()
            shot("q3-after-clicks")
        res["full_log"] = log.read_text().splitlines()
        close_probe()
        # Same click, but the popup never enables mouse reporting itself.
        r, ok = open_probe("m80", "--env", "MOUSEPROBE_NOENABLE=1")
        origin = locate("@@ORIGIN@@")
        before = len(log.read_text().splitlines())
        if origin:
            sgr_click(origin[0] + 7, origin[1] + 3)
            os.write(v.fd, f"\x1b[<64;{origin[0] + 8};{origin[1] + 4}M".encode())
            v.pump(.4)
        res["no_enable_click_log"] = log.read_text().splitlines()[before:]
        res["no_enable_full_log"] = log.read_text().splitlines()
        close_probe()
        return res

    def q4():
        res = {}
        for cols, rows in ((100, 32), (160, 50)):
            if (cols, rows) != (100, 32):
                v.resize(cols, rows)
            for entry in ("m80", "m56"):
                pr, origin = probe_popup(entry, f"{entry}-{cols}x{rows}")
                pr["client_size"] = f"{rows}x{cols}"
                res[f"{entry}@{rows}x{cols}"] = pr
                close_probe()
        v.resize(100, 32)
        return res

    # ---- q5 / q6 -----------------------------------------------------------
    def open_sync(timing=None):
        v.keys("\x02", .15)
        t0 = time.monotonic()
        os.write(v.fd, b"S")
        seen = {}
        end = t0 + 6
        while time.monotonic() < end and "content" not in seen:
            v.pump(.01)
            text = v.text()
            if "Sync agent name" in text and "title" not in seen:
                seen["title"] = time.monotonic() - t0
            if "Yes" in text and "No" in text and "content" not in seen:
                seen["content"] = time.monotonic() - t0
        v.pump(.3)
        return seen

    def q5():
        res = {"cases": {}}
        gestures = [
            ("a_tab_enter", lambda: v.keys("\t\r", 1)),
            ("b_right_enter", lambda: v.keys("\x1b[C\r", 1)),
            ("b2_right_ss3_enter", lambda: v.keys("\x1bOC\r", 1)),
            ("c_left_enter", lambda: v.keys("\x1b[D\r", 1)),
            ("d_y_key", lambda: v.keys("y", 1)),
            ("d2_Y_key", lambda: v.keys("Y", 1)),
            ("e_mouse_click_yes", None),
            ("f_enter_default", lambda: v.keys("\r", 1)),
            ("g_tab_space", lambda: v.keys("\t ", 1)),
            ("h_shift_tab_enter", lambda: v.keys("\x1b[Z\r", 1)),
            ("i_tab_tab_enter", lambda: v.keys("\t\t\r", 1)),
        ]
        for name, gesture in gestures:
            set_state(p1, "tester", "other")
            case = {"agent_before": agent_name(p1)}
            case["open_timing"] = open_sync()
            case["opened"] = "Sync agent name" in v.text()
            case["screen_before"] = v.text()
            if name == "e_mouse_click_yes":
                loc = locate("Yes")
                case["yes_cell"] = loc
                if loc:
                    sgr_click(loc[0] + 1, loc[1])
                    v.pump(.8)
            else:
                gesture()
            case["agent_after"] = agent_name(p1)
            case["renamed"] = case["agent_after"] == "tester"
            case["popup_closed"] = "Sync agent name" not in v.text()
            case["screen_after"] = v.text()
            if not case["popup_closed"]:
                close_popup_esc()
            res["cases"][name] = case
            print("q5", name, "renamed" if case["renamed"] else "NOT renamed", flush=True)
        # Precondition probe: is Yes ever disabled? Capture its screen at default state.
        set_state(p1, "tester", "other")
        open_sync()
        shot("q5-sync-open")
        res["default_screen"] = v.text()
        v.keys("\t", .4)
        shot("q5-after-tab")
        res["after_tab_screen"] = v.text()
        close_popup_esc()
        return res

    def q6():
        res = {"samples": []}
        for _ in range(5):
            set_state(p1, "tester", "other")
            res["samples"].append(open_sync())
            close_popup_esc()
            v.pump(.5)
        return res

    for name, fn in (("q1", q1), ("q2", q2), ("q3", q3), ("q4", q4), ("q5", q5), ("q6", q6)):
        stage(name, fn)
    (out / "r1-data.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
    try:
        v.cli("session", "stop", v.name, expected=False)
    except Exception:
        pass
    os.close(v.fd)
    shutil.rmtree(v.base, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--stages", default="q1,q2,q3,q4,q5,q6")
    parser.add_argument("--output", type=Path, default=ROOT / OUT_IN_SANDBOX)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stages = set(args.stages.split(","))
    if args.inside:
        inside(stages)
        return 0
    if not args.run:
        parser.error("Pass --run to execute the sandboxed probes.")
    from verify_sandbox import native_codex
    bwrap, herdr = shutil.which("bwrap"), shutil.which("herdr")
    if sys.platform != "linux" or not bwrap or not herdr:
        parser.error("Requires Linux, bubblewrap and Herdr.")
    codex = native_codex(None)
    python = ROOT / ".venv/bin/python"
    runtime = python.resolve().parent.parent
    runtime_alias = Path(os.path.abspath(python.parent / os.readlink(python))).parent.parent if python.is_symlink() else runtime
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="herdr-r1-sandbox-") as temporary:
        checkout = Path(temporary) / "checkout"
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "graphify-out", ".graphify-cache", "dist", "captures",
            "sandbox", "r1-live", "actual-results.json", "isolation.json"))
        system_config = Path(temporary) / "system-config"
        system_config.mkdir()
        (system_config / "passwd").write_text(f"tester:x:{os.getuid()}:{os.getgid()}:Sandbox tester:/sandbox/home:/bin/bash\n")
        (system_config / "group").write_text(f"tester:x:{os.getgid()}:\n")
        (system_config / "nsswitch.conf").write_text("passwd: files\ngroup: files\nhosts: files\n")
        (system_config / "hosts").write_text("127.0.0.1 localhost\n::1 localhost\n")
        command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--hostname", "herdr-rename-sandbox",
                   "--cap-drop", "ALL", "--clearenv"]
        for directory in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(directory).exists():
                command += ["--ro-bind", directory, directory]
        command += ["--dir", "/etc"]
        for name in ("passwd", "group", "nsswitch.conf", "hosts"):
            command += ["--ro-bind", str(system_config / name), f"/etc/{name}"]
        for path in ("/etc/ld.so.cache", "/etc/fonts", "/etc/localtime"):
            if Path(path).exists():
                command += ["--ro-bind", path, path]
        command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run", "--tmpfs", "/sandbox/home",
                    "--ro-bind", str(runtime), str(runtime), "--bind", str(checkout), str(SANDBOX_ROOT),
                    "--ro-bind", str(ROOT / ".venv"), str(SANDBOX_ROOT / ".venv"),
                    "--ro-bind", str(Path(herdr).resolve()), "/opt/bin/herdr", "--ro-bind", str(codex), "/opt/bin/codex"]
        if runtime_alias != runtime:
            command += ["--ro-bind", str(runtime), str(runtime_alias)]
        environment = {
            "HOME": "/sandbox/home", "PATH": "/opt/bin:/usr/bin:/bin", "SHELL": "/bin/bash", "TERM": "xterm-256color",
            "COLORTERM": "truecolor", "LANG": "C.UTF-8", "USER": "tester", "LOGNAME": "tester",
            "PYTHONDONTWRITEBYTECODE": "1", "XDG_CONFIG_HOME": "/sandbox/home/config",
            "XDG_STATE_HOME": "/sandbox/home/state", "XDG_CACHE_HOME": "/sandbox/home/cache",
            "CODEX_HOME": "/sandbox/home/.codex",
        }
        for key, value in environment.items():
            command += ["--setenv", key, value]
        command += ["--chdir", str(SANDBOX_ROOT), str(SANDBOX_ROOT / ".venv/bin/python"),
                    str(SANDBOX_ROOT / "tools/probe_r1.py"), "--inside", "--stages", args.stages]
        try:
            code = subprocess.run(command, stdin=subprocess.DEVNULL, timeout=600).returncode
        except subprocess.TimeoutExpired:
            print("Probe timed out; namespace terminated.", file=sys.stderr)
            code = 124
        source = checkout / OUT_IN_SANDBOX
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        print(f"exit {code}; evidence in {destination}", flush=True)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
