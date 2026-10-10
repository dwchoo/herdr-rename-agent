#!/usr/bin/env python3
"""Test-only Herdr CLI shim (sandbox E2E only; never installed on a real Herdr).

The sandbox harness starts its private Herdr server with ``HERDR_BIN_PATH``
pointing at this file, so the plugin's CLI calls pass through it:

* every invocation's argv is appended as one JSON line to ``$HERDR_SHIM_DIR/calls.jsonl``
  (``{"t": monotonic-ish epoch seconds, "argv": [...], "phase": "start"|"end", "rc": n}``);
* it then runs the real binary (``$HERDR_SHIM_REAL``) with the same argv, stdio and environment
  and returns its exit status, so behavior is unchanged;
* only when ``$HERDR_SHIM_DIR/rename-delay-ms`` holds a positive number does it sleep that long
  before an ``agent rename`` call (the harness uses this to make a batch long enough to interrupt).
"""
import json
import os
import subprocess
import sys
import time

SHIM_DIR = os.environ.get("HERDR_SHIM_DIR", "")
REAL = os.environ.get("HERDR_SHIM_REAL", "herdr")


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


def main() -> int:
    argv = sys.argv[1:]
    log(phase="start", argv=argv)
    is_rename = any(argv[i:i + 2] == ["agent", "rename"] for i in range(len(argv)))
    if is_rename and SHIM_DIR and (delay := rename_delay()):
        time.sleep(delay)
    result = subprocess.run([REAL, *argv])
    log(phase="end", argv=argv, rc=result.returncode)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
