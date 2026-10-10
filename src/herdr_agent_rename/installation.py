"""Install/uninstall scoped keybindings without restoring a whole config file."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib

import tomlkit
from tomlkit.items import AoT, Comment, Table

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = "local.agent-rename"
BINDINGS = {
    PLUGIN + ".open": ("prefix+shift+a", "Rename current agent"),
    PLUGIN + ".sync": ("prefix+shift+s", "Sync agent names from pane names"),
}
LEGACY_COMMAND = ('printf "New agent name: "; IFS= read -r name; if [ -n "$name" ]; '
                  'then herdr agent rename "$HERDR_ACTIVE_PANE_ID" "$name"; fi')


class InstallError(Exception):
    pass


@dataclass
class Edit:
    text: str
    state: dict
    messages: list[str]


def slot(value: object) -> str | None:
    """Recognize aliases of our two chords; do not normalize other bindings."""
    if not isinstance(value, str):
        return None
    parts = [part.strip() for part in value.split("+")]
    if not parts or parts[0].lower() != "prefix":
        return None
    modifiers = set()
    letter = None
    for part in parts[1:]:
        if part.lower() == "shift":
            modifiers.add("shift")
        elif part in {"a", "A", "s", "S"} and letter is None:
            letter = part.lower()
            if part.isupper():
                modifiers.add("shift")
        else:
            return None
    if letter and modifiers == {"shift"}:
        return "prefix+shift+" + letter
    return None


def key_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise InstallError("A command key must be a string or an array of strings.")


def owned(table: Table) -> bool:
    return table.get("type") == "plugin_action" and table.get("command") in BINDINGS


class CommandBlocks:
    """Edit actual AoT fragments, never the proxy's merged (detached) copy."""
    def __init__(self, fragments):
        self.fragments = fragments

    def groups(self):
        return [fragment["command"] for fragment in self.fragments if "command" in fragment]

    def __iter__(self):
        return iter([table for group in self.groups() for table in group])

    def __len__(self):
        return sum(len(group) for group in self.groups())

    def __getitem__(self, index):
        return list(self)[index]

    def __delitem__(self, index):
        target = self[index]
        for group in self.groups():
            for position, table in enumerate(group):
                if table is target:
                    del group[position]
                    return

    def append(self, table):
        self.groups()[-1].append(table)


class KeyBlocks:
    def __init__(self, fragments):
        self.fragments = fragments

    def items(self):
        return ((key, value) for fragment in self.fragments for key, value in fragment.items())

    def __bool__(self):
        return any(bool(fragment) for fragment in self.fragments)

    def add(self, key, value):
        self.fragments[-1].add(key, value)

    def __delitem__(self, key):
        for fragment in self.fragments:
            if key in fragment:
                del fragment[key]


def document(text: str):
    source = text.removeprefix("\ufeff")
    try:
        doc = tomlkit.parse(source)
        if tomlkit.dumps(doc) != source:
            raise ValueError("This TOML layout does not round-trip unchanged")
    except Exception as error:
        raise InstallError(f"Invalid TOML; configuration was not edited: {error}") from error
    fragments = [value for key, value in doc.body if key is not None and key.key == "keys"]
    if any(not isinstance(fragment, Table) for fragment in fragments):
        raise InstallError("Use a [keys] table; inline keys tables are left unchanged.")
    groups = [fragment["command"] for fragment in fragments if "command" in fragment]
    if any(not isinstance(group, AoT) for group in groups):
        raise InstallError("Use [[keys.command]] tables; inline command arrays are left unchanged.")
    return doc, KeyBlocks(fragments) if fragments else None, CommandBlocks(fragments) if groups else None


def has_key_comments(keys) -> bool:
    fragments = keys.fragments if isinstance(keys, KeyBlocks) else [keys]
    return any(fragment.trivia.comment or any(type(item).__name__ == "Comment" for _, item in fragment.value.body)
               for fragment in fragments)


def displaced_copy(table: Table) -> Table:
    """Standalone comments stay in the live file, not in recovery copies."""
    saved = tomlkit.table()
    for key, item in table.value.body:
        if not isinstance(item, Comment):
            saved.add(deepcopy(key), deepcopy(item))
    return saved


