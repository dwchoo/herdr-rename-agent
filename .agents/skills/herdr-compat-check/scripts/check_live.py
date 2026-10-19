#!/usr/bin/env python3
"""Read-only compatibility check of this plugin against the installed Herdr.

Covers versions, live install state, schema drift and CLI help drift. It only
runs `herdr` query commands and reads ~/.config/herdr files; it never writes
there. Output goes to --out (default: a fresh temp dir). Exit 1 on any FAIL.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib


def _find_root():
    for d in Path(__file__).resolve().parents:
        if (d / "herdr-plugin.toml").is_file():
            return d
    raise SystemExit("cannot locate repo root (herdr-plugin.toml) above " + __file__)


ROOT = _find_root()
SKILL = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "herdr"
PLUGIN_ID = "local.agent-rename"
HELP_COMMANDS = [
    "agent rename", "agent get", "agent list", "agent focus", "pane get", "pane list",
    "pane process-info", "plugin pane open", "notification show", "tab list",
    "workspace list", "server reload-config",
]
# Schema definitions the plugin depends on (drift here matters most).
RELEVANT = ("AgentRenameParams", "PluginPaneOpenParams", "PluginInvocationContext",
            "PaneListParams", "PluginPanePlacement")
RESULTS: list[tuple[str, str, str]] = []


def report(level: str, name: str, detail: str = "") -> None:
    RESULTS.append((level, name, detail))
    print(f"[{level}] {name}" + (f": {detail}" if detail else ""))


def herdr(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["herdr", *args], stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=30, check=False)


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def check_versions(baseline: str) -> str:
    out = herdr("--version")
    match = re.search(r"\d+\.\d+\.\d+", out.stdout)
    if out.returncode or not match:
        report("FAIL", "herdr --version", (out.stderr or out.stdout).strip())
        return ""
    installed = match.group()
    status = herdr("status").stdout
    versions = re.findall(r"^\s*version:\s*(\S+)", status, re.M)
    ok = len(set(versions)) == 1 and versions[0] == installed if versions else False
    report("PASS" if ok else "FAIL", "client/server versions agree", ", ".join(versions) or "no status")
    for key in ("endpoint_compatible", "private_protocol_compatible"):
        found = re.search(rf"{key}:\s*(\S+)", status)
        report("PASS" if found and found.group(1) == "yes" else "FAIL", key, found.group(1) if found else "missing")
    for key in ("restart_needed", "server_binary_stale"):
        found = re.search(rf"{key}:\s*(\S+)", status)
        if found and found.group(1) != "no":
            report("WARN", key, found.group(1) + " (restart Herdr yourself; this tool never does)")
    manifest = tomllib.loads((ROOT / "herdr-plugin.toml").read_text())
    minimum = manifest.get("min_herdr_version", "")
    if version_tuple(installed) < version_tuple(minimum):
        report("FAIL", "min_herdr_version", f"installed {installed} < manifest {minimum}")
    delta = "same as" if installed == baseline else "differs from"
    report("PASS" if installed == baseline else "WARN", "baseline", f"installed {installed} {delta} baseline {baseline} (manifest min {minimum})")
    notes = CONFIG / "release-notes.json"
    if notes.is_file():
        try:
            data = json.loads(notes.read_text())
            print(f"--- release notes ({data.get('version')}) ---\n{str(data.get('body', ''))[:2000]}\n---")
        except ValueError:
            report("WARN", "release-notes.json", "unreadable")
    return installed


def check_install() -> None:
    listing = herdr("plugin", "list").stdout
    line = next((l for l in listing.splitlines() if PLUGIN_ID in l), "")
    ok = "enabled" in line and f"[local:{ROOT}]" in line
    report("PASS" if ok else "FAIL", "plugin list", line.strip() or "plugin missing")
    actions = herdr("plugin", "action", "list").stdout
    ids = set(re.findall(r'"action_id":\s*"([^"]+)"', actions))
    report("PASS" if {"open", "sync"} <= ids else "FAIL", "plugin actions", ", ".join(sorted(ids)) or "none")
    try:
        entries = json.loads((CONFIG / "plugins.json").read_text())
        entry = next(e for e in entries if e.get("plugin_id") == PLUGIN_ID)
        ok = entry.get("enabled") is True and Path(entry["manifest_path"]) == ROOT / "herdr-plugin.toml"
        report("PASS" if ok else "FAIL", "plugins.json", f"enabled={entry.get('enabled')} manifest={entry.get('manifest_path')}")
    except (OSError, ValueError, StopIteration, KeyError, TypeError) as error:
        report("FAIL", "plugins.json", repr(error))
    try:
        keys = tomllib.loads((CONFIG / "config.toml").read_text()).get("keys", {}).get("command", [])
        bound = {k.get("command") for k in keys if k.get("type") == "plugin_action"}
        missing = {f"{PLUGIN_ID}.open", f"{PLUGIN_ID}.sync"} - bound
        report("FAIL" if missing else "PASS", "config.toml key bindings", f"missing {sorted(missing)}" if missing else "open + sync bound")
    except (OSError, ValueError) as error:
        report("FAIL", "config.toml", repr(error))


def flatten(node: object, path: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            flat.update(flatten(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            flat.update(flatten(value, f"{path}[{index}]"))
    else:
        flat[path] = json.dumps(node)
    return flat


def check_schema(baseline: str, out: Path) -> None:
    result = herdr("api", "schema", "--json")
    try:
        current = json.loads(result.stdout)
    except ValueError:
        report("FAIL", "api schema", "not JSON")
        return
    (out / "schema-current.json").write_text(json.dumps(current, indent=2, sort_keys=True))
    # Reuse the plugin's own capability check so this never diverges from runtime behaviour.
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from herdr_agent_rename.client import _schema_supported
        supported = _schema_supported(current["schemas"]["request"])
        report("PASS" if supported else "FAIL", "plugin capability check (client._schema_supported)")
    except Exception as error:  # noqa: BLE001 - report, never crash the whole check
        report("FAIL", "plugin capability check", repr(error))
    fixture = ROOT / f"tests/fixtures/schema-{baseline}.json"
    if not fixture.is_file():
        report("WARN", "schema drift", f"no fixture {fixture.name}: unknown")
        return
    old, new = flatten(json.loads(fixture.read_text())), flatten(current)
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k] and k != "/protocol")
    added = sorted(set(new) - set(old))
    relevant = lambda keys: [k for k in keys if any(f"/{name}" in k for name in RELEVANT)]
    report("PASS" if not (removed or changed) else "WARN", "schema drift vs " + fixture.name,
           f"removed={len(removed)} changed={len(changed)} added={len(added)}")
    if relevant(removed + changed):
        report("WARN", "drift in plugin-relevant definitions", "; ".join(relevant(removed + changed)[:10]))
    for label, keys in (("removed", removed), ("changed", changed)):
        for key in keys[:15]:
            print(f"    {label}: {key}")
    (out / "schema-drift.txt").write_text(
        "\n".join([f"removed {k}" for k in removed] + [f"changed {k}" for k in changed] + [f"added {k}" for k in added]))
    print(f"    full drift list: {out / 'schema-drift.txt'} (added keys are usually new features)")


def check_help(installed: str, out: Path, save: bool) -> None:
    snapshots = SKILL / "baseline"
    current = out / "help"
    current.mkdir(exist_ok=True)
    texts = {}
    for command in HELP_COMMANDS:
        result = herdr(*command.split(), "--help")
        texts[command] = result.stdout if result.returncode == 0 else ""
        if result.returncode:
            report("FAIL", f"help: herdr {command}", "command missing or failing")
        (current / (command.replace(" ", "_") + ".txt")).write_text(texts[command])
    stored = snapshots / "VERSION"
    if save:
        snapshots.mkdir(exist_ok=True)
        for command, text in texts.items():
            (snapshots / (command.replace(" ", "_") + ".txt")).write_text(text)
        stored.write_text(installed + "\n")
        report("PASS", "help baseline saved", f"{snapshots} labelled {installed}")
        return
    if not stored.is_file():
        report("WARN", "help drift", "no stored snapshots: unknown (run with --save-baseline after a verified run)")
        return
    for command, text in texts.items():
        path = snapshots / (command.replace(" ", "_") + ".txt")
        if not path.is_file():
            report("WARN", f"help: herdr {command}", "no stored snapshot: unknown")
        elif path.read_text() == text:
            report("PASS", f"help: herdr {command}", "unchanged")
        else:
            diff = difflib.unified_diff(path.read_text().splitlines(), text.splitlines(), "stored", "current", lineterm="", n=0)
            report("WARN", f"help: herdr {command}", f"changed vs {stored.read_text().strip()}")
            for line in list(diff)[:12]:
                print("    " + line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="baseline Herdr version (default: manifest min_herdr_version)")
    parser.add_argument("--out", type=Path, help="directory for schema dump and help snapshots")
    parser.add_argument("--save-baseline", action="store_true", help="rewrite help snapshots from the installed binary")
    args = parser.parse_args()
    manifest = tomllib.loads((ROOT / "herdr-plugin.toml").read_text())
    baseline = args.baseline or manifest["min_herdr_version"]
    out = args.out or Path(tempfile.mkdtemp(prefix="herdr-compat-"))
    out.mkdir(parents=True, exist_ok=True)
    print(f"plugin {manifest['version']} at {ROOT}; artifacts in {out}")
    installed = check_versions(baseline)
    check_install()
    check_schema(baseline, out)
    check_help(installed, out, args.save_baseline)
    counts = {level: sum(1 for r in RESULTS if r[0] == level) for level in ("PASS", "WARN", "FAIL")}
    print(f"\nSUMMARY: {counts} -> " + ("FAIL" if counts["FAIL"] else "WARN (review above)" if counts["WARN"] else "PASS"))
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
