from argparse import Namespace
from copy import deepcopy
import json
from pathlib import Path

import pytest
import tomlkit

from herdr_agent_rename import installation as m


def command(key="prefix+A", action="previous", kind="popup"):
    table = tomlkit.table()
    table.update(key=key, type=kind, description="User command", command=action)
    return "[[keys.command]]\n" + table.as_string()


def state():
    return m.empty_state(Path("/test/config.toml"))


def commands(text):
    return tomlkit.parse(text).get("keys", {}).get("command", [])


def test_uninstall_preserves_edits_made_since_install_and_restores_only_legacy():
    original = '# Header to keep\n[theme]\nname = "dracula" # theme comment\n\n' + command(action=m.LEGACY_COMMAND)
    installed = m.install_config(original, state())
    changed = installed.text.replace('"dracula"', '"nord"')
    changed += '\n# Newly added settings\n[ui]\nsidebar_width = 33\n\n'
    changed += command(key="prefix+alt+y", action="echo user", kind="shell")
    removed = m.uninstall_config(changed, installed.state)
    doc = tomlkit.parse(removed.text)
    assert doc["theme"]["name"] == "nord"
    assert doc["ui"]["sidebar_width"] == 33
    assert '# Header to keep\n' in removed.text
    assert 'name = "nord" # theme comment\n' in removed.text
    assert '# Newly added settings\n[ui]\nsidebar_width = 33\n' in removed.text
    assert {table["command"] for table in commands(removed.text)} == {m.LEGACY_COMMAND, "echo user"}
    assert removed.state["displaced"] == []
    assert "[theme]" not in json.dumps(installed.state)  # journal holds displaced tables, not a full backup


def test_repeated_install_and_uninstall_are_idempotent():
    first = m.install_config(command(action=m.LEGACY_COMMAND), state())
    second = m.install_config(first.text, first.state)
    assert second == m.Edit(first.text, first.state, second.messages)
    assert len(commands(second.text)) == 2 and len(second.state["displaced"]) == 1
    removed = m.uninstall_config(second.text, second.state)
    repeated = m.uninstall_config(removed.text, removed.state)
    assert repeated.text == removed.text and repeated.state == removed.state


@pytest.mark.parametrize("alias", ["prefix+A", "prefix+Shift+a", "prefix+ a + SHIFT", "prefix+shift+A", "prefix+S", "prefix+Shift+s"])
def test_alias_conflicts_require_opt_in_and_restore_previous_command(alias):
    original = command(key=alias)
    with pytest.raises(m.InstallError, match="Shortcut conflict"):
        m.install_config(original, state())
    installed = m.install_config(original, state(), replace_conflicts=True)
    removed = m.uninstall_config(installed.text, installed.state)
    assert commands(removed.text)[0].unwrap() == commands(original)[0].unwrap()


def test_new_user_binding_wins_over_original_restoration():
    installed = m.install_config(command(action=m.LEGACY_COMMAND), state())
    changed = installed.text.replace('command = "local.agent-rename.open"', 'command = "other.plugin.open"')
    removed = m.uninstall_config(changed, installed.state)
    assert [item["command"] for item in commands(removed.text)] == ["other.plugin.open"]
    assert len(removed.state["displaced"]) == 1
    assert m.saved_table(removed.state["displaced"][0])["command"] == m.LEGACY_COMMAND
    assert any("Keep user's current" in message for message in removed.messages)


def test_removes_owned_bindings_at_custom_keys_and_after_description_edit():
    original = command(key="ctrl+alt+q", action=m.PLUGIN + ".open", kind="plugin_action")
    original += command(key=["prefix+S", "ctrl+alt+x"], action=m.PLUGIN + ".sync", kind="plugin_action")
    original += command(key="prefix+A", action="other.plugin.open", kind="plugin_action")
    removed = m.uninstall_config(original, state())
    assert [item["command"] for item in commands(removed.text)] == ["other.plugin.open"]


