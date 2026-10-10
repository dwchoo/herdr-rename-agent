"""Large snapshots use a bounded pointer and private, one-use state files."""

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from herdr_agent_rename import context
from herdr_agent_rename.context import (
    HANDOFF_KEY, MAX_INLINE_BYTES, cleanup_handoff, decode_handoff, encode_handoff,
)
from herdr_agent_rename.controller import check_target
from herdr_agent_rename.models import HerdrError, Invocation, Snapshot


ORIGINAL = Invocation("w1:p1", "w1", "w1:t1")


def snapshot(*, large=False):
    location = {
        "pane_id": "w1:p1", "terminal_id": "term-1", "workspace_id": "w1", "tab_id": "w1:t1",
        "agent": "codex", "agent_session": {"agent": "codex", "source": "test", "kind": "id", "value": "session-1"},
    }
    argument = "task" * 20_000 if large else "task"
    process = {"pid": 123, "name": "codex", "argv0": "/usr/bin/codex",
               "argv": ["codex", argument], "cmdline": "codex " + argument}
    return Snapshot({**location, "name": "reviewer"}, {**location, "label": "backend-api"},
                    {"pane_id": "w1:p1", "foreground_processes": [process]})


@pytest.fixture
def state_env(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return {"HERDR_PLUGIN_STATE_DIR": str(state), "HERDR_BIN_PATH": "/opt/herdr",
            "HERDR_SOCKET_PATH": "/tmp/original.sock", "HERDR_CONFIG_PATH": "/tmp/original.toml"}


def file_path(encoded):
    pointer = json.loads(encoded)["state_file"]
    return Path(pointer["directory"]) / pointer["name"]


def test_small_handoff_preserves_entire_snapshot_and_creates_no_file(state_env):
    initial = snapshot()
    initial.agent["tokens"] = {"extra": "keep"}
    initial.pane["title"] = "preserved small metadata"
    initial.process["foreground_processes"][0]["cwd"] = "/tmp/test"
    encoded = encode_handoff(ORIGINAL, initial, "", state_env)
    invocation, decoded, error, connection = decode_handoff({**state_env, HANDOFF_KEY: encoded})
    assert (invocation, decoded, error) == (ORIGINAL, initial, "")
    assert connection == {key: state_env[key] for key in ("HERDR_BIN_PATH", "HERDR_SOCKET_PATH", "HERDR_CONFIG_PATH")}
    assert not list(Path(state_env["HERDR_PLUGIN_STATE_DIR"]).iterdir())


def test_large_argv_roundtrips_through_private_consumed_file_without_oversized_env(state_env):
    initial = snapshot(large=True)
    encoded = encode_handoff(ORIGINAL, initial, "", state_env)
    assert len(encoded.encode()) < MAX_INLINE_BYTES
    path = file_path(encoded)
    assert path.stat().st_size > MAX_INLINE_BYTES
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_uid == os.getuid()
    # The short pointer must be suitable for the same argv/env transport used
    # by the public popup request; the original 160 KiB JSON cannot be spawned.
    subprocess.run(["/usr/bin/true", encoded], env={**os.environ, HANDOFF_KEY: encoded}, check=True)
    invocation, decoded, error, connection = decode_handoff({**state_env, HANDOFF_KEY: encoded})
    assert (invocation, decoded, error) == (ORIGINAL, initial, "")
    assert connection["HERDR_SOCKET_PATH"] == "/tmp/original.sock"
    assert not path.exists()
    with pytest.raises(HerdrError, match="original pane"):
        decode_handoff({**state_env, HANDOFF_KEY: encoded})


def test_large_irrelevant_metadata_is_removed_without_losing_identity_or_source(state_env):
    initial = snapshot()
    initial.agent["tokens"] = {"noise": "x" * 200_000}
    initial.pane["title"] = "y" * 200_000
    initial.process["foreground_processes"][0]["cwd"] = "/unused/" + "z" * 200_000
    encoded = encode_handoff(ORIGINAL, initial, "", state_env)
    assert "state_file" not in json.loads(encoded)
    invocation, decoded, _, _ = decode_handoff({**state_env, HANDOFF_KEY: encoded})
    assert decoded.agent["name"] == "reviewer"
    assert decoded.pane["label"] == "backend-api"
    assert decoded.agent["agent_session"] == initial.agent["agent_session"]
    assert decoded.process["foreground_processes"][0]["argv"] == initial.process["foreground_processes"][0]["argv"]
    check_target(decoded, initial, invocation)
    changed = deepcopy(initial)
    changed.process["foreground_processes"][0]["argv"] = ["replacement"]
    with pytest.raises(HerdrError, match="agent has changed"):
        check_target(decoded, changed, invocation)
    assert not list(Path(state_env["HERDR_PLUGIN_STATE_DIR"]).iterdir())


def test_large_file_keeps_only_used_fields_and_exact_invalid_label(state_env):
    initial = snapshot(large=True)
    initial.agent["tokens"] = {"not_used": "noise"}
    initial.pane["label"] = " Exact invalid label\n"
    initial.pane["title"] = "unused"
    encoded = encode_handoff(ORIGINAL, initial, "", state_env)
    _, decoded, _, _ = decode_handoff({**state_env, HANDOFF_KEY: encoded})
    assert "tokens" not in decoded.agent and "title" not in decoded.pane
    assert decoded.pane["label"] == " Exact invalid label\n"
    assert decoded.process["foreground_processes"] == initial.process["foreground_processes"]


@pytest.mark.parametrize("state", [None, "", "/does-not-exist/herdr-rename-test", "bad\0path"])
def test_large_handoff_without_managed_state_fails_with_english_error(state):
    env = {} if state is None else {"HERDR_PLUGIN_STATE_DIR": state}
    with pytest.raises(HerdrError, match="Herdr plugin state is unavailable"):
        encode_handoff(ORIGINAL, snapshot(large=True), "", env)


def test_file_payload_size_has_a_hard_bound(state_env, monkeypatch):
    monkeypatch.setattr(context, "MAX_FILE_BYTES", MAX_INLINE_BYTES + 100)
    with pytest.raises(HerdrError, match="details are too large"):
        encode_handoff(ORIGINAL, snapshot(large=True), "", state_env)
    assert not list(Path(state_env["HERDR_PLUGIN_STATE_DIR"]).iterdir())


@pytest.mark.parametrize("kind", ["symlink", "public_mode", "hardlink", "directory", "fifo", "oversized", "malformed", "deep_json", "invalid_payload"])
def test_decode_rejects_unsafe_or_invalid_file_without_consuming_it(state_env, tmp_path, monkeypatch, kind):
    encoded = encode_handoff(ORIGINAL, snapshot(large=True), "", state_env)
    path = file_path(encoded)
    if kind == "symlink":
        original = tmp_path / "original.json"
        path.rename(original)
        path.symlink_to(original)
    elif kind == "public_mode":
        path.chmod(0o644)
    elif kind == "hardlink":
        os.link(path, tmp_path / "extra-link")
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif kind == "oversized":
        monkeypatch.setattr(context, "MAX_FILE_BYTES", 1024)
    elif kind == "malformed":
        path.write_text("{", encoding="utf-8")
    elif kind == "deep_json":
        path.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    elif kind == "invalid_payload":
        raw = json.loads(path.read_text())
        raw["snapshot"]["agent"]["pane_id"] = "w9:p9"
        path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HerdrError, match="original pane"):
        decode_handoff({**state_env, HANDOFF_KEY: encoded})
    assert os.path.lexists(path)


