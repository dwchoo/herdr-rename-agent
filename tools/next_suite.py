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
import os
from pathlib import Path
import signal
import time
import tomllib
import traceback

from herdr_lab import Lab, PALETTES, find

Scenario = namedtuple("Scenario", "id title theme size spec fn")
SCENARIOS: list[Scenario] = []
AGENTS = ("a", "b", "d", "e", "f")
SCENARIO_TIMEOUT = 75
TIMEOUTS = {"S28": 300, "S29": 150, "S26": 120}
LIFECYCLE = "lifecycle"  # pseudo-theme: these scenarios share one fresh, un-installed session and run in order
# Key UI states that also get a PNG (everything else is TXT only).
PNG_KEEP = {"T01-rename", "T01-sync", "T02-rename", "T02-sync", "R01-open", "S10-dialog", "S27-running", "S20-after",
            "native-pane-rename-catppuccin", "plugin-rename-catppuccin", "native-pane-rename-dracula",
            "plugin-rename-dracula", "S06-dialog", "S22-small", "R12-small", "S23-80cols", "R13-resized", "S25-done", "S28-stop-0ms"}


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


# ---- sync: narrow clients, resize, stale preview, cancel -----------------------------------
def frame_text(lab):
    frame = lab.frame()
    return "\n".join(lab.lines()[frame.y:frame.y + frame.height]) if frame else lab.text()


@scenario("S23", "Sync popup at an 80-column client lays out the table with visible Yes/No",
          spec=CHANGE_AB_D, size=(80, 24))
def s23(lab):
    before = lab.fingerprint()
    open_sync(lab)
    lab.shot("S23-80cols")
    ok(lab.has("Yes") and lab.has("No"), "Yes/No not visible at 80 columns\n" + lab.text())
    ok(not lab.has("Enlarge the terminal"), "resize message shown at 80x24 although the popup has room\n" + lab.text())
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close the popup")
    assert_unchanged(lab, before, "cancel at 80 columns")


@scenario("S24", "Sync popup at a narrow client (50 columns) shows the resize message; Y/Enter ignored; Esc cancels",
          spec=CHANGE_AB_D, size=(50, 20))
def s24(lab):
    before = lab.fingerprint()
    open_sync(lab)
    lab.shot("S24-narrow")
    ok(lab.has("Enlarge the terminal"), "no resize message in a 50-column client\n" + lab.text())
    ok(not lab.has("[ Yes ]"), "Yes button offered although the dialog does not fit\n" + lab.text())
    lab.keys("y", .6)
    lab.keys("\r", .6)
    lab.pump(.8)
    assert_unchanged(lab, before, "Y/Enter while the dialog is too small")
    ok(lab.popup_open(), "popup closed on Y/Enter although it only shows a resize message")
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close the popup")
    assert_unchanged(lab, before, "Esc in a narrow client")


@scenario("S25", "Resize while the sync popup is open: re-layout, controls visible, Yes still applies",
          spec=CHANGE_AB_D, size=(80, 24))
def s25(lab):
    open_sync(lab)
    ok(lab.has("Yes") and lab.has("No"), "Yes/No missing before resize\n" + lab.text())
    lab.set_size(120, 40)
    lab.pump(.8)
    lab.shot("S25-large")
    ok(lab.popup_open(), "popup closed by a resize to a larger client\n" + lab.text())
    ok(lab.has("[ Yes ]") and lab.has("[ No ]") and lab.has("Sync agent names"), "dialog not re-laid out after growing\n" + lab.text())
    lab.set_size(50, 20)
    lab.pump(.8)
    ok(lab.popup_open(), "popup closed by a resize to a narrow client\n" + lab.text())
    ok(lab.has("Enlarge the terminal"), "no resize message after shrinking\n" + lab.text())
    lab.set_size(100, 32)
    lab.pump(.8)
    lab.shot("S25-done")
    ok(lab.popup_open() and lab.has("[ Yes ]") and lab.has("[ No ]"), "controls not restored after growing again\n" + lab.text())
    lab.keys("y", .5)
    ok(settle_after_apply(lab, CHANGE_EXPECT), f"Yes after resizes did not apply: {lab.names()}\n{lab.text()}")


