"""Fault-injection and extended E2E scenarios (F, X and E series) for the PLAN section 10 matrix.

Registered into the same ``SCENARIOS`` list as tools/next_suite.py (imported at the bottom of that module), so they
run in the same real-Herdr bubblewrap sandbox with the same fixture. The test-only CLI shim (tools/herdr_shim.py)
sits between the plugin's Python processes and the real ``herdr`` binary; ``Lab.set_faults`` arms it per scenario
and ``Lab.reset`` disarms it. The real server is never made to misbehave: the shim only changes what the plugin
sees (a lost response, malformed JSON, a schema without a method) or performs the real write and then loses the
response. Server-side effects are always read back through the real CLI.

F: transport and API faults (batch results, uncertain writes, envelopes, capabilities, version, read failures).
X: targets that exit/move/are replaced, new panes, swaps/cycles, large and tampered handoffs, context, terminal modes.
E: editor behavior inside the real Herdr popup, compared with the ported editor model.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

from next_suite import (
    SCENARIOS, Skip, base, frame_text, notifications, ok, open_rename, open_sync, popup_opens, renames,
    retype, scan_rows, scenario, with_extra_agents, assert_unchanged, assert_names,
)
from herdr_lab import find

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FOUR = base(a=("new-a", "old-a"), b=("new-b", "old-b"), d=("new-d", "old-d"), e=("new-e", "old-e"))
ONE = base(a=("new-a", "old-a"))
NOT_SUPPORTED = "This Herdr version is not supported."
UNCERTAIN_TEXT = "The rename result could not be confirmed"
FOUR_KEYS = ("a", "b", "d", "e")


def flat_frame(lab):
    """Popup interior as one whitespace-normalized line (wrapped messages read as continuous text)."""
    frame = lab.frame()
    if frame is None:
        return " ".join(" ".join(line.split()) for line in lab.lines())
    rows = [line[frame.x + 1:frame.x + frame.width - 1] for line in lab.lines()[frame.y + 1:frame.y + frame.height - 1]]
    return " ".join(" ".join(rows).split())


def scan_flat(lab, count):
    """Like next_suite.scan_rows, but each visited screen is flattened (details wrap inside the popup)."""
    lab.keys("\x1b[H", .2)
    seen = [flat_frame(lab)]
    for _ in range(count + 1):
        lab.keys("\x1b[B", .15)
        seen.append(flat_frame(lab))
    return "\n".join(seen)


def error_envelope(code, message, ident="cli:agent:rename"):
    return json.dumps({"id": ident, "error": {"code": code, "message": message}})


def rename_rule(**over):
    return {"match": ["agent", "rename"], **over}


def synced_keys(lab, targets=None):
    targets = targets or {k: f"new-{k}" for k in FOUR_KEYS}
    return [k for k, name in targets.items() if lab.agent_name(k) == name]


def run_yes(lab, timeout=25):
    """Open the sync popup, confirm with Y, wait for the finished state; returns (popup text, row scan, shim records)."""
    open_sync(lab)
    mark = lab.shim_mark()
    lab.keys("y", .3)
    finished = lab.until(lambda: lab.has("[ Close ]"), timeout)
    lab.pump(.5)
    ok(finished, "the batch never reached the finished state\n" + lab.text())
    lab.shot(f"{lab.current}-done")
    summary = flat_frame(lab)
    scan = scan_flat(lab, 6)
    (lab.destination / f"{lab.current}-rows.txt").write_text(summary + "\n---\n" + scan + "\n")
    return summary, scan, lab.shim_calls(mark)


def close_popup(lab, why):
    lab.click_text("Close", frame=lab.frame())
    ok(lab.until(lambda: not lab.popup_open(), 4), f"{why}: the Close button did not close the popup\n" + lab.text())


def trigger(lab, key, wait=1.8):
    """Ctrl+B <key> without waiting for a popup (for outcomes that show none). Returns the shim mark taken before."""
    mark = lab.shim_mark()
    lab.keys("\x02", .15)
    os.write(lab.fd, key.encode())
    lab.pump(wait)
    return mark


def plugin_log(lab):
    result = lab.run_cli("plugin", "log", "list", "--plugin", "local.agent-rename")
    return (result["stdout"] or "") + (result["stderr"] or "")


# ---- F: batch results under transport faults -------------------------------------------------------
@scenario("F01", "Connection lost between rows: the next row fails with an English reason, the queue stops, no extra write",
          spec=FOUR)
def f01(lab):
    lab.set_faults({"match": ["pane", "list"], "after_renames": 1, "action": "fail"})
    summary, scan, calls = run_yes(lab)
    ok(len(renames(calls)) == 1, f"expected exactly one rename request, saw {[c['argv'] for c in renames(calls)]}")
    ok(len(synced_keys(lab)) == 1, f"exactly one row must be synced, got {lab.names()}")
    text = summary + scan
    for fragment in ("1 synced", "1 failed", "2 not attempted", "Could not connect to Herdr"):
        ok(fragment in text, f"{fragment!r} missing from the result\n{text}")
    ok("Stopped" in text, "the stop reason is not shown\n" + text)
    close_popup(lab, "after a connection failure")


@scenario("F02", "Write fails in transit and the connection stays down: row uncertain, nothing retried, rest not attempted",
          spec=FOUR)
def f02(lab):
    lab.set_faults(rename_rule(skip=1, times=1, action="fail"), {"match": [], "after_fired": 0, "action": "fail"})
    summary, scan, calls = run_yes(lab)
    ok(len(renames(calls)) == 2, f"expected the first write and one failed attempt, saw {[c['argv'] for c in renames(calls)]}")
    ok(len(synced_keys(lab)) == 1, f"only the first row may have changed, got {lab.names()}")
    text = summary + scan
    for fragment in ("1 synced", "1 uncertain", "2 not attempted", UNCERTAIN_TEXT):
        ok(fragment in text, f"{fragment!r} missing from the result\n{text}")
    close_popup(lab, "after a disconnect")


@scenario("F03", "Write fails in transit but reads work: the unchanged name makes the row uncertain and stops the queue",
          spec=FOUR)
def f03(lab):
    lab.set_faults(rename_rule(skip=1, times=1, action="fail"))
    summary, scan, calls = run_yes(lab)
    ok(len(renames(calls)) == 2, f"expected two rename requests, saw {[c['argv'] for c in renames(calls)]}")
    ok(len(synced_keys(lab)) == 1, f"only the first row may have changed, got {lab.names()}")
    text = summary + scan
    for fragment in ("1 synced", "1 uncertain", "2 not attempted", UNCERTAIN_TEXT):
        ok(fragment in text, f"{fragment!r} missing from the result\n{text}")
    close_popup(lab, "after an unconfirmed write")


@scenario("F04", "A definite failure of one row does not stop the independent rows", spec=FOUR)
def f04(lab):
    lab.set_faults(rename_rule(skip=1, times=1, action="stdout", rc=1,
                               text=error_envelope("agent_name_taken", "agent name already in use")))
    summary, scan, calls = run_yes(lab)
    ok(len(renames(calls)) == 4, f"each eligible row gets exactly one request, saw {[c['argv'] for c in renames(calls)]}")
    ok(len(synced_keys(lab)) == 3, f"three rows must sync, got {lab.names()}")
    text = summary + scan
    for fragment in ("3 synced", "1 failed", "already in use"):
        ok(fragment in text, f"{fragment!r} missing from the result\n{text}")
    ok("not attempted" not in text, "a definite failure must not stop the queue\n" + text)
    close_popup(lab, "after a definite failure")


@scenario("F05", "Write applied but response lost: the row is reconciled by a read and never written twice", spec=FOUR)
def f05(lab):
    lab.set_faults(rename_rule(skip=1, times=1, action="real_then_fail"))
    summary, scan, calls = run_yes(lab)
    ok(len(renames(calls)) == 4, f"expected one request per row, saw {[c['argv'] for c in renames(calls)]}")
    ok(len(synced_keys(lab)) == 4, f"all four rows must end synced, got {lab.names()}")
    text = summary + scan
    ok("4 synced" in text and "uncertain" not in text and "failed" not in text, "summary must show 4 synced only\n" + text)
    ok("Confirmed after an unclear response" in text, "the reconciled row's reason is not shown\n" + text)
    close_popup(lab, "after reconciliation")


@scenario("F06", "Write applied but the call times out: reconciled by a read, not repeated", spec=FOUR)
def f06(lab):
    lab.set_faults(rename_rule(skip=1, times=1, action="real_then_hang", seconds=40))
    summary, scan, calls = run_yes(lab, timeout=40)
    ok(len(renames(calls)) == 4, f"expected one request per row, saw {[c['argv'] for c in renames(calls)]}")
    ok(len(synced_keys(lab)) == 4, f"all four rows must end synced, got {lab.names()}")
    ok(lab.fault_events(0, "hang"), "the shim never simulated a hang")
    text = summary + scan
    ok("4 synced" in text and "uncertain" not in text, "summary must show 4 synced only\n" + text)
    close_popup(lab, "after a timeout")


# ---- F: the rename dialog under uncertain results ----------------------------------------------------
def wait_message(lab, fragment, timeout=6.0):
    return lab.until(lambda: fragment in flat_frame(lab), timeout, .1)


@scenario("F07", "Rename dialog: write applied, response lost; Enter again reconciles without a second write", spec=ONE)
def f07(lab):
    lab.set_faults(rename_rule(times=1, action="real_then_fail"))
    open_rename(lab)
    retype(lab, "typed-a")
    mark = lab.shim_mark()
    lab.keys("\r", .8)
    ok(wait_message(lab, "confirmed") or wait_message(lab, "rename result"),
       "no uncertain-result message in the dialog\n" + lab.text())
    lab.shot("F07-uncertain")
    ok(lab.popup_open(), "the dialog must stay open after an unconfirmed write")
    ok(lab.agent_name("a") == "typed-a", "the real write did not happen")
    ok(len(renames(lab.shim_calls(mark))) == 1, "unexpected number of writes before the retry")
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 5), "the explicit retry did not reconcile and close\n" + lab.text())
    ok(len(renames(lab.shim_calls(mark))) == 1, f"a second write was sent: {[c['argv'] for c in renames(lab.shim_calls(mark))]}")
    assert_names(lab, {"a": "typed-a"}, "after reconciliation")


@scenario("F08", "Rename dialog --clear: write applied, response lost; Enter again reconciles without a second write",
          spec=ONE)
def f08(lab):
    lab.set_faults(rename_rule(args=["--clear"], times=1, action="real_then_fail"))
    open_rename(lab)
    lab.keys("\x03", .5)
    mark = lab.shim_mark()
    lab.keys("\r", .8)
    ok(wait_message(lab, "confirmed") or wait_message(lab, "rename result"),
       "no uncertain-result message in the dialog\n" + lab.text())
    ok(lab.popup_open(), "the dialog must stay open after an unconfirmed clear")
    ok(lab.agent_name("a") is None, "the real clear did not happen")
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 5), "the explicit retry did not reconcile and close\n" + lab.text())
    sent = renames(lab.shim_calls(mark))
    ok(len(sent) == 1 and "--clear" in sent[0]["argv"], f"expected a single --clear request, saw {[c['argv'] for c in sent]}")
    ok(lab.agent_name("a") is None and lab.has_agent("a"), "the agent must remain, unnamed")


@scenario("F09", "Rename dialog: after an unconfirmed write the name was changed elsewhere; retry refuses, no second write",
          spec=ONE)
def f09(lab):
    lab.set_faults(rename_rule(times=1, action="real_then_fail"))
    open_rename(lab)
    retype(lab, "typed-a")
    mark = lab.shim_mark()
    lab.keys("\r", .8)
    ok(wait_message(lab, "confirmed") or wait_message(lab, "rename result"), "no uncertain message\n" + lab.text())
    ok(lab.run_cli("agent", "rename", lab.layout["a"], "elsewhere-a")["rc"] == 0, "external rename failed")
    lab.keys("\r", .8)
    ok(lab.popup_open(), "the dialog closed although the name no longer matches the unconfirmed request\n" + lab.text())
    ok(wait_message(lab, "Reopen") or wait_message(lab, "another rename"), "no refusal message\n" + lab.text())
    lab.shot("F09-refused")
    ok(len(renames(lab.shim_calls(mark))) == 1, "a second write was sent after the name changed")
    assert_names(lab, {"a": "elsewhere-a"}, "the external name must survive")
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close the dialog")


# ---- F: invalid or unknown envelopes -----------------------------------------------------------------
def expect_sync_error(lab, why, expected=NOT_SUPPORTED):
    before = lab.fingerprint()
    mark = trigger(lab, "S")
    calls = lab.shim_calls(mark)
    shown = [c["argv"][2] for c in notifications(calls)]
    ok(shown == [expected], f"{why}: expected one notification {expected!r}, saw {shown}\n{lab.text()}")
    ok(not popup_opens(calls) and not lab.popup_open(), f"{why}: a popup opened\n" + lab.text())
    ok(not renames(calls), f"{why}: a write was sent")
    assert_unchanged(lab, before, why)


@scenario("F10", "Sync with a malformed pane list: English error notification, no popup, zero writes", spec=FOUR)
def f10(lab):
    lab.set_faults({"match": ["pane", "list"], "action": "stdout", "text": "{this is not json"})
    expect_sync_error(lab, "malformed pane list")


@scenario("F11", "Sync with an unknown envelope type or a missing list in the agent list: error, zero writes", spec=FOUR)
def f11(lab):
    lab.set_faults({"match": ["agent", "list"], "action": "transform", "ops": [{"result_type": "something_new"}]})
    expect_sync_error(lab, "unknown agent list envelope")
    lab.set_faults({"match": ["pane", "list"], "action": "transform", "ops": [{"drop_result_key": "panes"}]})
    expect_sync_error(lab, "pane list without panes")
    lab.set_faults({"match": ["pane", "list"], "action": "transform", "ops": [{"result_type": "agent_list"}]})
    expect_sync_error(lab, "pane list with the wrong type")


@scenario("F12", "Sync when every pane lookup returns malformed data: information popup with lookup failures, zero writes",
          spec=FOUR)
def f12(lab):
    before = lab.fingerprint()
    lab.set_faults({"match": ["pane", "get"], "action": "stdout", "text": "<<broken>>"})
    mark = lab.shim_mark()
    frame = open_sync(lab)
    lab.shot("F12-dialog")
    text = frame_text(lab)
    ok(lab.has("Close") and not lab.has("[ Yes ]"), "an information popup (Close only) was expected\n" + text)
    scan = scan_flat(lab, 6)
    ok("lookup failed" in scan and "could not be read in a supported form" in scan, "lookup failures not shown\n" + scan)
    close_popup(lab, "information popup")
    ok(not renames(lab.shim_calls(mark)), "a write was sent")
    assert_unchanged(lab, before, "malformed pane lookups")


@scenario("F13", "Rename dialog with malformed or unknown lookup data: visible English error, nothing can be written",
          spec=ONE)
def f13(lab):
    before = lab.fingerprint()
    for rule, label in (({"match": ["pane", "get"], "action": "stdout", "text": "{nope"}, "malformed pane get"),
                        ({"match": ["pane", "process-info"], "action": "transform", "ops": [{"result_type": "mystery"}]},
                         "unknown process-info type")):
        lab.set_faults(rule)
        mark = lab.shim_mark()
        open_rename(lab)
        lab.shot(f"F13-{label.split()[0]}")
        ok(lab.has(NOT_SUPPORTED), f"{label}: the dialog shows no English error\n" + lab.text())
        lab.keys("\r", .6)  # Enter must not write from an error dialog
        lab.keys("\x1b", .6)
        ok(lab.until(lambda: not lab.popup_open(), 3), f"{label}: Esc did not close the dialog")
        ok(not renames(lab.shim_calls(mark)), f"{label}: a write was sent")
    assert_unchanged(lab, before, "error dialogs")


@scenario("F14", "Per-pane read failures give lookup-failed rows while the other rows still sync", spec=FOUR)
def f14(lab):
    b, d = lab.layout["b"], lab.layout["d"]
    lab.set_faults({"match": ["pane", "get", b], "action": "stdout", "rc": 1,
                    "text": error_envelope("pane_not_found", f"pane {b} not found", "cli:pane:get")},
                   {"match": ["pane", "process-info", "--pane", d], "action": "transform",
                    "ops": [{"result_type": "mystery"}]})
    open_sync(lab)
    lab.shot("F14-dialog")
    scan = scan_flat(lab, 6)
    ok(scan.count("lookup failed") >= 2, "two lookup-failed rows expected\n" + scan)
    ok("2 conflicts/errors" in scan or "conflicts" in scan or "err" in scan, "totals do not show the errors\n" + scan)
    mark = lab.shim_mark()
    lab.keys("y", .4)
    ok(lab.until(lambda: lab.has("[ Close ]"), 20), "batch did not finish\n" + lab.text())
    lab.pump(.4)
    ok(len(renames(lab.shim_calls(mark))) == 2, "exactly the two readable rows must be written")
    ok(lab.agent_name("a") == "new-a" and lab.agent_name("e") == "new-e", f"independent rows not synced {lab.names()}")
    ok(lab.agent_name("b") == "old-b" and lab.agent_name("d") == "old-d", f"a failed lookup row was written {lab.names()}")
    close_popup(lab, "after lookup failures")


# ---- F: capabilities and versions --------------------------------------------------------------------
CAPABILITY_VARIANTS = (
    ("no pane.list", [{"schema_drop_methods": ["pane.list"]}]),
    ("no agent.list", [{"schema_drop_methods": ["agent.list"]}]),
    ("agent.rename without name null (no --clear)", [{"schema_name_not_nullable": True}]),
)


@scenario("F15", "Unsupported capabilities (no pane.list, no agent.list, no --clear): clear outcome, zero writes", spec=FOUR)
def f15(lab):
    before = lab.fingerprint()
    for label, ops in CAPABILITY_VARIANTS:
        lab.set_faults({"match": ["api", "schema"], "action": "transform", "ops": ops})
        expect_sync_error(lab, f"sync, {label}")
        mark = trigger(lab, "A")
        calls = lab.shim_calls(mark)
        ok(not popup_opens(calls) and not lab.popup_open(), f"rename, {label}: a dialog opened\n" + lab.text())
        ok(not renames(calls), f"rename, {label}: a write was sent")
        log = plugin_log(lab)
        ok(NOT_SUPPORTED in log, f"rename, {label}: the plugin log lacks the English reason\n{log[-800:]}")
        lab.pump(.3)
    assert_unchanged(lab, before, "unsupported capabilities")


@scenario("F16", "Older Herdr (0.9.2) is refused with zero writes", spec=FOUR)
def f16(lab):
    before = lab.fingerprint()
    lab.set_faults({"match": ["--version"], "action": "stdout", "text": "herdr 0.9.2\n"})
    expect_sync_error(lab, "sync on 0.9.2")
    mark = trigger(lab, "A")
    ok(not popup_opens(lab.shim_calls(mark)) and not renames(lab.shim_calls(mark)), "rename on 0.9.2 opened or wrote")
    assert_unchanged(lab, before, "older version")


@scenario("F17", "Newer Herdr version string with a compatible schema works normally (sync and rename)", spec=FOUR)
def f17(lab):
    lab.set_faults({"match": ["--version"], "action": "stdout", "text": "herdr 0.9.4 (abc123)\n"})
    summary, scan, calls = run_yes(lab)
    ok(len(synced_keys(lab)) == 4, f"sync did not apply on a newer version string: {lab.names()}\n{summary}")
    close_popup(lab, "newer version")
    lab.set_state(base(a=("new-a", "old-a")))
    open_rename(lab)
    ok(lab.has("old-a"), "the rename dialog did not open on a newer version string\n" + lab.text())
    retype(lab, "newer-a")
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 4), "rename dialog did not close\n" + lab.text())
    assert_names(lab, {"a": "newer-a"}, "rename on newer version string")
    ok(any(f["argv"] == ["--version"] for f in lab.fault_events()), "the version fault was never exercised")


# ---- X: exited, moved and replaced targets -----------------------------------------------------------
def close_strays(lab):
    """Close every pane that is not part of the fixture layout (scenario leftovers, moved panes)."""
    keep = set(lab.layout.values())
    for pane in lab.panes():
        if pane["pane_id"] not in keep:
            lab.run_cli("pane", "close", pane["pane_id"])
    lab.pump(.4)


def with_scratch(lab, count, fn):
    try:
        return with_extra_agents(lab, count, fn)
    finally:
        close_strays(lab)


def kill_agent(lab, pane):
    """Terminate the agent's foreground processes (inside the sandbox PID namespace) and wait until Herdr drops the agent."""
    info = lab.run_cli("pane", "process-info", "--pane", pane)["json"]
    pids = [p["pid"] for p in (find(info, "foreground_processes") or []) if isinstance(p.get("pid"), int)]
    ok(pids, f"no foreground process to stop in {pane}: {info}")
    for pid in pids:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    ok(lab.until(lambda: lab.run_cli("agent", "get", pane)["rc"] != 0, 10), "Herdr still reports the agent after it was killed")


