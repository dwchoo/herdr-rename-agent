"""Bounded public CLI calls, with literal argv and strict response envelopes."""

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from typing import Mapping

from .models import HerdrError, Invocation, Snapshot

UNSUPPORTED = "This Herdr version is not supported."
CONNECTION = "Could not connect to Herdr."
UNCERTAIN = "The rename result could not be confirmed. Check the current name before retrying."


def sanitize(text: str) -> str:
    return "".join(c if not unicodedata.category(c).startswith("C") else " " for c in text)[:300]


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
        except subprocess.TimeoutExpired:
            raise HerdrError(UNCERTAIN if write else CONNECTION, uncertain=write) from None
        except OSError:
            raise HerdrError(UNCERTAIN if write else CONNECTION, uncertain=write) from None

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
            raise HerdrError(CONNECTION)
        raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")

    def verify_support(self) -> None:
        if self._supported:
            return
        if not sys.platform.startswith("linux"):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        version = self._exec(["--version"])
        match = re.fullmatch(r"herdr (\d+)\.(\d+)\.(\d+)(?:[-+][\w.-]+)?\s*", version.stdout)
        if version.returncode or not match or tuple(map(int, match.groups())) < (0, 9, 3):
            raise HerdrError(UNSUPPORTED, code="unsupported")
        schema = self._exec(["api", "schema", "--json"])
        try:
            raw = json.loads(schema.stdout)["schemas"]
            req = raw["request"]
            methods = {item["properties"]["method"]["const"] for item in req["oneOf"]}
            definitions = req["$defs"]
            required = {"agent.get", "agent.rename", "pane.get", "pane.process_info", "plugin.pane.open", "notification.show"}
            valid = required <= methods and "popup" in definitions["PluginPanePlacement"]["enum"]
            for definition, fields in (
                ("AgentRenameParams", {"target", "name"}),
                ("PluginPaneOpenParams", {"env", "target_pane_id", "workspace_id"}),
                ("PluginInvocationContext", {"focused_pane_id", "workspace_id", "tab_id"}),
            ):
                properties = definitions[definition]["properties"]
                valid = valid and isinstance(properties, dict) and fields <= properties.keys()
        except (ValueError, TypeError, KeyError, RecursionError):
            valid = False
        if schema.returncode or not valid:
            raise HerdrError(UNSUPPORTED, code="unsupported")
        self._supported = True

    def _record(self, argv: list[str], kind: str, key: str, *, write: bool = False) -> dict:
        result = self._json(argv, write=write)
        record = result.get(key)
        if result.get("type") != kind or not isinstance(record, dict):
            raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        for field in ("pane_id", "terminal_id", "workspace_id", "tab_id"):
            if not isinstance(record.get(field), str) or not record[field] or not record[field].isascii() or any(c.isspace() or ord(c) < 32 for c in record[field]):
                raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        for field in ("name", "label", "agent"):
            if record.get(field) is not None and not isinstance(record[field], str):
                raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        session = record.get("agent_session")
        if session is not None and (not isinstance(session, dict) or any(not isinstance(session.get(k), str) or not session[k] for k in ("agent", "source", "kind", "value"))):
            raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        if session is not None and session["kind"] not in {"id", "path"}:
            raise HerdrError(UNCERTAIN if write else UNSUPPORTED, uncertain=write, code="unconfirmed" if write else "unsupported")
        return record

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
        return self._record(["agent", "rename", pane_id, name], "agent_info", "agent", write=True)

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