def scan_rows(lab, count):
    """Popup text while the cursor visits every row (the detail line shows the full reason)."""
    lab.keys("\x1b[H", .2)
    seen = [frame_text(lab)]
    for _ in range(count + 1):
        lab.keys("\x1b[B", .15)
        seen.append(frame_text(lab))
    return "\n".join(seen)


def with_extra_agents(lab, count, fn):
    """Create ``count`` temporary eligible panes next to c (agent old-x<i>, label new-x<i>); always closed again."""
    extra = []
    try:
        for index in range(count):
            pane = find(lab.run_cli("pane", "split", lab.layout["c"], "--direction", "right", "--no-focus")["json"], "pane_id")
            extra.append(pane)
            lab.start_agent(pane, f"old-x{index}")
            lab.run_cli("pane", "rename", pane, f"new-x{index}")
        lab.pump(.5)
        return fn(extra)
    finally:
        for pane in extra:
            lab.run_cli("pane", "close", pane)
        lab.pump(.5)


STALE = base(a=("new-a", "old-a"), b=("new-b", "old-b"), d=("new-d", "old-d"), e=("new-e", "old-e"))


@scenario("S26", "Stale preview: changed label, closed pane and renamed agent are skipped with reasons; others sync",
          spec=STALE)
def s26(lab):
    def body(extra):
        open_sync(lab)
        lab.shot("S26-dialog")
        ok(lab.has("new-x0"), "extra pane missing from the preview\n" + lab.text())
        ok(lab.run_cli("pane", "rename", lab.layout["a"], "newer-a")["rc"] == 0, "label change failed")
        ok(lab.run_cli("pane", "close", extra[0])["rc"] == 0, "pane close failed")
        ok(lab.run_cli("agent", "rename", lab.layout["d"], "renamed-d")["rc"] == 0, "agent rename failed")
        lab.pump(.6)
        if not lab.popup_open():
            raise Skip("Herdr closed the popup when the pane was closed; staleness cannot be exercised")
        lab.keys("y", .5)
        done = lab.until(lambda: lab.agent_name("b") == "new-b" and lab.agent_name("e") == "new-e" and lab.has("[ Close ]"), 12)
        lab.pump(.5)
        lab.shot("S26-after")
        text = scan_rows(lab, 5)
        lab.dismiss()
        names = lab.names()
        ok(done, f"independent rows b/e were not synced; names {names}\n{text}")
        ok(names["a"] == "old-a", f"a was written ({names['a']!r}) although its label changed")
        ok(names["d"] == "renamed-d", f"d was overwritten ({names['d']!r}) although its agent was renamed")
        ok("newer-a" not in {a.get("name") for a in lab.agents()}, "a write used the new label")
        (lab.destination / "S26-reasons.txt").write_text(text + "\n")
        # The detail line wraps inside the popup, so match short fragments of each reason.
        for reason in ("pane name has changed", "pane is no longer", "agent name has"):
            ok(reason in text, f"skip reason {reason!r} not shown (see S26-reasons.txt)\n{text[-1500:]}")
        ok("5 skipped" in text and "2 synced" in text, f"summary should read 2 synced, 5 skipped (3 stale + 2 without a pane name)\n{text}")
    with_extra_agents(lab, 1, body)


@scenario("S27", "Label cleared after the preview opened: the row is skipped, no write with the old label",
          spec=base(a=("new-a", "old-a"), b=("new-b", "old-b")))
def s27(lab):
    open_sync(lab)
    ok(lab.run_cli("pane", "rename", lab.layout["a"], "--clear")["rc"] == 0, "label clear failed")
    lab.pump(.5)
    if not lab.popup_open():
        raise Skip("Herdr closed the popup after the label change")
    lab.keys("y", .5)
    ok(lab.until(lambda: lab.agent_name("b") == "new-b" and lab.has("[ Close ]"), 10), f"b not synced: {lab.names()}\n{lab.text()}")
    lab.pump(.4)
    lab.shot("S27-after")
    text = scan_rows(lab, 5)
    lab.dismiss()
    ok(lab.agent_name("a") == "old-a", f"a was written after its label was cleared: {lab.agent_name('a')!r}")
    ok("pane name was removed" in text, f"skip reason missing\n{text}")


S28_SPEC = base(a=("new-a", "old-a"), b=("new-b", "old-b"), d=("new-d", "old-d"), e=("new-e", "old-e"))


