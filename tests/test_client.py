import json
import subprocess
from pathlib import Path

import pytest

from herdr_agent_rename.client import HerdrClient
from herdr_agent_rename.models import HerdrError, Invocation


AGENT = {"pane_id": "w1:p1", "terminal_id": "term-test", "workspace_id": "w1", "tab_id": "w1:t1", "agent": "codex", "name": "reviewer"}


def response(kind="agent_info", key="agent", data=None):
    return json.dumps({"id": "test", "result": {"type": kind, key: AGENT if data is None else data}})


def install_run(monkeypatch, values):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        result = values.pop(0)
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(argv, *result)
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_rename_literal_argv_connection_timeout_and_no_terminal_input(monkeypatch):
    calls = install_run(monkeypatch, [(0, response(), "")])
    candidate = "x;$(touch /tmp/never-execute)`id`"
    client = HerdrClient({"HERDR_BIN_PATH": "/trusted/herdr", "HERDR_SOCKET_PATH": "/tmp/isolated.sock"}, timeout=0.5)
    client.rename("w1:p1", candidate)
    argv, kwargs = calls[0]
    assert argv == ["/trusted/herdr", "agent", "rename", "w1:p1", candidate]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == 0.5
    assert kwargs["env"]["HERDR_SOCKET_PATH"] == "/tmp/isolated.sock"
    assert not kwargs.get("shell", False)


@pytest.mark.parametrize("result", [(0, "{}", ""), (0, response("unexpected"), ""), (1, "", "disconnected"), subprocess.TimeoutExpired("herdr", 5)])
def test_uncertain_write_is_one_request(monkeypatch, result):
    calls = install_run(monkeypatch, [result])
    with pytest.raises(HerdrError) as failure:
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).rename("w1:p1", "name")
    assert failure.value.uncertain
    assert len(calls) == 1


def test_json_server_error_is_definite_and_sanitized(monkeypatch):
    raw = json.dumps({"id": "test", "error": {"code": "agent_name_taken", "message": "Name already in use.\u001b[2J"}})
    install_run(monkeypatch, [(1, "", raw)])
    with pytest.raises(HerdrError, match="already in use") as failure:
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).rename("w1:p1", "name")
    assert not failure.value.uncertain
    assert "\x1b" not in str(failure.value)


@pytest.mark.parametrize("kind", ["ok", "plugin_pane_opened"])
def test_manifest_placement_and_frozen_environment_used(monkeypatch, kind):
    calls = install_run(monkeypatch, [(0, response(kind, "plugin_pane", {}), "")])
    HerdrClient({"HERDR_BIN_PATH": "/herdr"}).open_popup(Invocation("w1:p1", "w1", "w1:t1"), "sync", '{"origin":"w1:p1"}')
    argv = calls[0][0]
    assert "--placement" not in argv
    assert "HERDR_AGENT_RENAME_HANDOFF={\"origin\":\"w1:p1\"}" in argv
    assert "--target-pane" not in argv
    assert "--workspace" not in argv


def test_live_snapshot_requires_agent_and_coherent_records(monkeypatch):
    install_run(monkeypatch, [(0, response("pane_info", "pane"), ""), (0, response(data={**AGENT, "agent": None}), "")])
    with pytest.raises(HerdrError, match="No running agent"):
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).get_snapshot(Invocation("w1:p1"))


def test_version_and_capabilities_both_required(monkeypatch):
    schema = (Path(__file__).parent / "fixtures/schema-0.9.3.json").read_text()
    install_run(monkeypatch, [(0, "herdr 0.9.3\n", ""), (0, schema, "")])
    HerdrClient({"HERDR_BIN_PATH": "/herdr"}).verify_support()
    changed = json.loads(schema)
    changed["schemas"]["request"]["$defs"]["PluginPanePlacement"]["enum"].remove("popup")
    install_run(monkeypatch, [(0, "herdr 1.0.0\n", ""), (0, json.dumps(changed), "")])
    with pytest.raises(HerdrError, match="not supported"):
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).verify_support()


def test_old_version_rejected_before_schema(monkeypatch):
    calls = install_run(monkeypatch, [(0, "herdr 0.9.2\n", "")])
    with pytest.raises(HerdrError, match="not supported"):
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).verify_support()
    assert len(calls) == 1


@pytest.mark.parametrize("error", [{"code": [], "message": "error"}, {"message": "error"}, {"code": "known"}, {"code": "known", "message": "error", "id": None}])
def test_malformed_write_error_cannot_establish_definite_failure(monkeypatch, error):
    envelope = {"id": "test", "error": error}
    if "id" in error:
        envelope["id"] = error.pop("id")
    install_run(monkeypatch, [(1, json.dumps(envelope), "")])
    with pytest.raises(HerdrError) as failure:
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).rename("w1:p1", "name")
    assert failure.value.uncertain


@pytest.mark.parametrize("properties", [None, [], "invalid"])
def test_malformed_capabilities_are_unsupported(monkeypatch, properties):
    schema = json.loads((Path(__file__).parent / "fixtures/schema-0.9.3.json").read_text())
    schema["schemas"]["request"]["$defs"]["AgentRenameParams"]["properties"] = properties
    install_run(monkeypatch, [(0, "herdr 0.9.3\n", ""), (0, json.dumps(schema), "")])
    with pytest.raises(HerdrError, match="not supported"):
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).verify_support()


@pytest.mark.parametrize("processes", [{}, [None], [{"pid": True, "name": "codex"}], [{"pid": 5, "name": "codex", "argv": "invalid"}]])
def test_malformed_process_evidence_is_rejected(monkeypatch, processes):
    process = {"pane_id": "w1:p1", "foreground_processes": processes}
    install_run(monkeypatch, [(0, response("pane_info", "pane"), ""), (0, response(), ""), (0, response("pane_process_info", "process_info", process), "")])
    with pytest.raises(HerdrError, match="not supported"):
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).get_snapshot(Invocation("w1:p1"))


def test_installed_runtime_fixture_parses(monkeypatch):
    fixture = json.loads((Path(__file__).parent / "fixtures/runtime-0.9.3.json").read_text())
    install_run(monkeypatch, [
        (0, response("pane_info", "pane", fixture["pane"]), ""),
        (0, response("agent_info", "agent", fixture["agent"]), ""),
        (0, response("pane_process_info", "process_info", fixture["process_info"]), ""),
    ])
    invocation = Invocation(fixture["pane"]["pane_id"], fixture["pane"]["workspace_id"], fixture["pane"]["tab_id"])
    snapshot = HerdrClient({"HERDR_BIN_PATH": "/herdr"}).get_snapshot(invocation)
    assert snapshot.agent["name"] == "backend-api"
    assert snapshot.pane["label"] == "source-changed"
    assert len(snapshot.process["foreground_processes"]) == 2


@pytest.mark.parametrize("kind", ["pane", "agent"])
def test_installed_not_found_error_envelopes(monkeypatch, kind):
    fixture = json.loads((Path(__file__).parent / "fixtures/errors-0.9.3.json").read_text())
    raw = fixture["responses"][kind + "_not_found"]
    install_run(monkeypatch, [(1, json.dumps(raw), "")])
    with pytest.raises(HerdrError) as failure:
        HerdrClient({"HERDR_BIN_PATH": "/herdr"})._json([kind, "get", "w0:p99999"])
    assert failure.value.code == kind + "_not_found"
    assert not failure.value.uncertain
