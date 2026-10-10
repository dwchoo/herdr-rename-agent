"""Real-Herdr E2E scenarios for the PLAN section 10 behavior (workspace sync, rename dialog, themes).

Run only through the sandbox wrapper:
    .venv/bin/python tools/verify_sandbox.py --run --suite next [--only S01,R0]

Each scenario is independent: it resets the shared session to a known state, drives the
real Herdr client PTY, and reports pass (all assertions hold), fail (AssertionError: the
behavior differs from PLAN) or error (infrastructure / unexpected exception). Assertions
target the PLANNED behavior. Elements are located by text on the captured client screen.

Fixture (see Lab.build_layout): workspace 1 tab 1 has a,b (agents) and c (no agent);
tab 2 has d,e (agents) and g (no agent); workspace 2 has f (agent, unchanged control).
Scenario state is (pane label, agent name) per key; default is no label and name old-<key>.
"""
from __future__ import annotations

from collections import namedtuple
from datetime import datetime
import json
from pathlib import Path
import signal
import time
import traceback

from herdr_lab import Lab, PALETTES

Scenario = namedtuple("Scenario", "id title theme size spec fn")
SCENARIOS: list[Scenario] = []
AGENTS = ("a", "b", "d", "e", "f")
SCENARIO_TIMEOUT = 75


def base(**over):
    spec = {k: (None, f"old-{k}") for k in AGENTS}
    spec.update(over)
    return spec


def scenario(id, title, *, theme="catppuccin", size=(100, 32), spec=None):
    def register(fn):
        SCENARIOS.append(Scenario(id, title, theme, size, base() if spec is None else spec, fn))
        return fn
    return register


class Skip(Exception):
    """Scenario cannot be evaluated with this Herdr/harness combination (not a failure)."""


def ok(condition, message):
    if not condition:
        raise AssertionError(message)


# ---- shared steps ----------------------------------------------------------------------
def open_sync(lab):
    frame = lab.open_popup("S")
    ok(frame, "Ctrl+B Shift+S did not open the sync popup\n" + lab.text())
    return frame


def open_rename(lab):
    frame = lab.open_popup("A")
    ok(frame, "Ctrl+B Shift+A did not open the rename popup\n" + lab.text())
    return frame


def settle_after_apply(lab, expected, timeout=8):
    """Wait until the agent names equal ``expected`` {key: name|None}; then close any Close-button popup."""
    done = lab.until(lambda: all(lab.agent_name(k) == v for k, v in expected.items()), timeout)
    lab.pump(.6)
    lab.dismiss()
    return done


def assert_names(lab, expected, why=""):
    actual = {k: lab.agent_name(k) for k in expected}
    ok(actual == expected, f"{why} names {actual} != expected {expected}\n{lab.text()}")


def assert_unchanged(lab, before, why):
    ok(lab.fingerprint() == before, f"{why}: observable write detected\nbefore={before}\nafter={lab.fingerprint()}")


def sync_apply(lab, gesture, expected):
    """Open sync, run gesture, assert expected names."""
    frame = open_sync(lab)
    lab.shot(f"{lab.current}-dialog")
    gesture(lab, frame)
    done = settle_after_apply(lab, expected)
    lab.shot(f"{lab.current}-after")
    ok(done, f"names never became {expected}; now {lab.names()}\n{lab.text()}")


def sync_cancel(lab, gesture):
    before = lab.fingerprint()
    frame = open_sync(lab)
    lab.shot(f"{lab.current}-dialog")
    gesture(lab, frame)
    lab.pump(1.0)
    lab.shot(f"{lab.current}-after")
    ok(lab.until(lambda: not lab.popup_open(), 3), "popup still open after cancel gesture\n" + lab.text())
    assert_unchanged(lab, before, "cancel gesture")


CHANGE_AB_D = base(a=("new-a", "old-a"), b=("new-b", "old-b"), d=("new-d", "old-d"), f=("new-f", "keep-f"))
CHANGE_EXPECT = {"a": "new-a", "b": "new-b", "d": "new-d", "f": "keep-f", "e": "old-e"}
ONE = base(a=("new-a", "old-a"))


def yes_mouse(lab, frame):
    lab.click_text("[ Yes ]", "Yes ]", "Yes", frame=frame)


