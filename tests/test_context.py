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