def test_decode_rejects_file_owned_by_a_different_uid(state_env, monkeypatch):
    encoded = encode_handoff(ORIGINAL, snapshot(large=True), "", state_env)
    path = file_path(encoded)
    real_uid = os.getuid()
    monkeypatch.setattr(context.os, "getuid", lambda: real_uid + 1)
    with pytest.raises(HerdrError, match="original pane"):
        decode_handoff({**state_env, HANDOFF_KEY: encoded})
    assert path.exists()


@pytest.mark.parametrize("tamper", ["parent_path", "absolute_path", "wrong_directory", "changed_state_env", "extra_pointer_field"])
def test_decode_rejects_pointer_outside_expected_state_directory(state_env, tmp_path, tamper):
    encoded = encode_handoff(ORIGINAL, snapshot(large=True), "", state_env)
    path = file_path(encoded)
    raw = json.loads(encoded)
    env = dict(state_env)
    if tamper == "parent_path":
        raw["state_file"]["name"] = "../" + raw["state_file"]["name"]
    elif tamper == "absolute_path":
        raw["state_file"]["name"] = str(path)
    elif tamper == "wrong_directory":
        raw["state_file"]["directory"] = str(tmp_path)
    elif tamper == "changed_state_env":
        env["HERDR_PLUGIN_STATE_DIR"] = str(tmp_path)
    elif tamper == "extra_pointer_field":
        raw["state_file"]["extra"] = True
    with pytest.raises(HerdrError, match="original pane"):
        decode_handoff({**env, HANDOFF_KEY: json.dumps(raw)})
    assert path.exists()


def test_cleanup_removes_only_its_owned_file_and_is_idempotent(state_env):
    encoded = encode_handoff(ORIGINAL, snapshot(large=True), "", state_env)
    path = file_path(encoded)
    other = path.parent / "unrelated.txt"
    other.write_text("keep")
    cleanup_handoff(encoded, state_env)
    cleanup_handoff(encoded, state_env)
    cleanup_handoff("not json", state_env)
    cleanup_handoff("[" * 2000 + "0" + "]" * 2000, state_env)
    cleanup_handoff(encode_handoff(ORIGINAL, snapshot(), "", state_env), state_env)
    assert not path.exists() and other.read_text() == "keep"


def test_cleanup_never_unlinks_a_substituted_symlink_or_cross_directory_pointer(state_env, tmp_path):
    encoded = encode_handoff(ORIGINAL, snapshot(large=True), "", state_env)
    path = file_path(encoded)
    target = tmp_path / "keep.json"
    path.rename(target)
    path.symlink_to(target)
    cleanup_handoff(encoded, state_env)
    assert path.is_symlink() and target.is_file()
    pointer = json.loads(encoded)
    pointer["state_file"]["directory"] = str(tmp_path)
    cleanup_handoff(json.dumps(pointer), state_env)
    assert target.is_file()


def test_cleanup_after_popup_consumed_the_file_is_harmless(state_env):
    encoded = encode_handoff(ORIGINAL, snapshot(large=True), "", state_env)
    decode_handoff({**state_env, HANDOFF_KEY: encoded})
    cleanup_handoff(encoded, state_env)
    assert not list(Path(state_env["HERDR_PLUGIN_STATE_DIR"]).iterdir())


def test_decode_rejects_oversized_inline_payload_instead_of_accepting_unbounded_env(state_env):
    raw = json.loads(encode_handoff(ORIGINAL, snapshot(), "", state_env))
    raw["error"] = "x" * (MAX_INLINE_BYTES + 1)
    with pytest.raises(HerdrError, match="original pane"):
        decode_handoff({**state_env, HANDOFF_KEY: json.dumps(raw)})
