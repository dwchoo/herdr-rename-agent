#!/usr/bin/env python3
"""Throwaway popup program for tools/probe_r1.py (never part of the plugin).

Enables SGR mouse reporting, switches the tty to raw mode, writes the PTY size
and every raw stdin byte (hex, with a monotonic timestamp) to the log file named
by MOUSEPROBE_LOG (set MOUSEPROBE_NOENABLE=1 to skip enabling mouse reporting), and exits when it reads the bytes b"QQQ".
"""
import fcntl
import os
import signal
import struct
import sys
import termios
import time
import tty

LOG = open(os.environ.get("MOUSEPROBE_LOG", "/tmp/mouseprobe.log"), "a", buffering=1)


def size() -> str:
    rows, cols = struct.unpack("HHHH", fcntl.ioctl(1, termios.TIOCGWINSZ, b"\0" * 8))[:2]
    return f"{rows}x{cols}"


def log(text: str) -> None:
    LOG.write(f"{time.monotonic():.4f} {text}\n")


tty.setraw(0)
sys.stdout.write("\x1b[2J\x1b[H@@ORIGIN@@\x1b[2;1H" + f"SIZE {size()}" + ("" if os.environ.get("MOUSEPROBE_NOENABLE") else "\x1b[?1000h\x1b[?1002h\x1b[?1006h"))
sys.stdout.flush()
log(f"START size={size()} isatty={os.isatty(0)} TERM={os.environ.get('TERM')}")
signal.signal(signal.SIGWINCH, lambda *_: log(f"WINCH size={size()}"))
seen = b""
while True:
    try:
        data = os.read(0, 4096)
    except InterruptedError:
        continue
    if not data:
        log("EOF")
        break
    log("BYTES " + data.hex() + " " + repr(data))
    seen = (seen + data)[-3:]
    if seen == b"QQQ":
        break
sys.stdout.write("\x1b[?1006l\x1b[?1002l\x1b[?1000l")
sys.stdout.flush()
