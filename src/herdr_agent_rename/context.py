"""Capture the invocation once; never infer it from popup focus."""

import json
import os
import re
import secrets
import stat
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable, Mapping

from .models import HerdrError, Invocation, Snapshot
from .sync import SyncPlan, plan_from_dict, plan_to_dict

HANDOFF_KEY = "HERDR_AGENT_RENAME_HANDOFF"
MISSING_CONTEXT = "The original pane could not be identified."
_PANE_ID = re.compile(r"w[0-9a-zA-Z_-]+:p[0-9a-zA-Z_-]+", re.ASCII)
MAX_INLINE_BYTES = 48 * 1024
# Sized for a multi-pane workspace plan: each eligible pane carries its own
# observations (including foreground process argv), so allow 4x the former bound.
MAX_FILE_BYTES = 4 * 1024 * 1024
_STATE_FILE = re.compile(r"agent-rename-handoff-[0-9a-f]{32}\.json", re.ASCII)
_STATE_UNAVAILABLE = "Could not open the dialog: Herdr plugin state is unavailable."
_STATE_FAILED = "Could not prepare the dialog's original target details. Try again."
_TOO_LARGE = "The original target details are too large to open the dialog."


def _json_text(raw: object) -> str:
    return json.dumps(raw, separators=(",", ":"), ensure_ascii=True)


def _compact_one(snapshot: dict) -> None:
    shared = {"pane_id", "terminal_id", "workspace_id", "tab_id", "agent", "agent_session"}
    for kind, fields in (("agent", shared | {"name"}), ("pane", shared | {"label"})):
        snapshot[kind] = {key: value for key, value in snapshot[kind].items() if key in fields}
    process = snapshot["process"]
    fields = {"pane_id", "shell_pid", "foreground_process_group_id", "tty", "foreground_processes"}
    snapshot["process"] = {key: value for key, value in process.items() if key in fields}
    processes = snapshot["process"].get("foreground_processes")
    if isinstance(processes, list):
        fields = {"pid", "name", "argv0", "argv", "cmdline"}
        snapshot["process"]["foreground_processes"] = [
            {key: value for key, value in item.items() if key in fields}
            if isinstance(item, dict) else item for item in processes
        ]


def _compact_snapshot(raw: dict) -> None:
    """Drop unused display metadata, retaining exact controller observations."""
    if raw.get("snapshot") is not None:
        _compact_one(raw["snapshot"])
    if raw.get("plan") is not None:
        for snapshot in raw["plan"]["snapshots"].values():
            _compact_one(snapshot)


def _state_directory(env: Mapping[str, str]) -> Path:
    value = env.get("HERDR_PLUGIN_STATE_DIR")
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("Missing plugin state directory")
    directory = Path(value).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Invalid plugin state directory")
    return directory


def _open_directory(directory: Path) -> int:
    return os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _private_file(info: os.stat_result) -> bool:
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)


def _file_pointer(raw: object, env: Mapping[str, str]) -> tuple[Path, str]:
    if not isinstance(raw, dict) or set(raw) != {"version", "state_file"} or type(raw["version"]) is not int or raw["version"] != 1:
        raise ValueError("Invalid file handoff")
    pointer = raw["state_file"]
    if not isinstance(pointer, dict) or set(pointer) != {"directory", "name"}:
        raise ValueError("Invalid file pointer")
    name = pointer["name"]
    if not isinstance(name, str) or not _STATE_FILE.fullmatch(name):
        raise ValueError("Invalid handoff filename")
    directory = _state_directory(env)
    if pointer["directory"] != str(directory):
        raise ValueError("Mismatched plugin state directory")
    return directory, name