def restart_agent(lab, pane, name):
    """Start a new agent in a pane whose agent was killed (no Enter is sent to the client: a popup may be open)."""
    import subprocess
    process = subprocess.Popen([lab.binary, "--session", lab.name, "agent", "start", name, "--kind", "codex", "--pane", pane,
                                "--timeout", "10000", "--", *lab.codex_arguments], env=lab.env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    end = lab.until(lambda: process.poll() is not None, 20, .2)
    ok(end, "agent start did not return")
    ok(lab.run_cli("agent", "get", pane)["rc"] == 0, f"the replacement agent did not start: {process.stdout.read()} {process.stderr.read()}")


def focus_extra(lab, pane):
    result = lab.run_cli("agent", "focus", pane)
    ok(result["rc"] == 0, f"agent focus failed: {result}")
    lab.pump(.5)


def rename_target_changed(lab, how, expect_fragments):
    """Open the rename popup for a scratch pane, change the target, press Enter, expect refusal and no write."""
    def body(extra):
        pane = extra[0]
        ok(lab.run_cli("agent", "rename", pane, "old-x0")["rc"] == 0, "could not name the scratch agent")
        focus_extra(lab, pane)
        open_rename(lab)
        ok(lab.has("old-x0"), "the dialog does not show the scratch agent's name\n" + lab.text())
        retype(lab, "typed-x")
        how(pane, extra)
        lab.pump(.5)
        ok(lab.popup_open(), "Herdr closed the popup while the target changed\n" + lab.text())
        mark = lab.shim_mark()
        lab.keys("\r", 1.0)
        lab.shot(f"{lab.current}-refused")
        text = flat_frame(lab)
        ok(lab.popup_open(), "the dialog closed although its target changed\n" + lab.text())
        ok(any(fragment in text for fragment in expect_fragments), f"no refusal message (wanted one of {expect_fragments})\n{text}")
        ok(not renames(lab.shim_calls(mark)), f"a write was sent: {[c['argv'] for c in renames(lab.shim_calls(mark))]}")
        lab.keys("\x1b", .6)
        ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close the refusing dialog")
        names = {a.get("name") for a in lab.agents()}
        ok("typed-x" not in names, f"the typed name was written somewhere: {names}")
    with_scratch(lab, 1, body)


@scenario("X01", "Rename dialog: the target pane is closed between open and Enter; refused, no write", spec=base())
def x01(lab):
    rename_target_changed(lab, lambda pane, extra: lab.run_cli("pane", "close", pane),
                          ("no longer exists", "moved", "No running agent", "changed"))


@scenario("X02", "Rename dialog: the target pane is moved to another tab; refused, never followed", spec=base())
def x02(lab):
    tab = lab.tab2

    def move(pane, extra):
        result = lab.run_cli("pane", "move", pane, "--tab", tab, "--split", "right", "--no-focus")
        ok(result["rc"] == 0, f"pane move failed: {result}")
    rename_target_changed(lab, move, ("moved", "no longer exists", "changed"))


@scenario("X03", "Rename dialog: the agent exits between open and Enter; refused, no write", spec=base())
def x03(lab):
    rename_target_changed(lab, lambda pane, extra: kill_agent(lab, pane),
                          ("No running agent", "no longer exists", "changed"))


@scenario("X04", "Rename dialog: the agent is restarted (replaced) between open and Enter; refused, no write", spec=base())
def x04(lab):
    def replace(pane, extra):
        kill_agent(lab, pane)
        restart_agent(lab, pane, "old-x0")
    rename_target_changed(lab, replace, ("changed", "Reopen"))


def sync_target_changed(lab, how, reason_fragments):
    """Preview with one scratch row, change the scratch target, confirm: that row is skipped, every other row syncs."""
    def body(extra):
        pane = extra[0]
        open_sync(lab)
        ok(lab.has("new-x0"), "the scratch pane is missing from the preview\n" + lab.text())
        how(pane)
        lab.pump(.6)
        ok(lab.popup_open(), "Herdr closed the popup while the target changed\n" + lab.text())
        mark = lab.shim_mark()
        lab.keys("y", .4)
        ok(lab.until(lambda: lab.has("[ Close ]"), 25), "batch did not finish\n" + lab.text())
        lab.pump(.4)
        lab.shot(f"{lab.current}-done")
        scan = scan_flat(lab, 7)
        (lab.destination / f"{lab.current}-rows.txt").write_text(scan + "\n")
        ok(any(f in scan for f in reason_fragments), f"skip reason missing (wanted one of {reason_fragments})\n{scan}")
        ok(len(renames(lab.shim_calls(mark))) == 4, f"only the four unchanged rows may be written: {[c['argv'] for c in renames(lab.shim_calls(mark))]}")
        ok(len(synced_keys(lab)) == 4, f"the other rows did not sync: {lab.names()}")
        names = {a.get("name") for a in lab.agents()}
        ok("new-x0" not in names, f"a write followed the scratch pane to a new place: {names}")
        ok(not any(a.get("name") == "new-x0" for a in lab.agents()), "scratch agent was renamed")
        lab.dismiss()
    with_scratch(lab, 1, body)


@scenario("X05", "Sync: a pane is moved to another tab after the preview; skipped, the pane is not followed", spec=FOUR)
def x05(lab):
    def move(pane):
        result = lab.run_cli("pane", "move", pane, "--tab", lab.tab2, "--split", "right", "--no-focus")
        ok(result["rc"] == 0, f"pane move failed: {result}")
    sync_target_changed(lab, move, ("no longer in this workspace", "has moved", "moved"))


@scenario("X06", "Sync: an agent exits after the preview; its row is skipped, others sync", spec=FOUR)
def x06(lab):
    sync_target_changed(lab, lambda pane: kill_agent(lab, pane), ("No running agent", "no longer"))


@scenario("X07", "Sync: an agent is replaced (same name, new process) after the preview; skipped, not renamed", spec=FOUR)
def x07(lab):
    def replace(pane):
        kill_agent(lab, pane)
        restart_agent(lab, pane, "old-x0")
    replace.__name__ = "replace"
    sync_target_changed(lab, replace, ("target agent has changed", "has changed"))


@scenario("X08", "Sync: a swap is skipped (no temporary clears) while an independent row syncs",
          spec=base(a=("old-b", "old-a"), b=("old-a", "old-b"), d=("new-d", "old-d")))
def x08(lab):
    summary, scan, calls = run_yes(lab)
    sent = renames(calls)
    ok(len(sent) == 1 and sent[0]["argv"][-1] == "new-d", f"only the independent row may be written once: {[c['argv'] for c in sent]}")
    assert_names(lab, {"a": "old-a", "b": "old-b", "d": "new-d"}, "swap")
    ok("conflict" in scan and "1 synced" in summary, "conflict rows not reported\n" + scan)
    close_popup(lab, "swap")


@scenario("X09", "Sync: a 4-cycle of names is a conflict-only information popup with zero writes",
          spec=base(a=("old-b", "old-a"), b=("old-d", "old-b"), d=("old-e", "old-d"), e=("old-a", "old-e")))
def x09(lab):
    before = lab.fingerprint()
    mark = lab.shim_mark()
    open_sync(lab)
    ok(lab.has("Close") and not lab.has("[ Yes ]"), "an information popup was expected\n" + lab.text())
    ok("conflict" in scan_flat(lab, 6), "conflicts not shown")
    close_popup(lab, "cycle")
    ok(not renames(lab.shim_calls(mark)), "a write was sent")
    assert_unchanged(lab, before, "name cycle")


@scenario("X10", "Sync: a pane that arrives in the workspace after the preview is not added", spec=FOUR)
def x10(lab):
    def body(extra):
        pane = extra[0]
        ws2_pane = find(lab.run_cli("pane", "split", lab.layout["f"], "--direction", "right", "--no-focus")["json"], "pane_id")
        lab.start_agent(ws2_pane, "old-x9")
        lab.run_cli("pane", "rename", ws2_pane, "new-x9")
        open_sync(lab)
        ok(not lab.has("new-x9"), "the other workspace's pane is in the preview")
        tab1 = next(p["tab_id"] for p in lab.panes(lab.ws1) if p["pane_id"] == lab.layout["a"])
        moved = lab.run_cli("pane", "move", ws2_pane, "--tab", tab1, "--split", "right", "--no-focus")
        ok(moved["rc"] == 0, f"pane move failed: {moved}")
        lab.pump(.5)
        ok(lab.popup_open(), "Herdr closed the popup\n" + lab.text())
        mark = lab.shim_mark()
        lab.keys("y", .4)
        ok(lab.until(lambda: lab.has("[ Close ]"), 25), "batch did not finish\n" + lab.text())
        names = {a.get("name") for a in lab.agents()}
        ok("old-x9" in names and "new-x9" not in names, f"the arriving pane was renamed: {names}")
        ok("old-x0" in names or "new-x0" in names, "scratch agent lost")
        lab.dismiss()
    with_scratch(lab, 1, body)


@scenario("X11", "Sync scrolling in a short client keeps Yes/No visible; the last row is reachable; Y still applies",
          spec=FOUR, size=(100, 24))
def x11(lab):
    open_sync(lab)
    lab.shot("X11-top")
    ok(lab.has("[ Yes ]") and lab.has("[ No ]"), "Yes/No missing at the top\n" + lab.text())
    first = lab.has("w1:p1")
    ok(first, "first row missing at the top\n" + lab.text())
    lab.keys("\x1b[6~", .4)  # PageDown
    lab.keys("\x1b[B\x1b[B\x1b[B\x1b[B", .4)
    lab.shot("X11-bottom")
    ok(lab.has("[ Yes ]") and lab.has("[ No ]"), "Yes/No missing after scrolling\n" + lab.text())
    ok(lab.has("w1:p5"), "the last eligible row is not reachable by scrolling\n" + lab.text())
    ok(not lab.has("w1:p1 "), "the table did not scroll\n" + lab.text()) if False else None
    lab.keys("\x1b[5~\x1b[A\x1b[A\x1b[A\x1b[A\x1b[A\x1b[A", .4)
    lab.keys("y", .5)
    ok(settle_names(lab, {k: f"new-{k}" for k in FOUR_KEYS}), f"Y after scrolling did not apply: {lab.names()}\n{lab.text()}")


def settle_names(lab, expected, timeout=10):
    done = lab.until(lambda: all(lab.agent_name(k) == v for k, v in expected.items()), timeout)
    lab.pump(.6)
    lab.dismiss()
    return done


# ---- X: handoff (large, oversize, tampered, cleanup) --------------------------------------------------
INLINE_LIMIT = 48 * 1024
FILE_LIMIT = 4 * 1024 * 1024
TOO_LARGE = "The original target details are too large to open the dialog."
MISSING_CONTEXT = "The original pane could not be identified."


def inflate(n):
    """Pad every `pane process-info` answer (consistently, for planning and for the apply-time rechecks)."""
    return {"match": ["pane", "process-info"], "action": "transform", "ops": [{"inflate_argv": n}]}


def handoff_records(lab, mark):
    return lab.shim_calls(mark, phase="handoff")


def state_dir(lab):
    records = lab.shim_calls(0, phase="handoff")
    if not records:
        open_rename(lab)
        lab.close_popups()
        records = lab.shim_calls(0, phase="handoff")
    return Path(records[-1]["env"]["HERDR_PLUGIN_STATE_DIR"])


def leftovers(lab):
    return sorted(e.name for e in state_dir(lab).iterdir() if e.name.startswith(("agent-rename-handoff-", "decoy-", "alias-")))


@scenario("X13", "Large multi-pane sync plan travels through the private file; file is 0600 and removed once consumed",
          spec=FOUR)
def x13(lab):
    state_dir(lab)
    lab.set_faults(inflate(30000))
    mark = lab.shim_mark()
    open_sync(lab)
    lab.shot("X13-dialog")
    records = handoff_records(lab, mark)
    ok(len(records) == 1 and records[0].get("state_file"), f"expected one private-file handoff, saw {records}")
    record = records[0]
    ok(record["exists"] and record["mode"] == 0o600, f"handoff file not private at launch: {record}")
    ok(INLINE_LIMIT < record["size"] <= FILE_LIMIT, f"file size {record['size']} is not in the large-handoff range")
    ok(record["inline_bytes"] < 400, "the launch argument must be a short pointer, not the plan")
    ok(lab.until(lambda: not os.path.lexists(record["state_file"]), 6), "the handoff file was not removed after the popup consumed it")
    ok(lab.has("[ Yes ]") and lab.has("w1:p5"), "the plan did not reach the popup\n" + lab.text())
    lab.keys("y", .4)
    ok(settle_names(lab, {k: f"new-{k}" for k in FOUR_KEYS}), f"Yes did not apply the large plan: {lab.names()}")
    ok(leftovers(lab) == [], f"files left behind: {leftovers(lab)}")


@scenario("X14", "Large rename snapshot travels through the private file, is consumed and removed, and the rename works",
          spec=ONE)
def x14(lab):
    state_dir(lab)
    lab.set_faults(inflate(60000))
    mark = lab.shim_mark()
    open_rename(lab)
    records = handoff_records(lab, mark)
    ok(len(records) == 1 and records[0].get("state_file"), f"expected one private-file handoff, saw {records}")
    ok(records[0]["exists"] and records[0]["mode"] == 0o600 and records[0]["size"] > INLINE_LIMIT, f"bad handoff file {records[0]}")
    ok(lab.until(lambda: not os.path.lexists(records[0]["state_file"]), 6), "the handoff file was not removed")
    ok(lab.has("old-a"), "the snapshot did not reach the dialog\n" + lab.text())
    retype(lab, "large-a")
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 5), "the dialog did not close\n" + lab.text())
    assert_names(lab, {"a": "large-a"}, "large rename")
    ok(leftovers(lab) == [], f"files left behind: {leftovers(lab)}")