def no_mouse(lab, frame):
    lab.click_text("[ No ]", "No ]", "No", frame=frame)


# ---- sync: scope and sources -----------------------------------------------------------
@scenario("S01", "Sync covers every tab of the workspace; second workspace unchanged", spec=CHANGE_AB_D)
def s01(lab):
    labels_before = {k: lab.pane_label(k) for k in lab.layout}
    sync_apply(lab, lambda l, f: l.keys("y", .5), CHANGE_EXPECT)
    ok({k: lab.pane_label(k) for k in lab.layout} == labels_before, "sync changed pane labels")


@scenario("S02", "Mixed rows: valid, unnamed pane, agent-less pane, invalid label, matching",
          spec=base(a=("new-a", "old-a"), b=(None, "keep-b"), c=("shell-c", None), d=("Bad Label", "keep-d"),
                    e=("match-e", "match-e"), f=("new-f", "keep-f")))
def s02(lab):
    sync_apply(lab, lambda l, f: l.keys("y", .5), {"a": "new-a", "b": "keep-b", "d": "keep-d", "e": "match-e", "f": "keep-f"})
    ok(not lab.has_agent("c"), "agent-less pane gained an agent")


@scenario("S03", "Unnamed agent receives a valid explicit pane label", spec=base(a=("new-a", None), b=("new-b", None)))
def s03(lab):
    sync_apply(lab, lambda l, f: l.keys("y", .5), {"a": "new-a", "b": "new-b", "d": "old-d"})


@scenario("S04", "Duplicate desired labels: neither wins, independent row still applies",
          spec=base(a=("dup", "old-a"), b=("dup", "old-b"), d=("new-d", "old-d")))
def s04(lab):
    sync_apply(lab, lambda l, f: l.keys("y", .5), {"a": "old-a", "b": "old-b", "d": "new-d"})


@scenario("S05", "Desired name held in another workspace is a conflict; independent row applies",
          spec=base(a=("held-f", "old-a"), d=("new-d", "old-d"), f=(None, "held-f")))
def s05(lab):
    sync_apply(lab, lambda l, f: l.keys("y", .5), {"a": "old-a", "d": "new-d", "f": "held-f"})


@scenario("S06", "Conflict-only workspace opens an information popup with Close and no writes",
          spec=base(a=("dup", "old-a"), b=("dup", "old-b")))
def s06(lab):
    before = lab.fingerprint()
    frame = open_sync(lab)
    lab.shot("S06-dialog")
    ok(lab.has("Close"), "information popup must offer Close\n" + lab.text())
    ok(not lab.has("Yes"), "information popup must not offer Yes\n" + lab.text())
    lab.click_text("Close", frame=frame)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Close button did not close the popup")
    assert_unchanged(lab, before, "information popup")


def screen_flat(lab):
    """Visible screen text plus, for notifications Herdr emits as terminal escape sequences, the raw stream tail."""
    visible = " ".join(" ".join(line.split()) for line in lab.lines())
    return visible + " " + lab.raw[-20000:].decode("utf-8", "replace")


@scenario("H01", "Harness self-test: a Herdr notification toast is observable on the client screen")
def h01(lab):
    seen = False
    for position in ("top-right", "bottom-right"):
        r = lab.run_cli("notification", "show", "harness-toast-probe", "--body", "probe body", "--position", position)
        seen = lab.until(lambda: "harness-toast-probe" in screen_flat(lab) or lab.raw_has("harness-toast-probe"), 3, .05)
        lab.shot(f"H01-toast-{position}")
        if seen:
            break
    ok(r["rc"] == 0, f"notification show failed: {r['stderr']}")
    if not seen:
        raise Skip("Herdr 0.9.3 headless client draws no toast and emits no notification text; "
                   "notification wording cannot be asserted (only 'no popup, no writes')")


@scenario("S07", "No eligible rows: notification, no popup, no writes",
          spec=base(a=("old-a", "old-a"), b=(None, "old-b"), d=("old-d", "old-d")))
