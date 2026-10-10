"""Fixtures shared by the workspace sync tests (Herdr 0.9.3 record shapes)."""

from copy import deepcopy

from herdr_agent_rename.models import HerdrError, Snapshot
from herdr_agent_rename.sync import build_plan


def pane(pane_id, tab="t1", label=None, workspace="w1"):
    record = {"pane_id": pane_id, "terminal_id": "term-" + pane_id, "workspace_id": workspace,
              "tab_id": "%s:%s" % (workspace, tab), "focused": False, "agent_status": "unknown"}
    if label is not None:
        record["label"] = label
    return record


def agent(pane_id, name=None, workspace="w1", tab="t1", kind="codex"):
    record = {"pane_id": pane_id, "terminal_id": "term-" + pane_id, "workspace_id": workspace,
              "tab_id": "%s:%s" % (workspace, tab), "agent_status": "unknown"}
    if kind:
        record["agent"] = kind
    if name:
        record["name"] = name
    return record


def snapshot_of(panes, agents, pane_id):
    p = next(x for x in panes if x["pane_id"] == pane_id)
    a = next(x for x in agents if x["pane_id"] == pane_id)
    return Snapshot(deepcopy(a), deepcopy(p), {"pane_id": pane_id})


def observed(panes, agents, plan):
    return {r.pane_id: snapshot_of(panes, agents, r.pane_id) for r in plan.eligible}


class Fake:
    """Mutable server state with the client methods the executor uses."""

    def __init__(self, panes, agents):
        self.panes, self.agents = deepcopy(panes), deepcopy(agents)
        self.calls, self.writes = [], []
        self.fail = {}        # method name -> exception raised once
        self.rename_hook = None
        self.notifications, self.popups, self.env = [], [], {}

    def verify_support(self):
        self.calls.append("support")

    def pane_get(self, pane_id):
        self.calls.append("pane_get")
        for p in self.panes:
            if p["pane_id"] == pane_id:
                return deepcopy(p)
        raise HerdrError("The target pane no longer exists.", code="pane_not_found")

    def notify(self, title):
        self.notifications.append(title)

    def open_popup(self, invocation, mode, handoff):
        self.popups.append((invocation, mode, handoff))

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise self.fail.pop(name)

    def pane_list(self, workspace_id):
        self._maybe_fail("pane_list")
        return deepcopy([p for p in self.panes if p["workspace_id"] == workspace_id])

    def agent_list(self):
        self._maybe_fail("agent_list")
        return deepcopy(self.agents)

    def tab_list(self, workspace_id):
        return []

    def workspace_label(self, workspace_id):
        return None

    def get_snapshot(self, invocation):
        self._maybe_fail("get_snapshot")
        p = next((x for x in self.panes if x["pane_id"] == invocation.pane_id), None)
        a = next((x for x in self.agents if x["pane_id"] == invocation.pane_id), None)
        if p is None:
            raise HerdrError("The target pane no longer exists.", code="pane_not_found")
        if a is None or not a.get("agent"):
            raise HerdrError("No running agent was found in this pane.", code="no_agent")
        return Snapshot(deepcopy(a), deepcopy(p), {"pane_id": p["pane_id"]})

    def rename(self, pane_id, name):
        self.writes.append((pane_id, name))
        self.calls.append("rename")
        if self.rename_hook:
            return self.rename_hook(self, pane_id, name)
        return self.apply(pane_id, name)

    def apply(self, pane_id, name):
        a = next(x for x in self.agents if x["pane_id"] == pane_id)
        if any(x.get("name") == name and x is not a for x in self.agents):
            raise HerdrError("This agent name is already in use.", code="agent_name_taken")
        a["name"] = name
        return deepcopy(a)


def world(*specs):
    """specs: (pane_id, label, agent_name or '-' for no agent or None for unnamed, tab)."""
    panes, agents = [], []
    for pane_id, label, name, *tab in specs:
        panes.append(pane(pane_id, tab[0] if tab else "t1", label))
        if name != "-":
            agents.append(agent(pane_id, name, tab=tab[0] if tab else "t1"))
    return panes, agents


def prepared(*specs):
    panes, agents = world(*specs)
    plan = build_plan("w1", panes, agents, observations=observed(panes, agents, build_plan("w1", panes, agents)))
    return Fake(panes, agents), plan