def test_action_id_in_shell_string_is_not_owned():
    original = command(action=m.PLUGIN + ".open", kind="shell")
    assert m.uninstall_config(original, state()).text == original


def test_array_conflict_keeps_unaffected_shortcuts_during_install():
    original = command(key=["prefix+A", "prefix+alt+x"], kind="shell")
    installed = m.install_config(original, state(), replace_conflicts=True)
    other = next(item for item in commands(installed.text) if item["command"] == "previous")
    assert other["key"] == ["prefix+alt+x"]
    removed = m.uninstall_config(installed.text, installed.state)
    assert {key for item in commands(removed.text) for key in m.key_values(item["key"])} == {"prefix+A", "prefix+alt+x"}


def test_partial_restore_leaves_occupied_array_slot_untouched():
    installed = m.install_config(command(key=["prefix+A", "prefix+S"]), state(), replace_conflicts=True)
    changed = installed.text + command(key="prefix+S", action="new user action", kind="shell")
    removed = m.uninstall_config(changed, installed.state)
    assert {item["command"]: m.key_values(item["key"]) for item in commands(removed.text)} == {
        "previous": ["prefix+A"], "new user action": ["prefix+S"]}
    assert m.key_values(m.saved_table(removed.state["displaced"][0])["key"]) == ["prefix+S"]


@pytest.mark.parametrize("value", ['"prefix+A"', '["prefix+alt+x", "prefix+Shift+a"]'])
def test_builtin_conflict_is_not_silently_replaced(value):
    with pytest.raises(m.InstallError, match="Built-in"):
        m.install_config(f"[keys]\nhelp={value}\n", state(), replace_conflicts=True)


def test_user_changes_to_builtin_keys_are_preserved_on_uninstall():
    installed = m.install_config(command(action=m.LEGACY_COMMAND), state())
    changed = '[keys]\nhelp="prefix+A"\nprefix="ctrl+g"\n' + installed.text
    removed = m.uninstall_config(changed, installed.state)
    assert tomlkit.parse(removed.text)["keys"]["help"] == "prefix+A"
    assert tomlkit.parse(removed.text)["keys"]["prefix"] == "ctrl+g"
    assert removed.state["displaced"]


def test_existing_user_customization_of_our_shortcut_is_not_reset():
    original = command(key="ctrl+alt+x", action=m.PLUGIN + ".open", kind="plugin_action")
    installed = m.install_config(original, state())
    assert commands(installed.text)[0]["key"] == "ctrl+alt+x"
    assert len(commands(installed.text)) == 2


def test_missing_install_record_can_still_remove_manual_bindings():
    installed = m.install_config('[theme]\nname="nord"\n', state())
    removed = m.uninstall_config(installed.text, state())
    assert not commands(removed.text) and tomlkit.parse(removed.text)["theme"]["name"] == "nord"


@pytest.mark.parametrize("text", ['[keys]\ncommand=[]\n', 'keys={ command=[] }\n', 'keys={prefix="ctrl+b"}\n', 'not valid toml'])
def test_unsupported_layout_or_invalid_toml_fails_without_a_partial_edit(text):
    with pytest.raises(m.InstallError):
        m.install_config(text, state())


def test_no_implicit_keys_table_left_after_clean_uninstall():
    original = '# Header\n[theme]\nname="nord"\n'
    installed = m.install_config(original, state())
    removed = m.uninstall_config(installed.text, installed.state)
    assert tomlkit.parse(removed.text).unwrap() == tomlkit.parse(original).unwrap()


def test_user_comments_in_created_keys_table_survive_removal():
    installed = m.install_config('', state())
    changed = '[keys]\n# Keep this user note\n' + installed.text
    removed = m.uninstall_config(changed, installed.state)
    assert '# Keep this user note' in removed.text


