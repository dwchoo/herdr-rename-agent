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
    candidate = "x-flag_1"
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


SYNC = json.loads((Path(__file__).parent / "fixtures/sync-0.9.3.json").read_text())


def envelope(fixture):
    return json.dumps(fixture)


def test_pane_list_and_agent_list_literal_argv_and_real_shapes(monkeypatch):
    calls = install_run(monkeypatch, [(0, envelope(SYNC["pane_list_ws1"]), ""), (0, envelope(SYNC["agent_list"]), "")])
    client = HerdrClient({"HERDR_BIN_PATH": "/herdr"}, timeout=0.5)
    panes = client.pane_list("w1")
    agents = client.agent_list()
    assert calls[0][0] == ["/herdr", "pane", "list", "--workspace", "w1"]
    assert calls[1][0] == ["/herdr", "agent", "list"]
    assert all(kwargs["timeout"] == 0.5 and kwargs["stdin"] == subprocess.DEVNULL for _, kwargs in calls)
    assert [p["pane_id"] for p in panes] == ["w1:p1", "w1:p3", "w1:p2", "w1:p4", "w1:p5"]
    assert "label" not in panes[2]
    assert {a["name"] for a in agents} == {"alpha", "beta", "gamma"}


def test_tab_list_and_workspace_label(monkeypatch):
    install_run(monkeypatch, [(0, envelope(SYNC["tab_list"]), ""), (0, envelope(SYNC["workspace_list"]), "")])
    client = HerdrClient({"HERDR_BIN_PATH": "/herdr"})
    assert [t["label"] for t in client.tab_list("w1")][:2] == ["1", "second"]
    assert client.workspace_label("w2") == "second-ws"


@pytest.mark.parametrize("raw", [
    {"id": "x", "result": {"type": "pane_list", "panes": "no"}},
    {"id": "x", "result": {"type": "other", "panes": []}},
    {"id": "x", "result": {"type": "pane_list", "panes": [None]}},
    {"id": "x", "result": {"type": "pane_list", "panes": [{"pane_id": "w1:p1"}]}},
])
def test_malformed_list_responses_are_unsupported(monkeypatch, raw):
    install_run(monkeypatch, [(0, json.dumps(raw), "")])
    with pytest.raises(HerdrError, match="not supported"):
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).pane_list("w1")


def test_list_connection_failure_has_a_connection_code(monkeypatch):
    install_run(monkeypatch, [subprocess.TimeoutExpired("herdr", 5)])
    with pytest.raises(HerdrError) as failure:
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).agent_list()
    assert failure.value.code == "connection" and not failure.value.uncertain


def test_clear_name_literal_argv_and_uncertain_write(monkeypatch):
    unnamed = {k: v for k, v in AGENT.items() if k != "name"}
    calls = install_run(monkeypatch, [(0, response(data=unnamed), ""), subprocess.TimeoutExpired("herdr", 5)])
    client = HerdrClient({"HERDR_BIN_PATH": "/herdr"})
    assert "name" not in client.clear_name("w1:p1")
    assert calls[0][0] == ["/herdr", "agent", "rename", "w1:p1", "--clear"]
    with pytest.raises(HerdrError) as failure:
        client.clear_name("w1:p1")
    assert failure.value.uncertain and len(calls) == 2


def test_clear_agent_not_found_is_definite(monkeypatch):
    raw = json.dumps({"id": "cli:agent:rename", "error": {"code": "agent_not_found", "message": "agent target does not currently host an agent"}})
    install_run(monkeypatch, [(1, "", raw)])
    with pytest.raises(HerdrError) as failure:
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).clear_name("w1:p3")
    assert failure.value.code == "agent_not_found" and not failure.value.uncertain


def test_pane_get_has_no_agent_requirement(monkeypatch):
    install_run(monkeypatch, [(0, response("pane_info", "pane", {**AGENT, "agent": None}), "")])
    assert HerdrClient({"HERDR_BIN_PATH": "/herdr"}).pane_get("w1:p1")["workspace_id"] == "w1"


def _drop_method(name):
    def mutate(request):
        request["oneOf"] = [m for m in request["oneOf"] if m["properties"]["method"]["const"] != name]
    return mutate


@pytest.mark.parametrize("mutate", [
    _drop_method("pane.list"),
    _drop_method("agent.list"),
    lambda request: request["$defs"]["AgentRenameParams"]["properties"]["name"].update(type="string"),
    lambda request: request["$defs"]["PaneListParams"]["properties"].pop("workspace_id"),
])
def test_list_and_clear_capabilities_are_required(monkeypatch, mutate):
    schema = json.loads((Path(__file__).parent / "fixtures/schema-0.9.3.json").read_text())
    mutate(schema["schemas"]["request"])
    install_run(monkeypatch, [(0, "herdr 0.9.3\n", ""), (0, json.dumps(schema), "")])
    with pytest.raises(HerdrError, match="not supported"):
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).verify_support()


