import json

import pytest

from herdr_agent_rename.context import decode_handoff, encode_handoff, parse_context
from herdr_agent_rename.models import HerdrError, Invocation, Snapshot


@pytest.mark.parametrize("raw", [None, "", "not-json", "[]", "{}", '{"pane_id":"w1:p1"}', '{"focused_pane_id":null}', '{"focused_pane_id":"--current"}', '{"focused_pane_id":"w1:p1","tab_id":42}'])
def test_context_never_falls_back_to_popup_or_current_pane(raw):
    env = {"HERDR_PANE_ID": "w9:p9", "HERDR_TAB_ID": "w9:t9"}
    if raw is not None:
        env["HERDR_PLUGIN_CONTEXT_JSON"] = raw
    with pytest.raises(HerdrError, match="original pane"):
        parse_context(env)


def test_frozen_handoff_ignores_later_plugin_context_and_preserves_connection():
    invocation = Invocation("w1:p1", "w1", "w1:t1")
    snap = Snapshot({"pane_id": "w1:p1", "name": "reviewer"}, {"pane_id": "w1:p1", "label": "backend-api"}, {})
    handoff = encode_handoff(invocation, snap, "", {"HERDR_SOCKET_PATH": "/tmp/original.sock", "HERDR_BIN_PATH": "/usr/bin/herdr"})
    result = decode_handoff({"HERDR_AGENT_RENAME_HANDOFF": handoff, "HERDR_PLUGIN_CONTEXT_JSON": '{"focused_pane_id":"w9:p9"}'})
    assert result == (invocation, snap, "", {"HERDR_SOCKET_PATH": "/tmp/original.sock", "HERDR_BIN_PATH": "/usr/bin/herdr"})


def test_handoff_rejects_snapshot_for_another_pane():
    snap = Snapshot({"pane_id": "w9:p9"}, {"pane_id": "w9:p9"}, {})
    handoff = encode_handoff(Invocation("w1:p1"), snap, "", {})
    with pytest.raises(HerdrError):
        decode_handoff({"HERDR_AGENT_RENAME_HANDOFF": handoff})


def test_handoff_rejects_arbitrary_environment_override():
    handoff = json.loads(encode_handoff(Invocation("w1:p1"), None, "Missing agent", {}))
    handoff["connection"] = {"LD_PRELOAD": "payload.so"}
    with pytest.raises(HerdrError):
        decode_handoff({"HERDR_AGENT_RENAME_HANDOFF": json.dumps(handoff)})


from herdr_agent_rename.context import decode_sync_handoff, freeze_workspace, parse_sync_context


def test_sync_context_accepts_workspace_only_pane_only_or_both():
    assert parse_sync_context({"HERDR_PLUGIN_CONTEXT_JSON": '{"workspace_id":"w1"}'}) == Invocation("", "w1")
    assert parse_sync_context({"HERDR_PLUGIN_CONTEXT_JSON": '{"focused_pane_id":"w1:p1"}'}) == Invocation("w1:p1")
    both = '{"focused_pane_id":"w1:p1","workspace_id":"w1","tab_id":"w1:t1"}'
    assert parse_sync_context({"HERDR_PLUGIN_CONTEXT_JSON": both}) == Invocation("w1:p1", "w1", "w1:t1")


@pytest.mark.parametrize("raw", [None, "", "[]", "{}", '{"workspace_id":""}', '{"workspace_id":"w 1"}', '{"focused_pane_id":"--current"}'])
def test_sync_context_never_falls_back_to_popup_variables(raw):
    env = {"HERDR_PANE_ID": "w9:p9", "HERDR_WORKSPACE_ID": "w9"}
    if raw is not None:
        env["HERDR_PLUGIN_CONTEXT_JSON"] = raw
    with pytest.raises(HerdrError, match="original pane"):
        parse_sync_context(env)


def test_freeze_workspace_prefers_the_context_and_makes_no_lookup():
    def forbidden(pane_id):
        raise AssertionError("no lookup expected")
    invocation = Invocation("w1:p1", "w1", "w1:t1")
    assert freeze_workspace(invocation, forbidden) == invocation


def test_freeze_workspace_fallback_uses_only_the_invoking_panes_canonical_record():
    seen = []
    record = {"pane_id": "w3:p2", "workspace_id": "w3", "tab_id": "w3:t1"}
    result = freeze_workspace(Invocation("w3:p2"), lambda pane_id: seen.append(pane_id) or record)
    assert seen == ["w3:p2"] and result == Invocation("w3:p2", "w3", "w3:t1")


@pytest.mark.parametrize("invocation,record", [
    (Invocation("w3:p2"), {"pane_id": "w3:p5", "workspace_id": "w3", "tab_id": "w3:t1"}),
    (Invocation("w3:p2", None, "w3:t9"), {"pane_id": "w3:p2", "workspace_id": "w3", "tab_id": "w3:t1"}),
])
def test_freeze_workspace_rejects_a_moved_pane(invocation, record):
    with pytest.raises(HerdrError, match="moved"):
        freeze_workspace(invocation, lambda pane_id: record)


def test_freeze_workspace_without_pane_or_workspace_fails():
    with pytest.raises(HerdrError, match="original pane"):
        freeze_workspace(Invocation(""), lambda pane_id: {})