def test_multiline_header_lookalikes_quoted_keys_bom_and_crlf_survive():
    original = '\ufeff[theme]\r\nname="nord"\r\n[ui]\r\ntext="""\r\n[[keys.command]]\r\ncommand="local.agent-rename.open"\r\n"""\r\n[["keys"."command"]]\r\nkey="prefix+z"\r\ntype="shell"\r\ncommand="echo user"\r\n'
    installed = m.install_config(original, state())
    removed = m.uninstall_config(installed.text, installed.state)
    assert removed.text.startswith('\ufeff')
    assert '\n' not in removed.text.replace('\r\n', '')
    assert tomlkit.parse(removed.text.removeprefix('\ufeff')).unwrap() == tomlkit.parse(original.removeprefix('\ufeff')).unwrap()


def test_atomic_write_preserves_permissions_and_rejects_concurrent_edits(tmp_path):
    config = tmp_path / 'config.toml'
    config.write_bytes(b'old')
    config.chmod(0o664)
    m.atomic_write(config, b'new', b'old')
    assert config.stat().st_mode & 0o777 == 0o664
    config.write_bytes(b'user edited')
    with pytest.raises(m.InstallError, match="changed during"):
        m.atomic_write(config, b'discard user edit', b'new')
    assert config.read_bytes() == b'user edited'
    assert not list(tmp_path.glob('.agent-rename-*'))


def test_paths_respect_override_xdg_and_config_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.delenv('HERDR_CONFIG_PATH', raising=False)
    default = (tmp_path / 'config/herdr/config.toml').resolve()
    assert m.config_path(None) == default
    default.parent.mkdir(parents=True)
    default.touch()
    link = tmp_path / 'linked.toml'
    link.symlink_to(default)
    monkeypatch.setenv('HERDR_CONFIG_PATH', str(link))
    assert m.config_path(None) == default
    assert m.state_path(default) != m.state_path(tmp_path / 'other.toml')
    assert str(m.state_path(default)).startswith(str(tmp_path / 'state/herdr/plugins/local.agent-rename'))


@pytest.mark.parametrize('corruption', ['mode', 'symlink', 'version', 'config', 'table'])
def test_invalid_private_recovery_record_is_not_used(tmp_path, corruption):
    config = tmp_path / 'config.toml'
    path = tmp_path / 'record.json'
    data = m.empty_state(config)
    if corruption == 'version': data['version'] = 2
    if corruption == 'config': data['config'] = '/other/config.toml'
    if corruption == 'table': data['displaced'] = [{'table': 'key="prefix+A"\ncommand="bad"\n[theme]\nname="bad"\n'}]
    path.write_text(json.dumps(data))
    path.chmod(0o600 if corruption != 'mode' else 0o644)
    if corruption == 'symlink':
        alias = tmp_path / 'alias'
        alias.symlink_to(path)
        path = alias
    with pytest.raises(m.InstallError):
        m.load_state(path, config)


class FakeHerdr:
    def __init__(self, config, session):
        self.config = config
        self.session = session

    registered = None
    fail = None
    calls = []

    def installed(self):
        if self.fail == 'connect': raise m.InstallError('unavailable')
        return self.registered

    def validate(self, text, directory):
        if self.fail == 'validate': raise m.InstallError('invalid config')

    def run(self, *args):
        self.calls.append(args)
        if self.fail == args[0]: raise m.InstallError('injected failure')
        if args[:2] == ('plugin', 'link'):
            type(self).registered = {'plugin_root': str(m.ROOT), 'source': {'kind': 'local'}, 'enabled': True}
        if args[:2] == ('plugin', 'unlink'):
            type(self).registered = None
        if args[:3] == ('plugin', 'action', 'list'):
            return json.dumps({'result': {'actions': [{'plugin_id': m.PLUGIN, 'action_id': action.removeprefix(m.PLUGIN + '.')} for action in m.BINDINGS]}})
        return ''


@pytest.fixture
def managed(tmp_path, monkeypatch):
    config = tmp_path / 'config.toml'
    config.write_text('[theme]\nname="dracula"\n' + command(action=m.LEGACY_COMMAND))
    config.chmod(0o664)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(m, 'Herdr', FakeHerdr)
    FakeHerdr.registered, FakeHerdr.fail, FakeHerdr.calls = None, None, []
    args = Namespace(operation='install', config=str(config), session='test', dry_run=False,
                     replace_conflicts=False, config_only=False)
    return config, args