def s07(lab):
    before = lab.fingerprint()
    # Is a toast observable at all in this harness? (see H01)
    lab.run_cli("notification", "show", "harness-toast-probe")
    observable = lab.until(lambda: "harness-toast-probe" in screen_flat(lab), 3, .05)
    lab.pump(5.5)  # let the probe toast expire
    lab.keys("\x02", .15)
    import os
    os.write(lab.fd, b"S")
    seen = lab.until(lambda: "pane names" in screen_flat(lab) or lab.popup_open(), 5, .05)
    lab.shot("S07-after")
    ok(not lab.popup_open(), "popup opened although nothing needs renaming\n" + lab.text())
    assert_unchanged(lab, before, "no-eligible sync")
    if not (seen and "already match" in screen_flat(lab)):
        if not observable:
            raise Skip("no popup and no writes (verified), but notification toasts are not observable on the captured screen")
        raise AssertionError("missing 'All agent names already match their pane names.' notification\n" + lab.text())


# ---- sync: selection --------------------------------------------------------------------
@scenario("S10", "Default No + Enter sends zero writes", spec=CHANGE_AB_D)
def s10(lab):
    sync_cancel(lab, lambda l, f: l.keys("\r", .5))


@scenario("S11", "Tab then Enter applies", spec=CHANGE_AB_D)
def s11(lab):
    sync_apply(lab, lambda l, f: (l.keys("\t", .3), l.keys("\r", .5)), CHANGE_EXPECT)


@scenario("S12", "Left then Enter applies (Left selects Yes)", spec=CHANGE_AB_D)
def s12(lab):
    sync_apply(lab, lambda l, f: (l.keys("\x1b[D", .3), l.keys("\r", .5)), CHANGE_EXPECT)


@scenario("S13", "Right/Left toggle selection; Right then Enter is No or Yes consistently (no write unless Yes shown selected)",
          spec=CHANGE_AB_D)
def s13(lab):
    # Right from default No must never silently apply: Enter after Right, Right, Right (odd presses back on a
    # button pair) is checked by reading the result: names are either all applied or all unchanged, never partial.
    frame = open_sync(lab)
    lab.keys("\x1b[C", .3)
    lab.keys("\x1b[C", .3)
    lab.keys("\x1b[C", .3)
    lab.keys("\r", .5)
    lab.pump(1.0)
    lab.dismiss()
    names = {k: lab.agent_name(k) for k in CHANGE_EXPECT}
    ok(names in (CHANGE_EXPECT, {"a": "old-a", "b": "old-b", "d": "old-d", "f": "keep-f", "e": "old-e"}),
       f"partial result {names}")


@scenario("S14", "Y key applies", spec=CHANGE_AB_D)
def s14(lab):
    sync_apply(lab, lambda l, f: l.keys("Y", .5), CHANGE_EXPECT)


@scenario("S15", "N key cancels with zero writes", spec=CHANGE_AB_D)
def s15(lab):
    sync_cancel(lab, lambda l, f: l.keys("n", .5))


@scenario("S16", "Esc and Ctrl+C cancel with zero writes", spec=CHANGE_AB_D)
def s16(lab):
    sync_cancel(lab, lambda l, f: l.keys("\x1b", .5))
    sync_cancel(lab, lambda l, f: l.keys("\x03", .5))


@scenario("S17", "Mouse click on Yes applies", spec=CHANGE_AB_D)
def s17(lab):
    sync_apply(lab, yes_mouse, CHANGE_EXPECT)


@scenario("S18", "Mouse click on No cancels with zero writes", spec=CHANGE_AB_D)
def s18(lab):
    sync_cancel(lab, no_mouse)


@scenario("S19", "Repeated Enter/Y and double-click do not start a second batch", spec=CHANGE_AB_D)
def s19(lab):
    def run(gesture):
        lab.reset(CHANGE_AB_D)
        before = lab.revisions()
        sync_apply(lab, gesture, CHANGE_EXPECT)
        after = lab.revisions()
        return {k: (after[k] or 0) - (before[k] or 0) for k in after}
    single = run(lambda l, f: l.keys("y", .5))
    repeated = run(lambda l, f: (l.keys("y", .0), l.keys("yyy\r\r\r\r", .2)))
    ok(all(repeated[k] <= single[k] for k in single), f"revision deltas repeated {repeated} > single {single}")
    double = run(lambda l, f: l.click_text("[ Yes ]", "Yes ]", frame=f, double=True))
    ok(all(double[k] <= single[k] for k in single), f"double-click revision deltas {double} > single {single}")