def stop_attempt(lab, extra, delay):
    """Yes, then Ctrl+C (Stop; Herdr holds a lone Esc for >170 ms) ``delay`` seconds later (same write when 0). Returns (synced, not_attempted, text, names)."""
    lab.reset(S28_SPEC)
    for pane in extra:
        lab.run_cli("agent", "rename", pane, "--clear")
    open_sync(lab)
    ok(lab.has("[ Yes ]"), "confirm dialog missing\n" + lab.text())
    if delay == 0:
        os.write(lab.fd, b"y\x03")
    else:
        os.write(lab.fd, b"y")
        time.sleep(delay)
        os.write(lab.fd, b"\x03")
    lab.until(lambda: not lab.popup_open() or lab.has("[ Close ]"), 15)
    lab.pump(.3)
    text = frame_text(lab) if lab.popup_open() else ""
    lab.shot(f"S28-stop-{int(delay * 1000)}ms")
    lab.dismiss()
    targets = {lab.layout[k]: f"new-{k}" for k in ("a", "b", "d", "e")}
    targets.update({pane: f"new-x{i}" for i, pane in enumerate(extra)})
    current = {a["pane_id"]: a.get("name") for a in lab.agents()}
    synced = [p for p, name in targets.items() if current.get(p) == name]
    untouched = [p for p, name in targets.items() if current.get(p) in (None, "") or str(current.get(p)).startswith("old-")]
    ok(len(synced) + len(untouched) == len(targets), f"a row is neither synced nor untouched after Stop: {current}\n{text}")
    ok(lab.agent_name("f") == "old-f", "control agent in the other workspace changed")
    ok("failed" not in text and "uncertain" not in text, "Stop produced failed/uncertain rows\n" + text)
    return len(synced), len(untouched), text, current


@scenario("S28", "Cancel during application: Stop leaves later rows not attempted and the in-flight row reconciled",
          spec=S28_SPEC)
def s28(lab):
    seen = {}

    def body(extra):
        total = 4 + len(extra)
        for delay in (.01, .02, .03, .045, .06, .08, .11, .15):
            synced, untouched, text, _ = stop_attempt(lab, extra, delay)
            seen[delay] = (synced, untouched)
            if 0 < synced < total:
                ok(untouched >= 1 and "not attempted" in text and "Canceled" in text,
                   f"partial batch ({synced} synced) but the summary lacks 'Canceled'/'not attempted'\n{text}")
                return
        raise Skip("rows complete in about 20 ms each, so a Stop could not be placed reliably inside the batch "
                   f"(delay -> (synced, untouched): {seen}); the Stop-before-first-row latch is covered by S29")
    with_extra_agents(lab, 3, body)


@scenario("S29", "Stop requested together with Yes: nothing half-applied, canceled rows reported not attempted",
          spec=S28_SPEC)
def s29(lab):
    def body(extra):
        total = 4 + len(extra)
        synced, untouched, text, _ = stop_attempt(lab, extra, 0)
        ok(synced + untouched == total, "rows unaccounted for")
        if untouched:
            ok("Canceled" in text and "not attempted" in text, f"summary lacks 'Canceled'/'not attempted'\n{text}")
        else:
            raise Skip("Ctrl+C reached the dialog only after the batch had finished; the latch could not be observed")
        ok(synced == 0, f"{synced} rows were applied although Stop arrived in the same key batch as Yes")
    with_extra_agents(lab, 3, body)


@scenario("R13", "Resize while the rename popup is open: input preserved, buttons visible, save still works", spec=ONE)
def r13(lab):
    open_rename(lab)
    retype(lab, "resized-name")
    for cols, rows in ((140, 45), (70, 20), (100, 32)):
        lab.set_size(cols, rows)
        lab.pump(.8)
        ok(lab.popup_open(), f"popup closed by a resize to {cols}x{rows}\n" + lab.text())
        body = frame_text(lab)
        ok("resized-name" in body, f"typed input lost after resizing to {cols}x{rows}\n" + lab.text())
        ok("save" in body and "clear" in body and "cancel" in body, f"buttons not visible at {cols}x{rows}\n" + lab.text())
    lab.shot("R13-resized")
    lab.set_size(45, 12)
    lab.pump(.8)
    lab.shot("R13-narrow")
    small = lab.popup_open() and "Enlarge the terminal" in frame_text(lab)
    lab.set_size(100, 32)
    lab.pump(.8)
    ok(lab.popup_open(), "popup lost after shrinking and growing\n" + lab.text())
    ok("resized-name" in frame_text(lab) and "save" in frame_text(lab), "input or buttons lost after the shrink/grow cycle\n" + lab.text())
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 4), "Enter did not close the popup")
    assert_names(lab, {"a": "resized-name"}, "save after resizing")