def test_managed_install_uninstall_keeps_user_edits_and_cleans_record(managed):
    config, args = managed
    assert m.perform(args) == 0
    config.write_text(config.read_text().replace('"dracula"', '"nord"') + '\n[ui]\nsidebar_width=33\n')
    args.operation = 'uninstall'
    assert m.perform(args) == 0
    assert FakeHerdr.registered is None
    assert not m.state_path(config).exists()
    assert tomlkit.parse(config.read_text())['theme']['name'] == 'nord'
    assert tomlkit.parse(config.read_text())['ui']['sidebar_width'] == 33
    assert config.stat().st_mode & 0o777 == 0o664
    assert ('server', 'reload-config') in FakeHerdr.calls


def test_preview_does_not_create_records_or_change_registry(managed):
    config, args = managed
    before = config.read_bytes()
    args.dry_run = True
    assert m.perform(args) == 0
    assert config.read_bytes() == before
    assert not m.state_path(config).parent.exists()
    assert FakeHerdr.calls == []


@pytest.mark.parametrize('failure', ['connect', 'validate'])
def test_preflight_failure_has_no_config_or_record_mutation(managed, failure):
    config, args = managed
    before = config.read_bytes()
    FakeHerdr.fail = failure
    with pytest.raises(m.InstallError): m.perform(args)
    assert config.read_bytes() == before
    assert not m.state_path(config).exists()


def test_registration_failure_leaves_original_config_and_recoverable_table(managed):
    config, args = managed
    before = config.read_bytes()
    FakeHerdr.fail = 'plugin'
    with pytest.raises(m.InstallError): m.perform(args)
    assert config.read_bytes() == before
    record, _ = m.load_state(m.state_path(config), config)
    assert len(record['displaced']) == 1
    args.operation, args.config_only = 'uninstall', True
    assert m.perform(args) == 0
    assert config.read_bytes() == before and not m.state_path(config).exists()


def test_retry_after_failed_registration_does_not_duplicate_recovery_record(managed):
    config, args = managed
    FakeHerdr.fail = 'plugin'
    with pytest.raises(m.InstallError): m.perform(args)
    FakeHerdr.fail = None
    assert m.perform(args) == 0
    record, _ = m.load_state(m.state_path(config), config)
    assert len(record['displaced']) == 1


def test_comments_after_legacy_binding_stay_in_live_config_without_recovery_duplicates():
    original = command(action=m.LEGACY_COMMAND) + '# Keep my next settings comment\n[ui]\nsidebar_width=31\n'
    installed = m.install_config(original, state())
    assert installed.text.count('# Keep my next settings comment') == 1
    removed = m.uninstall_config(installed.text, installed.state)
    assert removed.text.count('# Keep my next settings comment') == 1
    assert tomlkit.parse(removed.text)['ui']['sidebar_width'] == 31


def test_failed_reload_is_recoverable_without_whole_file_restore(managed):
    config, args = managed
    FakeHerdr.fail = 'server'
    with pytest.raises(m.InstallError): m.perform(args)
    assert m.state_path(config).exists()
    config.write_text(config.read_text().replace('"dracula"', '"nord"'))
    args.operation, args.config_only = 'uninstall', True
    assert m.perform(args) == 0
    assert tomlkit.parse(config.read_text())['theme']['name'] == 'nord'
    assert [entry['command'] for entry in commands(config.read_text())] == [m.LEGACY_COMMAND]


def test_existing_other_checkout_is_not_relinked(managed):
    config, args = managed
    before = config.read_bytes()
    FakeHerdr.registered = {'plugin_root': '/different/checkout', 'source': {'kind': 'local'}}
    with pytest.raises(m.InstallError, match='another checkout'): m.perform(args)
    assert config.read_bytes() == before and not m.state_path(config).exists()