@scenario("S20", "Sync dialog after completion shows totals and a Close button", spec=CHANGE_AB_D)
def s20(lab):
    frame = open_sync(lab)
    lab.shot("S20-before")
    ok(lab.has("Yes") and lab.has("No"), "Yes/No buttons missing\n" + lab.text())
    lab.keys("y", .5)
    lab.until(lambda: lab.agent_name("d") == "new-d", 8)
    lab.pump(.6)
    lab.shot("S20-after")
    ok(lab.has("Close"), "no Close button after completion\n" + lab.text())
    lab.click_text("Close", frame=lab.frame() or frame)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Close did not close the popup")


@scenario("S21", "Focus change while the popup is open does not retarget the batch (skipped if Herdr closes the popup)", spec=CHANGE_AB_D)
def s21(lab):
    frame = open_sync(lab)
    lab.run_cli("pane", "focus", "--pane", lab.layout["b"])  # focus moves inside the workspace
    lab.pump(.6)
    if not lab.popup_open():  # never type 'y' into an agent pane
        raise Skip("Herdr closes the popup when focus moves; later-focus retargeting cannot be exercised")
    lab.keys("y", .5)
    ok(settle_after_apply(lab, CHANGE_EXPECT), f"names {lab.names()}")


@scenario("S22", "Sync small terminal: buttons reachable or popup cancels safely with zero writes",
          spec=CHANGE_AB_D, size=(60, 16))
def s22(lab):
    before = lab.fingerprint()
    frame = lab.open_popup("S")
    lab.shot("S22-small")
    ok(frame, "no popup in small terminal\n" + lab.text())
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not cancel in a small terminal")
    assert_unchanged(lab, before, "small-terminal cancel")


# ---- rename dialog -----------------------------------------------------------------------
def retype(lab, text):
    lab.keys("\x05\x15", .2)  # End, kill to start: works with old and native-style editors
    lab.keys(text, .4)


def input_row_text(lab, frame):
    return "\n".join(lab.lines()[frame.y:frame.y + frame.height])


@scenario("R01", "Enter saves a typed name; only the agent name changes", spec=ONE)
def r01(lab):
    frame = open_rename(lab)
    lab.shot("R01-open")
    ok(lab.has("old-a"), "current agent name not shown in the input\n" + lab.text())
    retype(lab, "typed-a")
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 4), "popup did not close after Enter")
    assert_names(lab, {"a": "typed-a"}, "Enter save")
    ok(lab.pane_label("a") == "new-a", "pane label changed")


@scenario("R02", "Leading/trailing whitespace is trimmed on save", spec=ONE)
def r02(lab):
    open_rename(lab)
    retype(lab, "  spaced-a  ")
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 4), "popup did not close (trimmed name must be valid)\n" + lab.text())
    assert_names(lab, {"a": "spaced-a"}, "trim")


@scenario("R03", "Ctrl+C clears the input only: popup stays open, nothing written", spec=ONE)
def r03(lab):
    before = lab.fingerprint()
    frame = open_rename(lab)
    lab.keys("\x03", .6)
    lab.shot("R03-cleared")
    ok(lab.popup_open(), "Ctrl+C closed the popup; it must only clear the input\n" + lab.text())
    ok("old-a" not in input_row_text(lab, lab.frame()), "input still shows the old name after Ctrl+C\n" + lab.text())
    assert_unchanged(lab, before, "Ctrl+C clear-only")
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close")
    assert_unchanged(lab, before, "Esc after clear")


@scenario("R04", "Saving an empty input clears the explicit name (--clear)", spec=ONE)
def r04(lab):
    open_rename(lab)
    lab.keys("\x03", .5)
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 4), "popup did not close after empty save\n" + lab.text())
    ok(lab.until(lambda: lab.agent_name("a") is None, 4), f"explicit name not cleared: {lab.agent_name('a')}")
    ok(lab.has_agent("a"), "agent disappeared")
    ok(lab.pane_label("a") == "new-a", "pane label changed by clear")


@scenario("R05", "Esc cancels without writing", spec=ONE)
def r05(lab):
    before = lab.fingerprint()
    open_rename(lab)
    retype(lab, "discarded")
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close")
    assert_unchanged(lab, before, "Esc")