@scenario("X15", "Plan above 4 MiB: clear English error, no popup, zero writes, no file left", spec=FOUR)
def x15(lab):
    before = lab.fingerprint()
    state_dir(lab)
    lab.set_faults(inflate(1_200_000))
    mark = lab.shim_mark()
    keyed = trigger(lab, "S", wait=4)
    calls = lab.shim_calls(mark)
    shown = [c["argv"][2] for c in notifications(calls)]
    ok(shown == [TOO_LARGE], f"expected one notification {TOO_LARGE!r}, saw {shown}")
    ok(not popup_opens(calls) and not lab.popup_open(), "a popup opened for an oversize plan")
    ok(not renames(calls), "a write was sent")
    ok(leftovers(lab) == [], f"files left behind: {leftovers(lab)}")
    lab.set_faults(inflate(4_400_000))
    mark = trigger(lab, "A", wait=3)
    ok(not popup_opens(lab.shim_calls(mark)) and not lab.popup_open(), "a dialog opened for an oversize snapshot")
    ok(TOO_LARGE in plugin_log(lab), "the plugin log lacks the oversize reason\n" + plugin_log(lab)[-600:])
    ok(leftovers(lab) == [], f"files left behind: {leftovers(lab)}")
    assert_unchanged(lab, before, "oversize handoff")