def remove_command(keys, commands, index):
    table = commands[index]
    fragments = keys.fragments if isinstance(keys, KeyBlocks) else [keys]
    for fragment in fragments:
        if "command" in fragment and any(item is table for item in fragment["command"]):
            for key, item in table.value.body:
                if key is None and isinstance(item, Comment):
                    fragment.add(deepcopy(item))
            break
    del commands[index]


def finish(doc, original: str) -> str:
    text = tomlkit.dumps(doc)
    # Keep the original newline convention and optional UTF-8 BOM.
    if "\r\n" in original and "\n" not in original.replace("\r\n", ""):
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    if original.startswith("\ufeff"):
        text = "\ufeff" + text
    document(text)
    # Refuse any accidental parser/serialization change outside keys.command.
    normalized = []
    for value in (original, text):
        parsed = tomllib.loads(value.removeprefix("\ufeff"))
        keys = parsed.get("keys", {})
        keys.pop("command", None)
        if not keys:
            parsed.pop("keys", None)
        normalized.append(json.dumps(parsed, sort_keys=True, default=lambda item: item.isoformat()))
    if normalized[0] != normalized[1]:
        raise InstallError("An unrelated setting would change; configuration was not edited.")
    return text


def empty_state(config: Path) -> dict:
    return {"version": 1, "plugin_id": PLUGIN, "config": str(config),
            "keys_created": False, "displaced": []}


def binding(action: str) -> Table:
    key, description = BINDINGS[action]
    table = tomlkit.table()
    for field, value in (("key", key), ("type", "plugin_action"),
                         ("description", description), ("command", action)):
        table.add(field, value)
    return table


def occupied(keys, commands) -> set[str]:
    found = set()
    if commands:
        for table in commands:
            found.update(filter(None, (slot(key) for key in key_values(table.get("key")))))
    if keys:
        for field, value in keys.items():
            if field in {"command", "prefix"} or isinstance(value, dict):
                continue
            if isinstance(value, (str, list)):
                found.update(filter(None, (slot(key) for key in key_values(value))))
    return found


def install_config(text: str, state: dict, *, replace_conflicts=False) -> Edit:
    doc, keys, commands = document(text)
    state = deepcopy(state)
    messages = []
    configured = {table.get("command") for table in (commands or []) if owned(table)}
    needed = {action: values for action, values in BINDINGS.items() if action not in configured}
    desired_slots = {values[0] for values in needed.values()}
    if keys:
        builtin = occupied(keys, None) & desired_slots
        if builtin:
            raise InstallError(f"Built-in key settings conflict with {sorted(builtin)}. Change those settings explicitly first.")
    for index in range(len(commands or []) - 1, -1, -1):
        table = commands[index]
        values = key_values(table.get("key"))
        displaced_keys = [key for key in values if slot(key) in desired_slots]
        if not displaced_keys:
            continue
        legacy = table.get("type") == "popup" and table.get("command") == LEGACY_COMMAND
        if not legacy and not replace_conflicts:
            raise InstallError(f"Shortcut conflict: {displaced_keys}, {table.get('description', table.get('command', 'custom command'))!r}. "
                               "Use --replace-conflicts only if replacing this binding is intended.")
        saved = displaced_copy(table)
        remaining = [key for key in values if slot(key) not in desired_slots]
        if remaining:
            table["key"] = remaining
            saved["key"] = displaced_keys
        else:
            remove_command(keys, commands, index)
        if not any(saved_table(record).unwrap() == saved.unwrap() for record in state["displaced"]):
            state["displaced"].append({"table": saved.as_string()})
        messages.append(f"Save only displaced binding: {displaced_keys} ({'legacy rename popup' if legacy else 'custom command'})")
    if needed:
        if keys is None:
            keys = tomlkit.table(is_super_table=True)
            doc.add("keys", keys)
            state["keys_created"] = True
        if commands is None:
            commands = tomlkit.aot()
            keys.add("command", commands)
        for action in needed:
            commands.append(binding(action))
            messages.append(f"Add {BINDINGS[action][0]} -> {action}")
    for action in configured:
        messages.append(f"Keep existing {action} binding(s)")
    return Edit(finish(doc, text), state, messages)