# ---- installer lifecycle on the current runtime (own un-installed session, run in order) -----
PLUGIN = "local.agent-rename"
OWNED = {PLUGIN + ".open", PLUGIN + ".sync"}
LEGACY_TAG_DIR = "legacy-v0.1.0"
LIFE: dict = {}
USER_EDIT = """
# User change made after installation; preserve this comment.
[ui]
sidebar_width = 31
[[keys.command]]
key = "prefix+alt+y"
type = "shell"
description = "User binding after installation"
command = "true"
"""
BLOCKING_BINDING = """
[[keys.command]]
key = "prefix+shift+a"
type = "shell"
description = "Newer user binding on a displaced shortcut"
command = "true"
"""


def lifecycle(id, title):
    return scenario(id, title, theme=LIFECYCLE, spec={})


def user_commands(lab):
    return tomllib.loads(lab.config.read_text()).get("keys", {}).get("command", [])


def owned_bindings(lab):
    return sorted((c["key"], c["type"], c["command"]) for c in user_commands(lab) if c.get("command") in OWNED)


def records(lab):
    return sorted((lab.base / "state/herdr/plugins" / PLUGIN / "installations").glob("*.json"))


def plugins(lab):
    return lab.cli("plugin", "list")["result"]["plugins"]


def plugin_root(lab):
    found = [p for p in plugins(lab) if p["plugin_id"] == PLUGIN]
    return Path(found[0]["plugin_root"]).resolve() if found else None


def ensure_agent(lab):
    """Dedicated idle agent in the session's only pane, with an explicit pane name (for sync)."""
    if lab.run_cli("agent", "get", lab.pane)["rc"] != 0:
        lab.start_agent(lab.pane, "alpha")
    lab.run_cli("pane", "rename", lab.pane, "backend-api")


def installed_state(lab):
    return {"config": lab.config.read_bytes(), "records": [r.read_bytes() for r in records(lab)], "owned": owned_bindings(lab)}


@lifecycle("L01", "Installer dry-run on a fresh sandbox config previews the edit and changes nothing")
def l01(lab):
    LIFE["original"] = lab.config.read_bytes()
    ok(not records(lab) and not plugins(lab), "fixture is not a fresh, un-installed session")
    rc, out, err = lab.manage_at(lab.root, "install", "--dry-run")
    ok("prefix+shift+a" in out and "prefix+shift+s" in out and "Dry run" in out, f"dry-run output incomplete:\n{out}{err}")
    ok(lab.config.read_bytes() == LIFE["original"] and not records(lab) and not plugins(lab), "dry-run changed config, record or registry")


@lifecycle("L02", "Fresh bin/install: legacy popup migrated, two plugin_action bindings, registered from this checkout")
def l02(lab):
    lab.manage("install")
    commands = user_commands(lab)
    ok({c["command"] for c in commands} == OWNED and all(c["type"] == "plugin_action" for c in commands),
       f"unexpected bindings after install: {commands}")
    ok(sorted(c["key"] for c in commands) == ["prefix+shift+a", "prefix+shift+s"], f"unexpected keys: {commands}")
    ok(plugin_root(lab) == lab.root.resolve(), f"plugin not registered from this checkout: {plugins(lab)}")
    ok(all(p["enabled"] for p in plugins(lab)), "plugin not enabled")
    found = records(lab)
    ok(len(found) == 1, f"expected one recovery record, found {found}")
    record = json.loads(found[0].read_text())
    ok(len(record["displaced"]) == 1 and "HERDR_ACTIVE_PANE_ID" in record["displaced"][0]["table"],
       "legacy rename popup was not saved in the recovery record")
    ok((found[0].stat().st_mode & 0o777) == 0o600, "recovery record is not private (0600)")
    actions = lab.cli("plugin", "action", "list", "--plugin", PLUGIN)["result"]["actions"]
    ok(len(actions) == 2, f"expected 2 actions, found {actions}")
    LIFE["installed"] = installed_state(lab)