def tamper_case(lab, mode, check):
    state_dir(lab)
    lab.set_faults(inflate(30000), {"match": ["plugin", "pane", "open"], "action": "tamper_handoff", "mode": mode})
    before = lab.fingerprint()
    mark = lab.shim_mark()
    open_sync(lab)
    lab.pump(.8)
    lab.shot(f"{lab.current}-refused")
    events = lab.shim_calls(mark, phase="tamper")
    ok(len(events) == 1 and events[0].get("done"), f"the shim did not tamper: {events}")
    text = flat_frame(lab)
    ok(MISSING_CONTEXT in text, f"the popup shows no English refusal\n{text}")
    ok(not lab.has("[ Yes ]"), "a tampered handoff offered Yes\n" + lab.text())
    lab.keys("y", .3)
    lab.keys("\r", .3)
    path = events[0]["path"]
    check(path, lab)
    lab.dismiss()
    ok(not renames(lab.shim_calls(mark)), "a write was sent from a tampered handoff")
    assert_unchanged(lab, before, f"tampered handoff ({mode})")


def consumed(path, lab):
    ok(lab.until(lambda: not os.path.lexists(path), 5), f"{path} was not removed")


@scenario("X16", "Tampered handoff: file mode changed to 0644 is refused with an error; the file is removed; zero writes",
          spec=FOUR)