def saved_table(record: dict) -> Table:
    try:
        parsed = tomlkit.parse("[[keys.command]]\n" + record["table"])
        commands = parsed["keys"]["command"]
        if set(parsed) != {"keys"} or set(parsed["keys"]) != {"command"} or len(commands) != 1:
            raise ValueError("Unexpected saved table")
        table = commands[0]
        if owned(table) or not key_values(table.get("key")):
            raise ValueError("Invalid displaced binding")
        return table
    except Exception as error:
        raise InstallError("Invalid scoped installation record; leave it intact for recovery.") from error


def uninstall_config(text: str, state: dict) -> Edit:
    doc, keys, commands = document(text)
    state = deepcopy(state)
    messages = []
    for index in range(len(commands or []) - 1, -1, -1):
        if owned(commands[index]):
            messages.append(f"Remove plugin binding: {commands[index].get('key')}")
            remove_command(keys, commands, index)
    pending = []
    for record in reversed(state["displaced"]):
        table = saved_table(record)
        if any(existing.unwrap() == table.unwrap() for existing in (commands or [])):
            messages.append("Previous binding is already present; keep it")
            continue
        original_keys = key_values(table["key"])
        conflicts = set(filter(None, (slot(key) for key in original_keys))) & occupied(keys, commands)
        if conflicts:
            blocked = deepcopy(table)
            blocked["key"] = [key for key in original_keys if slot(key) in conflicts]
            pending.append({"table": blocked.as_string()})
            messages.append(f"Keep user's current {sorted(conflicts)} binding; retain previous binding in the scoped recovery record")
            free = [key for key in original_keys if slot(key) not in conflicts]
            if not free:
                continue
            table["key"] = free
        if keys is None:
            keys = tomlkit.table(is_super_table=True)
            doc.add("keys", keys)
        if commands is None:
            commands = tomlkit.aot()
            keys.add("command", commands)
        commands.append(table)
        messages.append(f"Restore previous binding only: {key_values(table['key'])}")
    state["displaced"] = list(reversed(pending))
    if commands is not None and len(commands) == 0:
        del keys["command"]
    if keys is not None and not keys and state["keys_created"] and not has_key_comments(keys):
        del doc["keys"]
    return Edit(finish(doc, text), state, messages or ["No plugin bindings to remove"])


