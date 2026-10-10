#!/usr/bin/env python3
"""Test-only Herdr CLI shim (sandbox E2E only; never installed on a real Herdr).

The sandbox harness starts its private Herdr server with ``HERDR_BIN_PATH``
pointing at this file, so the plugin's CLI calls pass through it:

* every invocation's argv is appended as one JSON line to ``$HERDR_SHIM_DIR/calls.jsonl``
  (``{"t": epoch seconds, "argv": [...], "phase": "start"|"end", "rc": n}``);
* it then runs the real binary (``$HERDR_SHIM_REAL``) with the same argv, stdio and environment
  and returns its exit status, so behavior is unchanged;
* only when ``$HERDR_SHIM_DIR/rename-delay-ms`` holds a positive number does it sleep that long
  before an ``agent rename`` call (the harness uses this to make a batch long enough to interrupt).

Fault injection (off unless ``$HERDR_SHIM_DIR/faults.json`` exists; the harness deletes it in ``Lab.reset``).
``faults.json`` is a JSON list of rules, evaluated in order, the first applicable rule wins:

    {"match": ["agent", "rename"],      # contiguous argv subsequence that must be present
     "args": ["w1:p3"],                 # optional: every element must appear in argv
     "skip": 0,                         # let this many matching calls through first
     "times": null,                     # fire at most this many times (null = always)
     "after_renames": 0,                # apply only after this many real `agent rename` calls ended
     "after_fired": 0,                  # apply only after the rule with this index has fired at least once
     "action": "...", ...}

Actions:  ``fail`` (no real call; exit 1, empty stdout: a transport failure), ``stdout`` (no real call; print
``text``, exit ``rc``, default 0), ``real_then_fail`` (the real call runs, then exit 1 with nothing printed: the
write happened but its result is lost), ``real_then_hang`` (real call runs, then sleep ``seconds`` so the caller's
timeout fires), ``transform`` (real call, JSON output rewritten by ``ops``, a list of the operations below),
``tamper_handoff`` (``plugin pane open`` only: damage the private handoff file named by the pointer in the
``--env HERDR_AGENT_RENAME_HANDOFF=`` argument, then run the real call).

Transform ops: ``{"result_type": X}``, ``{"drop_result_key": K}``, ``{"schema_drop_methods": [..]}``,
``{"schema_name_not_nullable": true}``, ``{"inflate_argv": N}`` (append an N-byte argument to every foreground
process of ``pane process-info``).

Every fired fault is logged as ``{"phase": "fault", ...}``; a handoff pointer in ``plugin pane open`` is logged as
``{"phase": "handoff", ...}`` with the state file's presence and mode, and its HERDR_* environment, for the harness.
"""
import fcntl
import json
import os
import stat
import subprocess
import sys
import time

SHIM_DIR = os.environ.get("HERDR_SHIM_DIR", "")
REAL = os.environ.get("HERDR_SHIM_REAL", "herdr")
HANDOFF_ENV = "HERDR_AGENT_RENAME_HANDOFF="


