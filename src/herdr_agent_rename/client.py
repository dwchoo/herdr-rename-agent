"""Bounded public CLI calls, with literal argv and strict response envelopes."""

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from typing import Mapping

from .validation import validate_name
from .models import HerdrError, Invocation, Snapshot

UNSUPPORTED = "This Herdr version is not supported."
CONNECTION = "Could not connect to Herdr."
TRANSPORT = "transport"
UNCERTAIN = "The rename result could not be confirmed. Check the current name before retrying."


def sanitize(text: str) -> str:
    return "".join(c if not unicodedata.category(c).startswith("C") else " " for c in text)[:300]


_REQUIRED_METHODS = {"agent.get", "agent.list", "agent.rename", "pane.get", "pane.list",
                     "pane.process_info", "plugin.pane.open", "notification.show"}
_COMBINATORS = ("oneOf", "anyOf", "allOf")


def _alternatives(node: object, definitions: Mapping, depth: int = 0):
    """Yield a schema node and every nested combinator/``$ref`` alternative."""
    if not isinstance(node, dict) or depth > 8:
        return
    ref = node.get("$ref")
    if isinstance(ref, str) and "/$defs/" in ref:
        yield from _alternatives(definitions.get(ref.rsplit("/", 1)[1]), definitions, depth + 1)
    yield node
    for key in _COMBINATORS:
        for item in node.get(key) or ():
            yield from _alternatives(item, definitions, depth + 1)


def _values(node: object, definitions: Mapping) -> set:
    found: set = set()
    for item in _alternatives(node, definitions):
        if "const" in item and isinstance(item["const"], str):
            found.add(item["const"])
        if isinstance(item.get("enum"), list):
            found.update(v for v in item["enum"] if isinstance(v, str))
    return found


def _types(node: object, definitions: Mapping) -> set:
    found: set = set()
    for item in _alternatives(node, definitions):
        kind = item.get("type")
        found.update([kind] if isinstance(kind, str) else [k for k in kind or () if isinstance(k, str)])
        if item.get("const") is None and "const" in item:
            found.add("null")
        if isinstance(item.get("enum"), list) and None in item["enum"]:
            found.add("null")
    return found


def _properties(node: object, definitions: Mapping) -> dict:
    """Union of ``properties`` over a node and its alternatives; malformed is an error."""
    merged: dict = {}
    for item in _alternatives(node, definitions):
        if "properties" in item:
            if not isinstance(item["properties"], dict):
                raise TypeError("properties")
            merged.update(item["properties"])
    return merged


def _schema_supported(request: dict) -> bool:
    definitions = request.get("$defs") if isinstance(request.get("$defs"), dict) else {}
    methods: set = set()
    for key in ("oneOf", "anyOf"):
        for item in request.get(key) or ():
            method = _properties(item, definitions).get("method")
            methods |= _values(method, definitions)
    if not _REQUIRED_METHODS <= methods:
        return False
    # Optional detail: absent means "cannot tell" (the methods exist); wrong means unsupported.
    if "PluginPanePlacement" in definitions and "popup" not in _values(definitions["PluginPanePlacement"], definitions):
        return False
    for definition, fields in (
        ("AgentRenameParams", {"target", "name"}),
        ("PluginPaneOpenParams", {"env", "target_pane_id", "workspace_id"}),
        ("PluginInvocationContext", {"focused_pane_id", "workspace_id", "tab_id"}),
        ("PaneListParams", {"workspace_id"}),
    ):
        if definition in definitions and not fields <= _properties(definitions[definition], definitions).keys():
            return False
    # Explicit-name removal sends ``name: null`` (CLI ``--clear``).
    if "AgentRenameParams" in definitions:
        name = _properties(definitions["AgentRenameParams"], definitions).get("name")
        if name is not None and "null" not in _types(name, definitions):
            return False
    return True