def read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def atomic_write(path: Path, data: bytes, expected: bytes | None, *, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary = tempfile.mkstemp(prefix=".agent-rename-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if read_bytes(path) != expected:
            raise InstallError(f"{path} changed during this operation; newer contents were preserved. Rerun the command.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def config_path(value: str | None) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return Path(value or os.environ.get("HERDR_CONFIG_PATH") or base / "herdr/config.toml").expanduser().resolve()


def state_path(config: Path) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    identity = hashlib.sha256(os.fsencode(config)).hexdigest()
    return base / "herdr/plugins" / PLUGIN / "installations" / (identity + ".json")


def load_state(path: Path, config: Path) -> tuple[dict, bytes | None]:
    raw = read_bytes(path)
    if raw is None:
        return empty_state(config), None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise InstallError(f"Unsafe installation record permissions: {path}")
    try:
        state = json.loads(raw)
        if (state["version"] != 1 or state["plugin_id"] != PLUGIN or state["config"] != str(config)
                or type(state["keys_created"]) is not bool or not isinstance(state["displaced"], list)):
            raise ValueError("Invalid record")
        for record in state["displaced"]:
            saved_table(record)
        return state, raw
    except (ValueError, KeyError, TypeError) as error:
        raise InstallError(f"Invalid installation record: {path}") from error


@contextmanager
def installation_lock(path: Path):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


class Herdr:
    def __init__(self, config: Path, session: str | None):
        self.argv = [os.environ.get("HERDR_BIN_PATH", "herdr")]
        if session:
            self.argv += ["--session", session]
        self.env = dict(os.environ, HERDR_CONFIG_PATH=str(config))

    def run(self, *arguments: str, config: Path | None = None) -> str:
        env = dict(self.env)
        if config:
            env["HERDR_CONFIG_PATH"] = str(config)
        try:
            result = subprocess.run([*self.argv, *arguments], env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InstallError(f"Herdr command failed: {' '.join(arguments)}: {error}") from error
        if result.returncode:
            raise InstallError(f"Herdr command failed: {' '.join(arguments)}: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout

    def installed(self):
        try:
            plugins = json.loads(self.run("plugin", "list", "--json"))["result"]["plugins"]
            return next((plugin for plugin in plugins if plugin["plugin_id"] == PLUGIN), None)
        except (ValueError, KeyError, TypeError) as error:
            raise InstallError("Unexpected Herdr plugin-list response") from error

    def validate(self, text: str, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".agent-rename-check-", suffix=".toml", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            self.run("config", "check", config=Path(name))
        finally:
            os.unlink(name)


def perform(args) -> int:
    config = config_path(args.config)
    record_path = state_path(config)
    herdr = Herdr(config, args.session)

    def prepare():
        before = read_bytes(config)
        text = (before or b"").decode("utf-8")
        state, record_before = load_state(record_path, config)
        if args.operation == "install":
            edit = install_config(text, state, replace_conflicts=args.replace_conflicts)
        else:
            edit = uninstall_config(text, state)
        return before, record_before, edit

    if args.dry_run:
        before, _, edit = prepare()
        print(f"Config: {config}\nScoped recovery record: {record_path}")
        print("\n".join(edit.messages))
        print("".join(difflib.unified_diff((before or b"").decode("utf-8").splitlines(True),
                                        edit.text.splitlines(True), fromfile="current config", tofile=args.operation + " result")), end="")
        print("Dry run: configuration and registry unchanged.")
        return 0
    with installation_lock(record_path):
        before, record_before, edit = prepare()
        registered = None if args.config_only else herdr.installed()
        if args.operation == "install" and registered:
            if Path(registered["plugin_root"]).resolve() != ROOT or registered.get("source", {}).get("kind") != "local":
                raise InstallError("This plugin ID is registered from another checkout/source. Uninstall that installation first.")
        if not args.config_only:
            herdr.validate(edit.text, config.parent)
        if args.operation == "install":
            # Save displaced tables before either registration or config mutation.
            atomic_write(record_path, (json.dumps(edit.state, indent=2) + "\n").encode(), record_before)
            if not args.config_only:
                herdr.run("plugin", "link", str(ROOT), "--enabled")
        if edit.text.encode("utf-8") != (before or b""):
            atomic_write(config, edit.text.encode("utf-8"), before, mode=0o600)
        if args.operation == "uninstall":
            if edit.state["displaced"]:
                atomic_write(record_path, (json.dumps(edit.state, indent=2) + "\n").encode(), record_before)
            elif record_before is not None:
                if read_bytes(record_path) != record_before:
                    raise InstallError("The recovery record changed; leave it intact and rerun uninstall.")
                record_path.unlink()
            if not args.config_only and registered:
                herdr.run("plugin", "unlink", PLUGIN)
        print("\n".join(edit.messages))
        if not args.config_only:
            herdr.run("server", "reload-config")
            current = herdr.installed()
            if args.operation == "install":
                actions = json.loads(herdr.run("plugin", "action", "list", "--plugin", PLUGIN))["result"]["actions"]
                action_ids = {action["action_id"] if action["action_id"].startswith(PLUGIN + ".")
                              else action["plugin_id"] + "." + action["action_id"] for action in actions}
                if not current or not current["enabled"] or not set(BINDINGS) <= action_ids:
                    raise InstallError("Installed configuration could not be verified; inspect the plugin registry and rerun.")
            elif current is not None:
                raise InstallError("The plugin is still registered; rerun uninstall.")
        print(f"{'Installed' if args.operation == 'install' else 'Uninstalled'}{' bindings only' if args.config_only else ''}. Config: {config}")
        if edit.state["displaced"] and args.operation == "uninstall":
            print(f"Previous binding restoration was blocked by newer user bindings. Recovery record: {record_path}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "uninstall"))
    parser.add_argument("--config", help="Config file (default: HERDR_CONFIG_PATH or XDG_CONFIG_HOME/herdr/config.toml)")
    parser.add_argument("--session", help="Target a named running Herdr session")
    parser.add_argument("--dry-run", action="store_true", help="Show the scoped config edit without registering/editing")
    parser.add_argument("--replace-conflicts", action="store_true", help="Allow replacing conflicting custom commands; save only their displaced bindings")
    parser.add_argument("--config-only", action="store_true", help="Edit bindings only, without registry operations/reload; useful for offline cleanup")
    args = parser.parse_args()
    try:
        return perform(args)
    except (InstallError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"{args.operation.capitalize()} incomplete: {error}\nFix the reported issue and rerun, or use bin/uninstall --config-only for scoped config cleanup.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