def test_rename_rejects_names_that_could_be_cli_flags_before_any_request(monkeypatch):
    calls = install_run(monkeypatch, [])
    for bad in ("--clear", "-x", "x;$(id)", "", "Name"):
        with pytest.raises(HerdrError) as failure:
            HerdrClient({"HERDR_BIN_PATH": "/herdr"}).rename("w1:p1", bad)
        assert not failure.value.uncertain
    assert calls == []


@pytest.mark.parametrize("result", [subprocess.TimeoutExpired("herdr", 5), OSError("gone"), (1, "", "disconnected")])
def test_transport_failure_of_a_write_has_a_distinct_code(monkeypatch, result):
    install_run(monkeypatch, [result])
    with pytest.raises(HerdrError) as failure:
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).rename("w1:p1", "name")
    assert failure.value.uncertain and failure.value.code == "transport"


def _supported(monkeypatch, version, schema):
    install_run(monkeypatch, [(0, version, ""), (0, json.dumps(schema), "")])
    HerdrClient({"HERDR_BIN_PATH": "/herdr"}).verify_support()


def _schema():
    return json.loads((Path(__file__).parent / "fixtures/schema-0.9.3.json").read_text())


@pytest.mark.parametrize("version", ["herdr 0.9.4 (abc123)\n", "herdr 0.10.0-rc.1+build.5 (deadbeef 2026-01-01)\n", "herdr 1.2.3\n"])
def test_newer_version_strings_with_build_info_are_accepted(monkeypatch, version):
    _supported(monkeypatch, version, _schema())


@pytest.mark.parametrize("version", ["herdr 0.9.2 (abc)\n", "herdr 0.9\n", "other 0.9.3\n"])
def test_old_or_odd_version_strings_are_rejected(monkeypatch, version):
    install_run(monkeypatch, [(0, version, "")])
    with pytest.raises(HerdrError, match="not supported"):
        HerdrClient({"HERDR_BIN_PATH": "/herdr"}).verify_support()


def test_schema_shape_changes_are_tolerated(monkeypatch):
    schema = _schema()
    request = schema["schemas"]["request"]
    defs = request["$defs"]
    # oneOf -> anyOf for the methods, const -> single-value enum.
    request["anyOf"] = [{"properties": {"method": {"enum": [m["properties"]["method"]["const"]]}}} for m in request.pop("oneOf")]
    # Placement enum split into a oneOf of consts; nullable name via anyOf; extra type list.
    defs["PluginPanePlacement"] = {"oneOf": [{"const": v} for v in ("overlay", "popup", "split")]}
    defs["AgentRenameParams"]["properties"]["name"] = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    defs["PaneListParams"]["properties"]["workspace_id"] = {"anyOf": [{"type": ["string"]}, {"type": "null"}]}
    _supported(monkeypatch, "herdr 0.9.3\n", schema)


def test_missing_optional_detail_is_not_unsupported_when_methods_exist(monkeypatch):
    schema = _schema()
    for name in ("PluginPanePlacement", "PluginInvocationContext", "PaneListParams", "AgentRenameParams"):
        del schema["schemas"]["request"]["$defs"][name]
    _supported(monkeypatch, "herdr 0.9.3\n", schema)


def test_unknown_session_kind_elsewhere_does_not_reject_a_list(monkeypatch):
    other = {**AGENT, "pane_id": "w2:p1", "terminal_id": "t2", "workspace_id": "w2", "tab_id": "w2:t1",
             "name": "holder", "agent_session": {"agent": "x", "source": "s", "kind": "future-kind", "value": "v"}}
    body = json.dumps({"id": "t", "result": {"type": "agent_list", "agents": [AGENT, other]}})
    install_run(monkeypatch, [(0, body, ""), (0, body, "")])
    client = HerdrClient({"HERDR_BIN_PATH": "/herdr"})
    assert [a["name"] for a in client.agent_list()] == ["reviewer", "holder"]
    # ...while a strict observation of that pane stays unsupported.
    pane = {**other, "agent": "x"}
    install_run(monkeypatch, [(0, response("pane_info", "pane", pane), "")])
    with pytest.raises(HerdrError, match="not supported"):
        client.pane_get("w2:p1")