def log(**fields):
    if not SHIM_DIR:
        return
    line = json.dumps({"t": time.time(), "pid": os.getpid(), **fields}, ensure_ascii=False) + "\n"
    fd = os.open(os.path.join(SHIM_DIR, "calls.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode())  # one O_APPEND write per record keeps lines whole
    finally:
        os.close(fd)


def rename_delay() -> float:
    try:
        with open(os.path.join(SHIM_DIR, "rename-delay-ms")) as handle:
            return max(0.0, float(handle.read().strip())) / 1000
    except (OSError, ValueError):
        return 0.0


def is_rename(argv) -> bool:
    return any(argv[i:i + 2] == ["agent", "rename"] for i in range(len(argv)))


def contains(argv, sequence) -> bool:
    n = len(sequence)
    return any(argv[i:i + n] == sequence for i in range(len(argv) - n + 1))


class State:
    """Per-scenario fault counters, serialized with an flock (plugin processes may overlap)."""

    def __init__(self):
        self.path = os.path.join(SHIM_DIR, "fault-state.json")
        self.lock = os.open(os.path.join(SHIM_DIR, "fault-state.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        try:
            with open(self.path) as handle:
                self.data = json.load(handle)
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("seen", {})
        self.data.setdefault("fired", {})
        self.data.setdefault("renames", 0)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(self.data, handle)
        os.replace(tmp, self.path)

    def close(self):
        os.close(self.lock)


def load_rules():
    try:
        with open(os.path.join(SHIM_DIR, "faults.json")) as handle:
            rules = json.load(handle)
        return rules if isinstance(rules, list) else []
    except (OSError, ValueError):
        return []


def pick_rule(argv):
    rules = load_rules()
    if not rules:
        return None
    state = State()
    try:
        for index, rule in enumerate(rules):
            key = str(index)
            if not contains(argv, rule.get("match", [])) or any(a not in argv for a in rule.get("args", [])):
                continue
            if state.data["renames"] < rule.get("after_renames", 0):
                continue
            if "after_fired" in rule and state.data["fired"].get(str(rule["after_fired"]), 0) < 1:
                continue
            seen = state.data["seen"].get(key, 0)
            state.data["seen"][key] = seen + 1
            if seen < rule.get("skip", 0):
                continue
            fired = state.data["fired"].get(key, 0)
            if rule.get("times") is not None and fired >= rule["times"]:
                continue
            state.data["fired"][key] = fired + 1
            state.save()
            return index, rule
        state.save()
        return None
    finally:
        state.close()


def count_real_rename():
    state = State()
    try:
        state.data["renames"] += 1
        state.save()
    finally:
        state.close()


def walk_methods(node, drop):
    """Remove request alternatives whose ``method`` const/enum is in ``drop`` (in place)."""
    if isinstance(node, dict):
        for key in ("oneOf", "anyOf"):
            if isinstance(node.get(key), list):
                node[key] = [item for item in node[key] if not (
                    isinstance(item, dict) and isinstance(item.get("properties"), dict)
                    and (item["properties"].get("method") or {}).get("const") in drop)]
        for value in node.values():
            walk_methods(value, drop)
    elif isinstance(node, list):
        for value in node:
            walk_methods(value, drop)


def apply_ops(raw: dict, ops) -> dict:
    for op in ops:
        result = raw.get("result") if isinstance(raw.get("result"), dict) else None
        if "result_type" in op and result is not None:
            result["type"] = op["result_type"]
        if "drop_result_key" in op and result is not None:
            result.pop(op["drop_result_key"], None)
        if "schema_drop_methods" in op:
            walk_methods(raw.get("schemas", {}).get("request", {}), set(op["schema_drop_methods"]))
        if op.get("schema_name_not_nullable"):
            definition = raw["schemas"]["request"]["$defs"]["AgentRenameParams"]
            definition["properties"]["name"] = {"type": "string"}
        if "inflate_argv" in op and result is not None:
            info = result.get("process_info")
            for item in (info or {}).get("foreground_processes") or []:
                if isinstance(item.get("argv"), list):
                    item["argv"] = item["argv"] + ["x" * int(op["inflate_argv"])]
    return raw


def tamper(argv, mode):
    """Damage the private handoff file before the popup consumes it; returns what was done."""
    pointer = next((a[len(HANDOFF_ENV):] for a in argv if a.startswith(HANDOFF_ENV)), "")
    try:
        state_file = json.loads(pointer)["state_file"]
        path = os.path.join(state_file["directory"], state_file["name"])
    except (ValueError, KeyError, TypeError):
        return {"mode": mode, "done": False, "why": "no file pointer"}
    directory = os.path.dirname(path)
    with open(path, "rb") as handle:
        original = handle.read()
    if mode in ("pointer_dir", "pointer_traversal"):  # the argv itself is rewritten (see main)
        bad = dict(state_file)
        if mode == "pointer_dir":
            bad["directory"] = "/tmp"
        else:
            bad["name"] = "../" + state_file["name"]
        new_pointer = json.dumps({"version": 1, "state_file": bad}, separators=(",", ":"))
        return {"mode": mode, "done": True, "path": path,
                "argv": [HANDOFF_ENV + new_pointer if a.startswith(HANDOFF_ENV) else a for a in argv]}
    if mode == "chmod644":
        os.chmod(path, 0o644)
    elif mode == "symlink":  # a valid private copy sits behind the link, so following it would "work"
        decoy = os.path.join(directory, "decoy-" + state_file["name"])
        with open(decoy, "wb") as handle:
            handle.write(original)
        os.chmod(decoy, 0o600)
        os.unlink(path)
        os.symlink(decoy, path)
    elif mode == "garbage":
        with open(path, "wb") as handle:
            handle.write(b"\x00not json at all{{{" + os.urandom(16).hex().encode())
    elif mode == "truncate":
        with open(path, "wb") as handle:
            handle.write(original[: len(original) // 2])
    elif mode == "hardlink":
        os.link(path, os.path.join(directory, "alias-" + state_file["name"]))
    elif mode == "retarget":  # valid JSON of the right shape, but a different plan workspace
        text = original.decode().replace('"workspace_id":"w1"', '"workspace_id":"w9"', 1)
        with open(path, "wb") as handle:
            handle.write(text.encode())
    else:
        return {"mode": mode, "done": False, "why": "unknown mode"}
    return {"mode": mode, "done": True, "path": path}


def log_handoff(argv):
    pointer = next((a[len(HANDOFF_ENV):] for a in argv if a.startswith(HANDOFF_ENV)), None)
    if pointer is None:
        return
    record = {"inline_bytes": len(pointer.encode()), "env": {k: v for k, v in os.environ.items() if k.startswith("HERDR_")}}
    try:
        state_file = json.loads(pointer).get("state_file")
    except ValueError:
        state_file = None
    if isinstance(state_file, dict):
        path = os.path.join(state_file["directory"], state_file["name"])
        record["state_file"] = path
        try:
            info = os.lstat(path)
            record.update(exists=True, mode=stat.S_IMODE(info.st_mode), size=info.st_size)
        except OSError:
            record["exists"] = False
    log(phase="handoff", argv=argv[:6], **record)


def run_real(argv, capture):
    return subprocess.run([REAL, *argv], **({"capture_output": True, "text": True} if capture else {}))


def main() -> int:
    argv = sys.argv[1:]
    log(phase="start", argv=argv)
    if SHIM_DIR and contains(argv, ["plugin", "pane", "open"]):
        log_handoff(argv)
    picked = pick_rule(argv) if SHIM_DIR else None
    if picked:
        index, rule = picked
        action = rule["action"]
        log(phase="fault", argv=argv, rule=index, action=action)
        if action == "fail":
            print("shim: injected transport failure", file=sys.stderr)
            log(phase="end", argv=argv, rc=1)
            return 1
        if action == "stdout":
            sys.stdout.write(rule.get("text", ""))
            sys.stdout.flush()
            log(phase="end", argv=argv, rc=rule.get("rc", 0))
            return rule.get("rc", 0)
    is_ren = is_rename(argv)
    if is_ren and SHIM_DIR and (delay := rename_delay()):
        time.sleep(delay)
    if picked and rule["action"] == "tamper_handoff":
        done = tamper(argv, rule["mode"])
        argv = done.pop("argv", argv)
        log(phase="tamper", argv=argv[:6], **done)
        picked = None
    if picked and rule["action"] == "transform":
        result = run_real(argv, capture=True)
        out = result.stdout
        try:
            out = json.dumps(apply_ops(json.loads(result.stdout), rule["ops"]))
        except (ValueError, KeyError, TypeError):
            pass
        sys.stdout.write(out)
        sys.stdout.flush()
        sys.stderr.write(result.stderr)
        log(phase="end", argv=argv, rc=result.returncode)
        return result.returncode
    result = run_real(argv, capture=bool(picked))
    if is_ren and SHIM_DIR and result.returncode == 0:
        count_real_rename()
    if picked and rule["action"] == "real_then_fail":
        log(phase="end", argv=argv, rc=1, real_rc=result.returncode)
        return 1
    if picked and rule["action"] == "real_then_hang":
        log(phase="hang", argv=argv, real_rc=result.returncode)
        time.sleep(float(rule.get("seconds", 30)))
        return 1
    log(phase="end", argv=argv, rc=result.returncode)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