class HerdrClient:
    def __init__(self, env: Mapping[str, str] | None = None, *, timeout: float = 5.0):
        self.env = dict(os.environ if env is None else env)
        self.binary = self.env.get("HERDR_BIN_PATH") or shutil.which("herdr", path=self.env.get("PATH"))
        self.timeout = timeout
        self._supported = False

    def _exec(self, argv: list[str], *, write: bool = False) -> subprocess.CompletedProcess[str]:
        if not self.binary:
            raise HerdrError(UNSUPPORTED, code="unsupported")
        try:
            return subprocess.run([self.binary, *argv], stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", env=self.env,
                                  timeout=self.timeout, check=False)
        except (subprocess.TimeoutExpired, OSError):
            # An uncertain write is a transport failure: the queue must stop.
            raise HerdrError(UNCERTAIN if write else CONNECTION, uncertain=write, code=TRANSPORT if write else "connection") from None

    def _json(self, argv: list[str], *, write: bool = False) -> dict:
        completed = self._exec(argv, write=write)
        for output in (completed.stdout, completed.stderr):
            try:
                raw = json.loads(output)
            except (ValueError, RecursionError):
                continue
            if isinstance(raw, dict) and isinstance(raw.get("error"), dict):
                error = raw["error"]
                code = error.get("code")
                if not isinstance(raw.get("id"), str) or not isinstance(code, str) or not isinstance(error.get("message"), str):
                    raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
                details = sanitize(error["message"])
                message = {
                    "pane_not_found": "The target pane no longer exists.",
                    "agent_not_found": "No running agent was found in this pane.",
                    "agent_name_taken": "This agent name is already in use.",
                    "agent_name_conflict": "This agent name is already in use.",
                    "duplicate_agent_name": "This agent name is already in use.",
                    "unknown_method": UNSUPPORTED,
                    "method_not_found": UNSUPPORTED,
                    "unsupported": UNSUPPORTED,
                }.get(code, "Could not rename the agent. Try again or cancel." if write else CONNECTION)
                if "already" in details.lower() and ("name" in details.lower() or "use" in details.lower()):
                    message = "This agent name is already in use."
                if details:
                    message += " " + details
                raise HerdrError(message, code=str(code))
            if output == completed.stdout and completed.returncode == 0 and isinstance(raw, dict) and isinstance(raw.get("result"), dict) and isinstance(raw.get("id"), str):
                return raw["result"]
        if completed.returncode and not write:
            raise HerdrError(CONNECTION, code="connection")
        if completed.returncode and write:
            raise HerdrError(UNCERTAIN, uncertain=True, code=TRANSPORT)
        raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")

    def verify_support(self) -> None:
        if self._supported:
            return
        if not sys.platform.startswith("linux"):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        version = self._exec(["--version"])
        match = re.match(r"\s*herdr v?(\d+)\.(\d+)\.(\d+)(?:[-+][\w.+-]+)?(?=\s|$)", version.stdout)
        if version.returncode or not match or tuple(map(int, match.groups())) < (0, 9, 3):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        schema = self._exec(["api", "schema", "--json"])
        try:
            valid = _schema_supported(json.loads(schema.stdout)["schemas"]["request"])
        except (ValueError, TypeError, KeyError, RecursionError, AttributeError):
            valid = False
        if schema.returncode or not valid:
            raise HerdrError(UNSUPPORTED, code="unsupported")
        self._supported = True

    def _record(self, argv: list[str], kind: str, key: str, *, write: bool = False) -> dict:
        result = self._json(argv, write=write)
        record = result.get(key)
        if result.get("type") != kind or not isinstance(record, dict):
            raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        return self._check_record(record, write)

    def _check_record(self, record: dict, write: bool = False, *, lenient_session: bool = False) -> dict:
        for field in ("pane_id", "terminal_id", "workspace_id", "tab_id"):
            if not isinstance(record.get(field), str) or not record[field] or not record[field].isascii() or any(c.isspace() or ord(c) < 32 for c in record[field]):
                raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        for field in ("name", "label", "agent"):
            if record.get(field) is not None and not isinstance(record[field], str):
                raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        session = None if lenient_session else record.get("agent_session")
        if session is not None and (not isinstance(session, dict) or any(not isinstance(session.get(k), str) or not session[k] for k in ("agent", "source", "kind", "value"))):
            raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        if session is not None and session["kind"] not in {"id", "path"}:
            raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        return record

    def _records(self, argv: list[str], kind: str, key: str) -> list[dict]:
        result = self._json(argv)
        records = result.get(key)
        if result.get("type") != kind or not isinstance(records, list):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        return [self._check_record(record, lenient_session=True) if isinstance(record, dict) else self._reject() for record in records]

    @staticmethod
    def _reject() -> dict:
        raise HerdrError(UNSUPPORTED, code="unsupported")

    def pane_get(self, pane_id: str) -> dict:
        """Canonical pane record; the pane need not host an agent."""
        return self._record(["pane", "get", pane_id], "pane_info", "pane")

    def pane_list(self, workspace_id: str) -> list[dict]:
        """Every pane of every tab of one workspace, in Herdr's tab/layout order."""
        return self._records(["pane", "list", "--workspace", workspace_id], "pane_list", "panes")

    def agent_list(self) -> list[dict]:
        """Every agent record on the server (all workspaces and tabs)."""
        return self._records(["agent", "list"], "agent_list", "agents")

    def tab_list(self, workspace_id: str) -> list[dict]:
        """Tab display records (best effort for the UI; ids and labels only)."""
        result = self._json(["tab", "list", "--workspace", workspace_id])
        tabs = result.get("tabs")
        if result.get("type") != "tab_list" or not isinstance(tabs, list):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        if any(not isinstance(t, dict) or not isinstance(t.get("tab_id"), str) for t in tabs):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        return tabs

    def workspace_label(self, workspace_id: str) -> str | None:
        """Display label of one workspace, or ``None`` when it cannot be read."""
        result = self._json(["workspace", "list"])
        items = result.get("workspaces")
        if result.get("type") != "workspace_list" or not isinstance(items, list):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        for item in items:
            if isinstance(item, dict) and item.get("workspace_id") == workspace_id and isinstance(item.get("label"), str):
                return item["label"]
        return None

    def get_snapshot(self, invocation: Invocation) -> Snapshot:
        pane = self._record(["pane", "get", invocation.pane_id], "pane_info", "pane")
        agent = self._record(["agent", "get", invocation.pane_id], "agent_info", "agent")
        if not agent.get("agent"):
            raise HerdrError("No running agent was found in this pane.", code="no_agent")
        if any(agent[k] != pane[k] for k in ("pane_id", "terminal_id", "workspace_id", "tab_id")) or agent.get("agent_session") != pane.get("agent_session") or agent.get("agent") != pane.get("agent"):
            raise HerdrError("The target agent has changed. Reopen the dialog.", code="target_changed")
        result = self._json(["pane", "process-info", "--pane", invocation.pane_id])
        process = result.get("process_info")
        if result.get("type") != "pane_process_info" or not isinstance(process, dict) or process.get("pane_id") != pane["pane_id"]:
            raise HerdrError(UNSUPPORTED, code="unsupported")
        for key in ("shell_pid", "foreground_process_group_id"):
            value = process.get(key)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise HerdrError(UNSUPPORTED, code="unsupported")
        if process.get("tty") is not None and not isinstance(process["tty"], str):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        processes = process.get("foreground_processes")
        if processes is not None:
            if not isinstance(processes, list):
                raise HerdrError(UNSUPPORTED, code="unsupported")
            for item in processes:
                if not isinstance(item, dict) or not isinstance(item.get("pid"), int) or isinstance(item.get("pid"), bool) or item["pid"] <= 0 or not isinstance(item.get("name"), str):
                    raise HerdrError(UNSUPPORTED, code="unsupported")
                for field in ("argv0", "cmdline", "cwd"):
                    if item.get(field) is not None and not isinstance(item[field], str):
                        raise HerdrError(UNSUPPORTED, code="unsupported")
                if item.get("argv") is not None and (not isinstance(item["argv"], list) or any(not isinstance(part, str) for part in item["argv"])):
                    raise HerdrError(UNSUPPORTED, code="unsupported")
        return Snapshot(agent, pane, process)

    def rename(self, pane_id: str, name: str) -> dict:
        # A valid name starts with a letter, so it can never be read as a CLI flag.
        problem = validate_name(name)
        if problem:
            raise HerdrError(problem, code="invalid_name")
        return self._record(["agent", "rename", pane_id, name], "agent_info", "agent", write=True)

    def clear_name(self, pane_id: str) -> dict:
        """Remove the explicit agent name (``herdr agent rename <pane> --clear``)."""
        return self._record(["agent", "rename", pane_id, "--clear"], "agent_info", "agent", write=True)

    def open_popup(self, invocation: Invocation, mode: str, handoff: str) -> None:
        argv = ["plugin", "pane", "open", "--plugin", "local.agent-rename", "--entrypoint",
                "rename" if mode == "open" else "sync", "--focus", "--env", "HERDR_AGENT_RENAME_HANDOFF=" + handoff]
        # Herdr 0.9.3 rejects location overrides for popup/overlay placement.
        # Popup focus context is independent of the rename target in the handoff.
        result = self._json(argv)
        # Popup resources have no pane id; 0.9.3 returns the public `ok` result.
        if result.get("type") not in {"ok", "plugin_pane_opened"}:
            raise HerdrError(UNSUPPORTED, code="unsupported")

    def notify(self, title: str) -> None:
        result = self._json(["notification", "show", title])
        if result.get("type") != "notification_show":
            raise HerdrError(UNSUPPORTED, code="unsupported")