@lifecycle("L03", "Both shortcuts open the new dialogs after install; cancelling writes nothing")
def l03(lab):
    ensure_agent(lab)
    before = lab.fingerprint()
    frame = lab.open_popup("A")
    lab.shot("L03-rename")
    ok(frame and lab.has("alpha"), "Ctrl+B Shift+A did not open the rename dialog\n" + lab.text())
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close rename")
    frame = lab.open_popup("S")
    lab.shot("L03-sync")
    ok(frame and lab.has("[ Yes ]") and lab.has("[ No ]") and lab.has("backend-api"), "Ctrl+B Shift+S did not open the sync dialog\n" + lab.text())
    lab.keys("n", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "N did not close sync")
    ok(lab.fingerprint() == before, "opening and cancelling the dialogs changed agent state")


@lifecycle("L04", "Repeated install keeps one binding per action, the same recovery record and one registration")
def l04(lab):
    state = LIFE["installed"]
    lab.manage("install")
    ok(lab.config.read_bytes() == state["config"], "repeated install changed the config")
    ok([r.read_bytes() for r in records(lab)] == state["records"], "repeated install changed the recovery record")
    ok(owned_bindings(lab) == state["owned"] and len(owned_bindings(lab)) == 2, "duplicate or missing bindings")
    ok(len([p for p in plugins(lab) if p["plugin_id"] == PLUGIN]) == 1, "duplicate registration")
    rc, out, err = lab.manage_at(lab.root, "uninstall", "--dry-run")
    ok(lab.config.read_bytes() == state["config"] and plugin_root(lab) == lab.root.resolve() and "Dry run" in out,
       "uninstall --dry-run changed state")


@lifecycle("L05", "Rollback: v0.1.0 is refused over a registration from another checkout; new -> v0.1.0 -> new keeps the bindings")
def l05(lab):
    legacy = lab.root / LEGACY_TAG_DIR
    if not (legacy / "bin/install").exists():
        raise Skip("tag v0.1.0 could not be exported into the sandbox (no git or tag)")
    if not (legacy / ".venv").exists():
        os.symlink(lab.root / ".venv", legacy / ".venv")
    state = installed_state(lab)
    rc, out, err = lab.manage_at(legacy, "install", expected=False)
    ok(rc != 0 and "another checkout" in err, f"v0.1.0 install over the new registration should be refused: rc={rc}\n{out}{err}")
    ok(lab.config.read_bytes() == state["config"] and plugin_root(lab) == lab.root.resolve(), "refused install changed state")
    lab.manage("uninstall")
    ok(not owned_bindings(lab) and plugin_root(lab) is None, "uninstall of the new runtime left bindings or registration")
    lab.manage_at(legacy, "install")
    ok(plugin_root(lab) == legacy.resolve(), f"v0.1.0 not registered: {plugins(lab)}")
    ok(owned_bindings(lab) == state["owned"], f"v0.1.0 bindings differ: {owned_bindings(lab)} vs {state['owned']}")
    ensure_agent(lab)
    frame = lab.open_popup("A")
    lab.shot("L05-legacy-rename")
    ok(frame and lab.has("alpha"), "shortcut does not open the v0.1.0 rename dialog\n" + lab.text())
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close the v0.1.0 dialog")
    lab.manage_at(legacy, "uninstall")
    ok(not owned_bindings(lab) and plugin_root(lab) is None, "v0.1.0 uninstall left bindings or registration")
    lab.manage("install")
    ok(plugin_root(lab) == lab.root.resolve(), "new runtime not registered after rollback cycle")
    ok(owned_bindings(lab) == state["owned"], "bindings changed across the rollback cycle")
    ok(lab.config.read_bytes() == state["config"], "config bytes differ after the rollback cycle")
    ok([r.read_bytes() for r in records(lab)] == state["records"], "recovery record differs after the rollback cycle")
    frame = lab.open_popup("S")
    ok(frame and lab.has("[ Yes ]"), "new sync dialog not available after the cycle\n" + lab.text())
    lab.keys("n", .6)
    lab.close_popups()