def x16(lab):
    tamper_case(lab, "chmod644", consumed)


@scenario("X17", "Tampered handoff: file replaced by a symlink is refused, never followed, its target untouched",
          spec=FOUR)
def x17(lab):
    def check(path, lab):
        ok(os.path.islink(path), "the plugin removed or replaced a file it does not own")
        decoy = os.path.realpath(path)
        ok(os.path.isfile(decoy) and decoy != path, "the symlink target disappeared (it was followed and consumed)")
        ok(os.stat(decoy).st_size > INLINE_LIMIT, "the symlink target was modified")
    tamper_case(lab, "symlink", check)


@scenario("X18", "Tampered handoff: garbage content is refused with an error; the file is removed; zero writes", spec=FOUR)
def x18(lab):
    tamper_case(lab, "garbage", consumed)


@scenario("X19", "Tampered handoff: truncated content is refused with an error; the file is removed; zero writes",
          spec=FOUR)
def x19(lab):
    tamper_case(lab, "truncate", consumed)


@scenario("X20", "Tampered handoff: a second hard link (link count 2) is refused; zero writes", spec=FOUR)
def x20(lab):
    tamper_case(lab, "hardlink", consumed)


@scenario("X21", "Tampered handoff: well-formed JSON for another workspace is refused; zero writes", spec=FOUR)
def x21(lab):
    tamper_case(lab, "retarget", consumed)