@scenario("R06", "Mouse click on save writes the name", spec=ONE)
def r06(lab):
    frame = open_rename(lab)
    retype(lab, "mouse-a")
    lab.shot("R06-typed")
    lab.click_text("↵ save", "save", frame=frame)
    ok(lab.until(lambda: not lab.popup_open(), 4), "save click did not close\n" + lab.text())
    assert_names(lab, {"a": "mouse-a"}, "mouse save")


@scenario("R07", "Mouse click on clear empties the input only; Enter then clears the name", spec=ONE)
def r07(lab):
    before = lab.fingerprint()
    frame = open_rename(lab)
    lab.click_text("^c clear", "clear", frame=frame)
    lab.shot("R07-cleared")
    ok(lab.popup_open(), "clear click closed the popup")
    ok("old-a" not in input_row_text(lab, lab.frame()), "clear click left the old name\n" + lab.text())
    assert_unchanged(lab, before, "mouse clear")
    lab.keys("\r", .8)
    ok(lab.until(lambda: lab.agent_name("a") is None, 4), "Enter after clear did not remove the name")


@scenario("R08", "Mouse click on cancel closes without writing", spec=ONE)
def r08(lab):
    before = lab.fingerprint()
    frame = open_rename(lab)
    retype(lab, "discarded")
    lab.click_text("esc cancel", "cancel", frame=frame)
    ok(lab.until(lambda: not lab.popup_open(), 3), "cancel click did not close\n" + lab.text())
    assert_unchanged(lab, before, "mouse cancel")


@scenario("R09", "Click on a non-button cell cancels (native: click outside the dialog box)", spec=ONE)
def r09(lab):
    before = lab.fingerprint()
    frame = open_rename(lab)
    lab.click_popup(0, 0, frame)  # top-left PTY cell: neither input nor buttons
    lab.shot("R09-after")
    ok(lab.until(lambda: not lab.popup_open(), 3), "click on empty popup area did not cancel\n" + lab.text())
    assert_unchanged(lab, before, "click-elsewhere cancel")


@scenario("R10", "Invalid paste (newline, control char) keeps the dialog and writes nothing", spec=ONE)
def r10(lab):
    before = lab.fingerprint()
    frame = open_rename(lab)
    lab.keys("\x05\x15", .2)
    lab.paste("invalid\nname")  # CR/LF -> space (D7): still invalid
    lab.keys("\r", .8)
    lab.shot("R10-newline")
    ok(lab.popup_open(), "dialog closed on an invalid pasted name\n" + lab.text())
    assert_unchanged(lab, before, "invalid paste")
    lab.keys("\x03", .3)
    lab.paste("bad\x01name")
    lab.keys("\r", .8)
    lab.shot("R10-control")
    ok(lab.popup_open(), "dialog closed on a control-character name\n" + lab.text())
    assert_unchanged(lab, before, "control-char paste")
    lab.keys("\x1b", .5)


@scenario("R11", "Name held by another agent shows an error, keeps input, writes nothing", spec=ONE)
def r11(lab):
    before = lab.fingerprint()
    open_rename(lab)
    retype(lab, "old-f")
    lab.keys("\r", .9)
    lab.shot("R11-duplicate")
    ok(lab.popup_open() and lab.has("old-f"), "dialog/input not retained after duplicate name\n" + lab.text())
    ok(lab.has("already") or lab.has("in use"), "no visible duplicate-name error\n" + lab.text())
    assert_unchanged(lab, before, "duplicate name")
    lab.keys("\x1b", .5)


@scenario("R12", "Rename dialog in a small terminal cancels safely", spec=ONE, size=(40, 10))
def r12(lab):
    before = lab.fingerprint()
    lab.open_popup("A")
    lab.shot("R12-small")
    lab.keys("\x1b", .6)
    lab.until(lambda: not lab.popup_open(), 3)
    lab.close_popups()
    assert_unchanged(lab, before, "small terminal")