@lifecycle("L06", "Later user edits survive bin/uninstall; only plugin bindings go and the legacy popup returns")
def l06(lab):
    lab.config.write_text(lab.config.read_text() + USER_EDIT)
    lab.manage("uninstall")
    text = lab.config.read_text()
    restored = tomllib.loads(text)
    bindings = restored["keys"]["command"]
    ok(restored["ui"]["sidebar_width"] == 31 and "preserve this comment" in text, "later setting or comment was lost")
    ok(any(b["command"] == "true" for b in bindings), "later user binding was removed")
    ok(not any(b["command"] in OWNED for b in bindings), "plugin bindings remain")
    ok(any(b["type"] == "popup" and "HERDR_ACTIVE_PANE_ID" in b["command"] for b in bindings) and len(bindings) == 2,
       f"only the displaced legacy popup should be restored: {bindings}")
    ok(not plugins(lab) and not records(lab), "registration or resolved recovery record remains")
    lab.keys("\x02", .15)
    os.write(lab.fd, b"S")
    lab.pump(.8)
    ok(not lab.popup_open(), "plugin shortcut still opens a popup after uninstall\n" + lab.text())
    LIFE["uninstalled"] = lab.config.read_bytes()


@lifecycle("L07", "Repeated uninstall is harmless")
def l07(lab):
    rc, out, err = lab.manage_at(lab.root, "uninstall", expected=False)
    ok(rc == 0 and lab.config.read_bytes() == LIFE["uninstalled"], f"second uninstall changed the config or failed: {out}{err}")
    ok(not plugins(lab) and not records(lab), "registry or record changed")


@lifecycle("L08", "Existing recovery record is retained when a newer user binding blocks restoration")
def l08(lab):
    lab.manage("install")
    ok(len(records(lab)) == 1, "no recovery record after reinstall")
    lab.config.write_text(lab.config.read_text() + BLOCKING_BINDING)
    rc, out, err = lab.manage_at(lab.root, "uninstall", expected=False)
    ok(rc == 0, f"uninstall failed: {out}{err}")
    ok("Recovery record" in out, f"uninstall did not mention the retained record:\n{out}")
    found = records(lab)
    ok(len(found) == 1 and "HERDR_ACTIVE_PANE_ID" in found[0].read_text(), "recovery record not retained with the blocked legacy binding")
    commands = user_commands(lab)
    ok(not any(c["command"] in OWNED for c in commands), "plugin bindings remain")
    ok(sum(c["key"] == "prefix+shift+a" for c in commands) == 1 and any(c.get("description", "").startswith("Newer user") for c in commands),
       f"newer user binding was replaced or duplicated: {commands}")
    ok(not plugins(lab), "plugin still registered")


class LifecycleLab(Lab):
    """A session with the legacy popup binding and nothing installed (the installer is the subject)."""
    root = Path(__file__).resolve().parents[1]

    def start(self):
        self.launch()
        self.pane = self.cli("pane", "list")["result"]["panes"][0]["pane_id"]
        self.layout = {"a": self.pane}
        self.agent_keys = ()
        self.pump(.3)

    def reset(self, spec=None, size=(100, 32)):
        self.close_popups()
        self.pump(.3)

    def build_layout(self):
        pass


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


# ---- native pane rename overlay vs plugin rename popup (same theme, same client size) ---------
PARITY_LABELS = (" ↵ save ", " ^c clear ", " esc cancel ")


def native_box(lab):
    """Border box (x, y, width, height) of Herdr's own 'rename pane' overlay, or None. Its title is the first content row."""
    lines = lab.lines()
    for y, line in enumerate(lines):
        at = line.find("rename pane")
        if at < 0 or y == 0:
            continue
        top = lines[y - 1]
        left = max((top.rfind(c, 0, at) for c in "┌╭"), default=-1)
        rights = [i for i in (top.find(c, at) for c in "┐╮") if i >= 0]
        if left < 0 or not rights:
            continue
        right = min(rights)
        for by in range(y + 1, len(lines)):
            if len(lines[by]) > right and lines[by][left] in "└╰" and lines[by][right] in "┘╯":
                return left, y - 1, right - left + 1, by - y + 2
    return None


class Box:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