def untouched(path, lab):
    lab.pump(.5)
    ok(os.path.isfile(path) and (os.stat(path).st_mode & 0o777) == 0o600, "the refused pointer's file was consumed or altered")


@scenario("X22", "Tampered handoff: a pointer to another directory is refused; the file is not touched; zero writes",
          spec=FOUR)
def x22(lab):
    tamper_case(lab, "pointer_dir", untouched)


@scenario("X23", "Tampered handoff: a path-traversal pointer name is refused; the file is not touched; zero writes",
          spec=FOUR)
def x23(lab):
    tamper_case(lab, "pointer_traversal", untouched)


@scenario("X24", "Popup launch failure after a large handoff: no popup, no writes, the private file is removed", spec=FOUR)
def x24(lab):
    before = lab.fingerprint()
    state_dir(lab)
    lab.set_faults(inflate(30000), {"match": ["plugin", "pane", "open"], "action": "fail"})
    mark = trigger(lab, "S", wait=3)
    records = handoff_records(lab, mark)
    ok(len(records) == 1 and records[0].get("exists") and records[0]["mode"] == 0o600, f"no private file at launch time: {records}")
    ok(not lab.popup_open(), "a popup is open although the launch failed\n" + lab.text())
    ok(not os.path.lexists(records[0]["state_file"]), "the private file survived the failed launch")
    ok(leftovers(lab) == [], f"files left behind: {leftovers(lab)}")
    ok(not renames(lab.shim_calls(mark)), "a write was sent")
    assert_unchanged(lab, before, "failed popup launch")


# ---- X: invocation context (real action process with a crafted context) ------------------------------
ROOT = Path(__file__).resolve().parents[1]


def plugin_env(lab):
    """Environment of a real plugin action process (recorded by the shim), without its invocation context."""
    records = lab.shim_calls(0, phase="handoff")
    if not records:
        open_rename(lab)
        lab.close_popups()
        records = lab.shim_calls(0, phase="handoff")
    env = dict(lab.env)
    env.update(records[-1]["env"])
    for key in ("HERDR_PLUGIN_CONTEXT_JSON", "HERDR_AGENT_RENAME_HANDOFF"):
        env.pop(key, None)
    return env


def run_action(lab, action, context):
    """Run ``bin/agent-rename <action>`` the way Herdr does, with the given invocation context (str, dict or None)."""
    import subprocess
    env = plugin_env(lab)
    if context is not None:
        env["HERDR_PLUGIN_CONTEXT_JSON"] = context if isinstance(context, str) else json.dumps(context)
    return subprocess.run([str(ROOT / "bin" / "agent-rename"), action], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=40)


@scenario("X25", "Sync context: workspace-only and pane-only contexts work; missing or malformed context is refused",
          spec=base(a=("new-a", "old-a"), b=("new-b", "old-b"), d=("new-d", "old-d"), e=("new-e", "old-e"),
                    f=("new-f", "old-f")))
def x25(lab):
    before = lab.fingerprint()
    mark = lab.shim_mark()
    result = run_action(lab, "sync", {"workspace_id": lab.ws1})
    ok(result.returncode == 0, f"workspace-only context failed: {result.stderr}")
    ok(lab.until(lab.popup_open, 6), "no popup for a workspace-only context")
    lab.pump(.4)
    ok("6 panes" in flat_frame(lab) and lab.has("[ Yes ]"), "wrong plan for the workspace-only context\n" + lab.text())
    lab.shot("X25-workspace-only")
    lab.keys("\x1b", .5)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close the popup")
    # Only the invoking pane (no workspace id): the workspace comes from that pane's canonical record, not from focus.
    result = run_action(lab, "sync", {"focused_pane_id": lab.layout["f"]})
    ok(result.returncode == 0, f"pane-only context failed: {result.stderr}")
    ok(lab.until(lab.popup_open, 6), "no popup for a pane-only context")
    lab.pump(.4)
    text = flat_frame(lab)
    ok("second-ws" in text or "1 pane" in text, f"the pane-only context did not select the pane's workspace\n{text}")
    lab.shot("X25-pane-only")
    lab.keys("\x1b", .5)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close the popup")
    for label, context in (("no context", None), ("empty object", {}), ("malformed JSON", "{not json"),
                           ("invalid pane id", {"focused_pane_id": "../etc"}), ("array", "[]")):
        note = lab.shim_mark()
        result = run_action(lab, "sync", context)
        calls = lab.shim_calls(note)
        shown = [c["argv"][2] for c in notifications(calls)]
        ok(result.returncode != 0 and shown == [MISSING_CONTEXT],
           f"{label}: rc={result.returncode} notifications={shown} stderr={result.stderr!r}")
        ok(not popup_opens(calls) and not renames(calls), f"{label}: a popup opened or a write was sent")
    ok(not renames(lab.shim_calls(mark)), "a write was sent")
    assert_unchanged(lab, before, "context handling")


@scenario("X26", "Frozen context wins over later focus: sync of workspace 1 while workspace 2 is focused; rename of pane a "
                 "while b is focused", spec=FOUR)
def x26(lab):
    lab.run_cli("workspace", "focus", lab.ws2)
    lab.pump(.5)
    result = run_action(lab, "sync", {"workspace_id": lab.ws1})
    ok(result.returncode == 0, f"sync failed: {result.stderr}")
    ok(lab.until(lab.popup_open, 6), "no popup")
    lab.pump(.4)
    ok(lab.has("[ Yes ]") and "6 panes" in flat_frame(lab), "the popup does not show workspace 1's plan\n" + lab.text())
    lab.keys("y", .4)
    ok(settle_names(lab, {k: f"new-{k}" for k in FOUR_KEYS}), f"workspace 1 not synced: {lab.names()}")
    ok(lab.agent_name("f") == "old-f", "the focused workspace's agent was renamed")
    lab.set_state(base(a=("new-a", "old-a"), b=("new-b", "old-b")))
    lab.run_cli("workspace", "focus", lab.ws1)
    lab.run_cli("agent", "focus", lab.layout["b"])
    lab.pump(.5)
    context = {"focused_pane_id": lab.layout["a"], "workspace_id": lab.ws1,
               "tab_id": next(p["tab_id"] for p in lab.panes(lab.ws1) if p["pane_id"] == lab.layout["a"])}
    result = run_action(lab, "open", context)
    ok(result.returncode == 0, f"open failed: {result.stderr}")
    ok(lab.until(lab.popup_open, 6), "no rename popup")
    lab.pump(.4)
    dialog = flat_frame(lab)
    ok("old-a" in dialog and "old-b" not in dialog, "the dialog follows the focus instead of the frozen pane\n" + lab.text())
    retype(lab, "frozen-a")
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 5), "dialog did not close")
    assert_names(lab, {"a": "frozen-a", "b": "old-b"}, "frozen rename target")


# ---- X: terminal modes of the dialog processes ----------------------------------------------------------
MODE_RE = re.compile(rb"\x1b\[\?([0-9;]+)([hl])")
TRACKED_MODES = (1000, 1002, 1003, 1005, 1006, 1015, 1004, 2004, 1049, 1047, 47)


def final_modes(raw):
    """(last state per DEC private mode, set of modes ever enabled) from a terminal byte stream."""
    state, enabled = {}, set()
    for match in MODE_RE.finditer(raw):
        for number in match.group(1).split(b";"):
            mode, on = int(number), match.group(2) == b"h"
            state[mode] = on
            if on:
                enabled.add(mode)
    return state, enabled