def _store_handoff(text: str, env: Mapping[str, str]) -> str:
    data = text.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise HerdrError(_TOO_LARGE)
    try:
        directory = _state_directory(env)
    except (ValueError, OSError):
        raise HerdrError(_STATE_UNAVAILABLE) from None
    name = "agent-rename-handoff-" + secrets.token_hex(16) + ".json"
    directory_fd = -1
    created = False
    try:
        directory_fd = _open_directory(directory)
        file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                          0o600, dir_fd=directory_fd)
        created = True
        with os.fdopen(file_fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
        return _json_text({"version": 1, "state_file": {"directory": str(directory), "name": name}})
    except OSError:
        if created:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError:
                pass
        raise HerdrError(_STATE_FAILED) from None
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def parse_context(env: Mapping[str, str]) -> Invocation:
    try:
        raw = json.loads(env.get("HERDR_PLUGIN_CONTEXT_JSON", ""))
        if not isinstance(raw, dict) or "focused_pane_id" not in raw:
            raise ValueError("Missing invocation target")
        return invocation_from_dict(raw)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise HerdrError(MISSING_CONTEXT) from None


def invocation_from_dict(raw: object) -> Invocation:
    if not isinstance(raw, dict):
        raise ValueError("Invalid invocation")
    pane = raw.get("focused_pane_id", raw.get("pane_id"))
    if not isinstance(pane, str) or not _PANE_ID.fullmatch(pane):
        raise ValueError("Invalid pane id")
    return Invocation(pane, *_location(raw))


def _location(raw: dict) -> list:
    location = []
    for key in ("workspace_id", "tab_id"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value or not value.isascii() or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError("Invalid location")
        location.append(value)
    return location


def parse_sync_context(env: Mapping[str, str]) -> Invocation:
    """Context for workspace sync: a workspace id and/or the focused pane id.

    The pane may be absent when the workspace id is present. Neither present
    means the invocation cannot be established; nothing else is consulted.
    """
    try:
        raw = json.loads(env.get("HERDR_PLUGIN_CONTEXT_JSON", ""))
        if not isinstance(raw, dict):
            raise ValueError("Invalid context")
        workspace, tab = _location(raw)
        pane = raw.get("focused_pane_id", raw.get("pane_id"))
        if pane is not None and (not isinstance(pane, str) or not _PANE_ID.fullmatch(pane)):
            raise ValueError("Invalid pane id")
        if workspace is None and pane is None:
            raise ValueError("Missing invocation target")
        return Invocation(pane or "", workspace, tab)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise HerdrError(MISSING_CONTEXT) from None


def freeze_workspace(invocation: Invocation, pane_get: Callable[[str], dict]) -> Invocation:
    """Return the invocation with its workspace id fixed.

    The id comes from the plugin context. Only when absent is it read from the
    frozen invoking pane's canonical ``pane get`` record, never from focus.
    """
    if invocation.workspace_id:
        return invocation
    if not invocation.pane_id:
        raise HerdrError(MISSING_CONTEXT)
    record = pane_get(invocation.pane_id)
    if record.get("pane_id") != invocation.pane_id or (invocation.tab_id and record.get("tab_id") != invocation.tab_id):
        raise HerdrError("The target pane has moved. Reopen the dialog.", code="target_moved")
    return replace(invocation, workspace_id=record["workspace_id"], tab_id=invocation.tab_id or record["tab_id"])


def encode_handoff(invocation: Invocation, snapshot: Snapshot | None, error: str, env: Mapping[str, str],
                   plan: SyncPlan | None = None) -> str:
    raw = {
        "version": 1,
        "invocation": asdict(invocation),
        "snapshot": asdict(snapshot) if snapshot else None,
        "error": error,
        "connection": {key: env[key] for key in ("HERDR_BIN_PATH", "HERDR_SOCKET_PATH", "HERDR_CONFIG_PATH") if key in env},
    }
    if plan is not None:
        raw["plan"] = plan_to_dict(plan)
    text = _json_text(raw)
    if len(text.encode("utf-8")) <= MAX_INLINE_BYTES:
        return text
    _compact_snapshot(raw)
    text = _json_text(raw)
    if len(text.encode("utf-8")) <= MAX_INLINE_BYTES:
        return text
    return _store_handoff(text, env)


def _decode_full(raw: object) -> tuple[Invocation, Snapshot | None, str, dict[str, str], SyncPlan | None]:
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or "state_file" in raw:
        raise ValueError("Invalid handoff")
    error = raw.get("error", "")
    if not isinstance(error, str):
        raise ValueError("Invalid error")
    plan = plan_from_dict(raw["plan"]) if raw.get("plan") is not None else None
    try:
        invocation = invocation_from_dict(raw["invocation"])
    except (ValueError, KeyError):
        if plan is None and error != MISSING_CONTEXT:
            raise
        invocation = _lenient_invocation(raw.get("invocation")) if plan is not None else Invocation("")
    snapshot = raw.get("snapshot")
    if snapshot is not None:
        if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(k), dict) for k in ("agent", "pane", "process")):
            raise ValueError("Invalid snapshot")
        snapshot = Snapshot(**{k: snapshot[k] for k in ("agent", "pane", "process")})
        if any(snapshot_dict.get("pane_id") != invocation.pane_id for snapshot_dict in (snapshot.agent, snapshot.pane)):
            raise ValueError("Mismatched snapshot")
    connection = raw.get("connection", {})
    if not isinstance(connection, dict) or any(k not in ("HERDR_BIN_PATH", "HERDR_SOCKET_PATH", "HERDR_CONFIG_PATH") or not isinstance(v, str) or "\0" in v for k, v in connection.items()):
        raise ValueError("Invalid connection")
    if plan is not None and (snapshot is not None or invocation.workspace_id != plan.workspace_id):
        raise ValueError("Mismatched plan")
    return invocation, snapshot, error, connection, plan