def parity_points(lab, x, y, width, height, title_text, fixed_text):
    """Observable layout/color points of one dialog. (x, y, width, height) is the content area."""
    lines = lab.lines()
    rows = lambda text: [i - y for i in range(y, y + height) if text in lines[i][x:x + width]]
    buttons = {}
    for label in PARITY_LABELS:
        at = next(((i, lines[i].find(label, x, x + width)) for i in range(y, y + height) if label in lines[i][x:x + width]), None)
        ok(at is not None and at[1] >= 0, f"button label {label!r} not found in the dialog\n" + lab.text())
        buttons[label] = (at[0] - y, at[1] - x)
    row = {"title": rows(title_text), "input": rows(fixed_text)}
    ok(row["title"] and row["input"], f"title {title_text!r} or input {fixed_text!r} not found\n" + lab.text())
    colors = {"panel_bg": lab.popup_background(Box(x - 1, y - 1, width + 2, height + 2))}
    for name, label in (("save", PARITY_LABELS[0]), ("clear", PARITY_LABELS[1]), ("cancel", PARITY_LABELS[2])):
        cx, cy = x + buttons[label][1] + 3, y + buttons[label][0]
        colors[name + "_bg"] = lab.cell_background(cx, cy)
        colors[name + "_fg"] = lab.screen.buffer[cy][cx].fg
    return {"title_row": row["title"][0], "input_row": row["input"][0], "button_rows": sorted({v[0] for v in buttons.values()}),
            "button_cols": {label.strip(): buttons[label][1] for label in PARITY_LABELS}, "colors": colors, "content": [width, height]}


def parity_checks(lab, theme):
    palette = PALETTES[theme]
    # Native pane rename overlay: Ctrl+B Shift+P (default rename-pane binding) on the focused pane (label new-a).
    lab.keys("\x02", .15)
    os.write(lab.fd, b"P")
    lab.until(lambda: native_box(lab), 5, .05)
    lab.pump(.4)
    box = native_box(lab)
    lab.shot(f"native-pane-rename-{theme}")
    ok(box, "Herdr's native pane rename overlay did not open (Ctrl+B Shift+P)\n" + lab.text())
    nx, ny, nw, nh = box
    native = parity_points(lab, nx + 1, ny + 1, nw - 2, nh - 2, "rename pane", "new-a")
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: native_box(lab) is None, 3), "native overlay did not close on Esc")
    lab.close_popups()
    frame = open_rename(lab)
    lab.shot(f"plugin-rename-{theme}")
    px, py, pw, ph = frame.inner
    plugin = parity_points(lab, px, py, 54, 5, "rename agent", "old-a")
    lab.keys("\x1b", .5)
    detail = {"native": native, "plugin": plugin, "native_box": [nx, ny, nw, nh], "plugin_title": frame.title,
              "plugin_frame": [frame.x, frame.y, frame.width, frame.height], "client": list(lab.client_size)}
    (lab.destination / f"parity-{theme}.json").write_text(json.dumps(detail, indent=2) + "\n")
    lab.captured.append(f"parity-{theme}")
    ok(native["colors"]["panel_bg"] == palette["panel_bg"] and native["colors"]["save_bg"] == palette["accent"],
       f"{theme}: native colors {native['colors']} differ from the expected palette")
    ok(plugin["button_cols"] == native["button_cols"], f"button column offsets differ: native {native['button_cols']} plugin {plugin['button_cols']}")
    ok(plugin["button_rows"] == native["button_rows"], f"button rows differ: native {native['button_rows']} plugin {plugin['button_rows']}")
    ok(plugin["title_row"] == native["title_row"] and plugin["input_row"] == native["input_row"],
       f"title/input rows differ: native {native['title_row']}/{native['input_row']} plugin {plugin['title_row']}/{plugin['input_row']}")
    ok(plugin["colors"] == native["colors"], f"colors differ: native {native['colors']} plugin {plugin['colors']}")


@scenario("T03", "Native pane rename overlay and plugin rename popup match (catppuccin, 100x32)", theme="catppuccin", spec=CHANGE_AB_D)
def t03(lab):
    parity_checks(lab, "catppuccin")


@scenario("T04", "Native pane rename overlay and plugin rename popup match (dracula, 100x32)", theme="dracula", spec=CHANGE_AB_D)
def t04(lab):
    parity_checks(lab, "dracula")


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
        kind = LifecycleLab if theme == LIFECYCLE else Lab
        lab = kind(captures, theme=None if theme in ("catppuccin", LIFECYCLE) else theme, codex_arguments=codex_arguments)
        lab.png_keep = set(PNG_KEEP)
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
                    signal.alarm(TIMEOUTS.get(s.id, SCENARIO_TIMEOUT))
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