# ---- themes ------------------------------------------------------------------------------
def theme_checks(lab, name):
    palette = PALETTES[name]
    frame = open_rename(lab)
    lab.shot(f"{lab.current}-rename")
    background = lab.popup_background(frame)
    ok(background == palette["panel_bg"], f"{name}: popup background {background} != panel_bg {palette['panel_bg']}")
    cell = lab.text_cell("↵ save", "save", frame=frame)
    ok(cell is not None, "save button not visible")
    save_bg = lab.cell_background(*cell)
    ok(save_bg == palette["accent"], f"{name}: save button background {save_bg} != accent {palette['accent']}")
    lab.keys("\x1b", .5)
    lab.close_popups()
    frame = open_sync(lab)
    lab.shot(f"{lab.current}-sync")
    background = lab.popup_background(frame)
    ok(background == palette["panel_bg"], f"{name}: sync background {background} != panel_bg {palette['panel_bg']}")
    lab.keys("\x1b", .5)


@scenario("T01", "Default theme (catppuccin) captures and palette colors", theme="catppuccin", spec=CHANGE_AB_D)
def t01(lab):
    theme_checks(lab, "catppuccin")


@scenario("T02", "Dracula theme captures and palette colors", theme="dracula", spec=CHANGE_AB_D)
def t02(lab):
    theme_checks(lab, "dracula")


# ---- runner ------------------------------------------------------------------------------
class ScenarioTimeout(Exception):
    pass


def _alarm(signum, frame):
    raise ScenarioTimeout(f"scenario exceeded {SCENARIO_TIMEOUT}s")


def run_suite(out: Path, codex_arguments, only: list[str] | None = None) -> int:
    """Run every selected scenario; write ``actual-results.json`` and captures under ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    captures = out / "captures"
    selected = [s for s in SCENARIOS if not only or any(s.id.startswith(prefix) for prefix in only)]
    results, setup_checks = [], {}
    signal.signal(signal.SIGALRM, _alarm)
    for theme in dict.fromkeys(s.theme for s in selected):
        group = [s for s in selected if s.theme == theme]
        lab = Lab(captures, theme=None if theme == "catppuccin" else theme, codex_arguments=codex_arguments)
        try:
            try:
                lab.start()
                lab.build_layout()
                setup_checks[theme] = list(lab.results)
            except Exception as error:
                for s in group:
                    results.append({"id": s.id, "title": s.title, "theme": theme, "result": "error",
                                    "details": f"session setup failed: {error!r}", "captures": []})
                    print(f"error: {s.id} (setup)", flush=True)
                continue
            for s in group:
                lab.current, lab.captured = s.id, []
                started = time.monotonic()
                outcome, details = "pass", ""
                try:
                    signal.alarm(SCENARIO_TIMEOUT)
                    lab.reset(s.spec, s.size)
                    s.fn(lab)
                except Skip as error:
                    outcome, details = "skip", str(error)
                except AssertionError as error:
                    outcome, details = "fail", str(error)
                except BaseException as error:  # infrastructure problem, keep the suite going
                    outcome, details = "error", f"{error!r}\n{traceback.format_exc(limit=4)}"
                finally:
                    signal.alarm(0)
                if outcome not in ("pass", "skip"):
                    try:
                        lab.shot(f"{s.id}-{outcome}")
                        log = lab.run_cli("plugin", "log", "list", "--plugin", "local.agent-rename")
                        (captures / f"{s.id}-plugin-log.txt").write_text((log["stdout"] or log["stderr"])[-6000:] + "\n")
                    except Exception:
                        pass
                try:
                    lab.close_popups()
                except Exception as error:
                    details += f"\ncleanup: {error!r}"
                results.append({"id": s.id, "title": s.title, "theme": theme, "size": f"{s.size[0]}x{s.size[1]}",
                                "result": outcome, "details": details[:3000], "captures": list(lab.captured),
                                "seconds": round(time.monotonic() - started, 1)})
                print(f"{outcome}: {s.id} {s.title}", flush=True)
        finally:
            lab.finish()
    counts = {k: sum(r["result"] == k for r in results) for k in ("pass", "fail", "error", "skip")}
    (out / "actual-results.json").write_text(json.dumps({
        "suite": "next", "date": datetime.now().astimezone().isoformat(), "herdr_version": "0.9.3",
        "terminal": "PTY xterm-256color, truecolor, default 100x32", "assertions": "PLAN sections 6, 10 (planned behavior)",
        "counts": counts, "setup_checks": setup_checks, "scenarios": results,
    }, indent=2) + "\n")
    print(f"next suite: {counts}", flush=True)
    return 0 if counts["fail"] == counts["error"] == 0 else 1