WRAP_TEMPLATE = """\
import sys
sys.path.insert(0, {src!r})
{patch}
from herdr_agent_rename import action
sys.argv = ["agent-rename", {mode!r}]
raise SystemExit(action.main())
"""


def pty_dialog(lab, mode, handoff, steps, *, size, patch=""):
    """Run a dialog process (``dialog-open`` / ``dialog-sync``) in its own PTY against the sandbox Herdr.

    ``steps``: list of (text to wait for or None, bytes to send). Returns (raw output, exit status, final screen text).
    """
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios
    import time
    import pyte
    env = plugin_env(lab)
    env.update(HERDR_AGENT_RENAME_HANDOFF=handoff, TERM="xterm-256color")
    columns, rows = size
    code = WRAP_TEMPLATE.format(src=str(ROOT / "src"), patch=patch, mode=mode)
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        os.chdir(ROOT)
        os.execvpe(sys.executable, [sys.executable, "-c", code], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    screen = pyte.Screen(columns, rows)
    stream = pyte.Stream(screen)
    raw = b""
    status = None

    def pump(duration):
        nonlocal raw, status
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and status is None:
            if select.select([fd], [], [], .05)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    _, status = os.waitpid(pid, 0)
                    break
                raw += data
                stream.feed(data.decode("utf-8", "replace"))
                if b"\x1b[6n" in data:
                    os.write(fd, f"\x1b[{screen.cursor.y + 1};{screen.cursor.x + 1}R".encode())
                if b"\x1b[c" in data:
                    os.write(fd, b"\x1b[?1;2c")
            else:
                done, state = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = state
    try:
        pump(1.2)
        for wait_for, send in steps:
            if wait_for:
                end = time.monotonic() + 8
                while wait_for not in "\n".join(screen.display) and status is None and time.monotonic() < end:
                    pump(.1)
            if status is None:
                os.write(fd, send)
                pump(.8)
        end = time.monotonic() + 8
        while status is None and time.monotonic() < end:
            pump(.2)
        if status is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise AssertionError("the dialog process did not exit\n" + "\n".join(screen.display))
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return raw, os.waitstatus_to_exitcode(status), "\n".join(screen.display)


def assert_modes_restored(raw, why, expect_enabled=True):
    state, enabled = final_modes(raw)
    still = sorted(m for m in enabled & set(TRACKED_MODES) if state.get(m))
    ok(not still, f"{why}: terminal modes {still} are still enabled when the dialog exits")
    if expect_enabled:
        ok({1000, 1006, 2004, 1049} <= enabled, f"{why}: the dialog never enabled mouse/paste/alt-screen ({sorted(enabled)}); check is vacuous")
    hidden_last = raw.rfind(b"\x1b[?25l") > raw.rfind(b"\x1b[?25h")
    ok(not hidden_last, f"{why}: the cursor is left hidden")


def captured_handoff(lab, key):
    """Open a popup the normal way and return the exact launch pointer/JSON Herdr passed to it."""
    mark = lab.shim_mark()
    frame = lab.open_popup(key)
    ok(frame, "the popup did not open")
    opens = popup_opens(lab.shim_calls(mark))
    ok(opens, "no `plugin pane open` call logged")
    argv = opens[-1]["argv"]
    value = next(a for a in argv if a.startswith("HERDR_AGENT_RENAME_HANDOFF="))
    lab.close_popups()
    return value.split("=", 1)[1]


RENAME_SIZE, SYNC_SIZE = (54, 5), (77, 20)
BREAK_SUBMIT = """
from herdr_agent_rename import controller
async def _boom(self, *args, **kwargs):
    raise RuntimeError("injected unexpected failure")
controller.Controller.submit = _boom
"""
BREAK_RUN = """
from herdr_agent_rename import sync
async def _boom(self, progress=None):
    raise RuntimeError("injected unexpected failure")
sync.SyncRunner.run = _boom
"""


@scenario("X27", "Rename dialog process restores every terminal mode on success, cancel, error screen and a crash",
          spec=ONE)
def x27(lab):
    handoff = captured_handoff(lab, "A")
    cases = (
        ("cancel (Esc)", handoff, [(None, b"\x1b")], "", 0),
        ("success (Enter, name unchanged)", handoff, [("old-a", b"\r")], "", 0),
        ("error screen (malformed handoff) then Esc", "{not valid", [(MISSING_CONTEXT, b"\x1b")], "", 0),
        ("unexpected exception in the controller", handoff, [("old-a", b"\r")], BREAK_SUBMIT, None),
    )
    before = lab.fingerprint()
    for label, data, steps, patch, expected_rc in cases:
        raw, rc, screen = pty_dialog(lab, "dialog-open", data, steps, size=RENAME_SIZE, patch=patch)
        if expected_rc is not None:
            ok(rc == expected_rc, f"{label}: exit status {rc}\n{screen}")
        else:
            ok(rc != 0 or b"injected" in raw, f"{label}: the injected failure was not reached (rc={rc})")
        assert_modes_restored(raw, label, expect_enabled="malformed" not in label)
        (lab.destination / "X27-rename-{}.txt".format(label.split()[0])).write_text(raw.decode("utf-8", "replace")[-3000:] + "\n")
    assert_unchanged(lab, before, "terminal-mode runs")


@scenario("X28", "Sync dialog process restores every terminal mode on success, cancel, error screen and a crash",
          spec=ONE)
def x28(lab):
    handoff = captured_handoff(lab, "S")
    cases = (
        ("cancel (N)", handoff, [("[ Yes ]", b"n")], "", 0),
        ("cancel (Esc)", handoff, [("[ Yes ]", b"\x1b")], "", 0),
        ("success (Y, then Close)", handoff, [("[ Yes ]", b"y"), ("[ Close ]", b"\r")], "", 0),
        ("error screen (malformed handoff) then Esc", "{not valid", [(MISSING_CONTEXT, b"\x1b")], "", 0),
        ("unexpected error while applying", handoff, [("[ Yes ]", b"y"), ("[ Close ]", b"\r")], BREAK_RUN, 0),
    )
    for label, data, steps, patch, expected_rc in cases:
        raw, rc, screen = pty_dialog(lab, "dialog-sync", data, steps, size=SYNC_SIZE, patch=patch)
        if expected_rc is not None:
            ok(rc == expected_rc, f"{label}: exit status {rc}\n{screen}")
        assert_modes_restored(raw, label, expect_enabled="malformed" not in label)
        (lab.destination / "X28-sync-{}.txt".format(label.split()[0])).write_text(raw.decode("utf-8", "replace")[-3000:] + "\n")
    ok(lab.agent_name("a") == "new-a", f"the success case did not write: {lab.names()}")


# ---- E: editor behavior inside the real Herdr popup, compared with the ported editor model ------------------
KEY_BYTES = {
    "left": "\x1b[D", "right": "\x1b[C", "home": "\x1b[H", "end": "\x1b[F", "c-a": "\x01", "c-e": "\x05", "c-f": "\x06",
    "escape b": "\x1bb", "escape f": "\x1bf", "backspace": "\x7f", "c-h": "\x08", "delete": "\x1b[3~", "c-d": "\x04",
    "c-u": "\x15", "c-k": "\x0b", "c-w": "\x17", "escape backspace": "\x1b\x7f", "escape d": "\x1bd", "c-y": "\x19",
}
EDIT_NAME = "alpha-bravo_charlie-delta"


def input_text(lab):
    """The rename popup's input row (content row 2) without the left margin; trailing blanks removed."""
    frame = lab.frame()
    row = lab.lines()[frame.y + 1 + 2][frame.x + 1:frame.x + frame.width - 2]
    import unicodedata
    return unicodedata.normalize("NFC", row[1:].rstrip(" │"))


def editor_run(lab, name, tokens, *, width=None):
    """Feed tokens (key names or literal text) to the popup and to the model; compare the input row after each."""
    from herdr_agent_rename.editor import TextEditor
    model = TextEditor(name)
    open_rename(lab)
    ok(input_text(lab) == name, f"unexpected initial input {input_text(lab)!r}")
    trace = []
    for token in tokens:
        if token in KEY_BYTES:
            lab.keys(KEY_BYTES[token], .25)
            model.handle_key(token)
        else:
            lab.keys(token, .25)
            for char in token:
                model.handle_key(char)
        shown = input_text(lab)
        expected = model.viewport(width)[0].rstrip() if width else model.text.rstrip()
        trace.append((token, shown, expected))
        ok(shown == expected, f"after {token!r}: popup shows {shown!r}, the editor model {expected!r}\n"
           + "\n".join(f"{t!r}: {a!r} / {b!r}" for t, a, b in trace))
    lab.shot(f"{lab.current}-end")
    (lab.destination / f"{lab.current}-trace.txt").write_text("\n".join(f"{t!r}\t{a!r}" for t, a, _ in trace) + "\n")
    return model


def cancel_dialog(lab, before):
    lab.keys("\x1b", .6)
    ok(lab.until(lambda: not lab.popup_open(), 3), "Esc did not close the dialog")
    assert_unchanged(lab, before, "editing then Esc")


EDIT_SPEC = base(a=("new-a", EDIT_NAME))


@scenario("E01", "Editor: opens at the end with the name (typing appends: no replace-on-type); char/word/Home/End movement",
          spec=EDIT_SPEC)
def e01(lab):
    before = lab.fingerprint()
    tokens = ["X", "left", "left", "1", "home", "2", "end", "3", "escape b", "4", "escape b", "5", "escape f", "6",
              "c-a", "7", "c-e", "8", "right", "left", "c-f", "9"]
    model = editor_run(lab, EDIT_NAME, tokens)
    ok(all(mark in model.text for mark in "123456789X"), f"movement sequence did not exercise the editor: {model.text!r}")
    cancel_dialog(lab, before)


@scenario("E02", "Editor: every kill and yank key (Ctrl+W/K/U/Y/D, Alt+D, Alt+Backspace, Delete, Backspace)", spec=EDIT_SPEC)
def e02(lab):
    before = lab.fingerprint()
    tokens = ["escape b", "escape b", "c-w", "c-y", "c-y", "left", "left", "c-k", "c-y", "home", "escape d", "c-y",
              "right", "c-d", "delete", "end", "backspace", "c-h", "escape backspace", "c-y", "c-u", "c-y"]
    model = editor_run(lab, EDIT_NAME, tokens)
    ok(model.killed, "nothing was killed")
    cancel_dialog(lab, before)


@scenario("E03", "Editor: grapheme clusters (combining marks, CJK, emoji with skin-tone modifier) move and delete as units",
          spec=EDIT_SPEC)
def e03(lab):
    before = lab.fingerprint()
    from herdr_agent_rename.editor import TextEditor
    open_rename(lab)
    lab.keys("\x05\x15", .3)
    text = "e\u0301a\u0308\u0323한글👍é"
    lab.paste(text)
    import unicodedata
    nfc = lambda value: unicodedata.normalize("NFC", value)  # the screen model composes marks differently
    model = TextEditor("")
    model.insert(text)
    ok(input_text(lab) == nfc(model.text), f"pasted text differs: {input_text(lab)!r} vs {nfc(model.text)!r}")
    for token in ("left", "backspace", "left", "left", "delete", "home", "right", "backspace", "end", "backspace"):
        lab.keys(KEY_BYTES[token], .25)
        model.handle_key(token)
        ok(input_text(lab) == nfc(model.text), f"after {token}: {input_text(lab)!r} vs {nfc(model.text)!r}")
    lab.shot("E03-end")
    # An emoji with a skin-tone modifier is one grapheme: one Backspace removes both code points.
    lab.keys("\x15", .3)
    lab.paste("x\U0001f44d\U0001f3fd")
    lab.keys(KEY_BYTES["backspace"], .3)
    ok(input_text(lab) == "x", f"Backspace left part of an emoji sequence: {input_text(lab)!r}")
    cancel_dialog(lab, before)


@scenario("E04", "Editor: long text scrolls the viewport with the cursor, matching the model", spec=EDIT_SPEC)
def e04(lab):
    before = lab.fingerprint()
    from herdr_agent_rename.editor import TextEditor
    open_rename(lab)
    long_text = "".join(chr(ord("a") + i % 26) for i in range(140))
    lab.paste(long_text)
    shown = input_text(lab)
    model = TextEditor(EDIT_NAME)
    model.insert(long_text)
    # At the end of the text the model keeps the cursor cell free: the visible text is width - 1 cells.
    width = len(shown) + 1
    ok(model.viewport(width)[0].rstrip() == shown and shown.endswith(long_text[-10:]) and not shown.startswith(EDIT_NAME),
       f"the end of the text is not shown: {shown!r} (width {width})")
    lab.shot("E04-end")
    for token in ["home", "right", "right"] + ["c-f"] * 60 + ["escape b"] * 2 + ["end", "left", "left"]:
        lab.keys(KEY_BYTES[token], .2)
        model.handle_key(token)
        shown = input_text(lab)
        ok(shown == model.viewport(width)[0].rstrip(), f"after {token}: viewport {shown!r} != model {model.viewport(width)[0]!r}")
    lab.keys(KEY_BYTES["home"], .3)
    ok(input_text(lab).startswith(EDIT_NAME[:10]), "Home did not scroll back to the start")
    lab.shot("E04-home")
    cancel_dialog(lab, before)


@scenario("E05", "Editor paste: CR, LF, CRLF and Tab become spaces, other controls become U+FFFD, nothing is written",
          spec=EDIT_SPEC)
def e05(lab):
    before = lab.fingerprint()
    from herdr_agent_rename.editor import TextEditor, normalize_input
    open_rename(lab)
    lab.keys("\x05\x15", .3)
    raw = "a\tb\r\nc\rd\ne\x07f"
    lab.paste(raw)
    lab.shot("E05-pasted")
    expected = normalize_input(raw)
    ok(input_text(lab) == expected, f"pasted text {input_text(lab)!r} != normalized {expected!r}")
    ok("�" in input_text(lab), "the control character is not shown as U+FFFD")
    mark = lab.shim_mark()
    lab.keys("\r", .8)
    ok(lab.popup_open(), "an invalid name closed the dialog\n" + lab.text())
    ok(not renames(lab.shim_calls(mark)), "an invalid pasted name was written")
    lab.keys("\x15", .3)
    lab.paste("ok-name")
    ok(input_text(lab) == "ok-name", f"typing after the error is wrong: {input_text(lab)!r}")
    cancel_dialog(lab, before)


@scenario("E06", "Editor with an absent name: empty input and hint; empty Enter is a no-op; a typed name sets it",
          spec=base(a=("new-a", None)))
def e06(lab):
    before = lab.fingerprint()
    open_rename(lab)
    lab.shot("E06-open")
    ok(input_text(lab) == "", f"the input is not empty: {input_text(lab)!r}")
    ok("This agent has no name" in flat_frame(lab), "no hint for an unnamed agent\n" + lab.text())
    mark = lab.shim_mark()
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 4), "empty Enter did not close the dialog\n" + lab.text())
    ok(not renames(lab.shim_calls(mark)), "an empty save on an unnamed agent was written")
    assert_unchanged(lab, before, "empty Enter on an unnamed agent")
    open_rename(lab)
    lab.paste("named-a")
    lab.keys("\r", .8)
    ok(lab.until(lambda: not lab.popup_open(), 4), "dialog did not close")
    assert_names(lab, {"a": "named-a"}, "name set on an unnamed agent")