def _lenient_invocation(raw: object) -> Invocation:
    """A workspace-only invocation: no focused pane could be identified."""
    if not isinstance(raw, dict) or raw.get("pane_id") not in ("", None):
        raise ValueError("Invalid invocation")
    return Invocation("", *_location(raw))


def _consume_file(raw: object, env: Mapping[str, str]) -> tuple[Invocation, Snapshot | None, str, dict[str, str], SyncPlan | None]:
    """Read the private file once; unlink it on every path that opened our own file."""
    directory, name = _file_pointer(raw, env)
    directory_fd = _open_directory(directory)
    identity: tuple[int, int] | None = None
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                          dir_fd=directory_fd)
        with os.fdopen(file_fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid():
                identity = (before.st_dev, before.st_ino)
            if not _private_file(before) or before.st_size > MAX_FILE_BYTES:
                raise ValueError("Invalid private handoff file")
            data = handle.read(MAX_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
            if (len(data) > MAX_FILE_BYTES or len(data) != before.st_size
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or not _private_file(after)):
                raise ValueError("Changed handoff file")
            result = _decode_full(json.loads(data))
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino) or not _private_file(current):
                raise ValueError("Replaced handoff file")
            return result
    finally:
        try:
            if identity is not None:
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == identity:
                    os.unlink(name, dir_fd=directory_fd)
        except OSError:
            pass
        os.close(directory_fd)


def decode_handoff(env: Mapping[str, str]) -> tuple[Invocation, Snapshot | None, str, dict[str, str]]:
    return _load(env)[:4]


def decode_sync_handoff(env: Mapping[str, str]) -> tuple[Invocation, SyncPlan | None, str, dict[str, str]]:
    """Like ``decode_handoff`` for the workspace sync popup: returns the frozen plan."""
    invocation, _, error, connection, plan = _load(env)
    return invocation, plan, error, connection


def _load(env: Mapping[str, str]) -> tuple[Invocation, Snapshot | None, str, dict[str, str], SyncPlan | None]:
    try:
        text = env.get(HANDOFF_KEY, "")
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INLINE_BYTES:
            raise ValueError("Invalid inline handoff")
        raw = json.loads(text)
        if isinstance(raw, dict) and "state_file" in raw:
            return _consume_file(raw, env)
        return _decode_full(raw)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        raise HerdrError(MISSING_CONTEXT) from None


def cleanup_handoff(encoded: str, env: Mapping[str, str]) -> None:
    """Best-effort cleanup after popup launch failure; never mask that failure."""
    directory_fd = -1
    try:
        raw = json.loads(encoded)
        if not isinstance(raw, dict) or "state_file" not in raw:
            return
        directory, name = _file_pointer(raw, env)
        directory_fd = _open_directory(directory)
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _private_file(info):
            os.unlink(name, dir_fd=directory_fd)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        pass
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
