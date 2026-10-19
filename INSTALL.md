# Install or uninstall Herdr Agent Rename

This is the entry point for both people and coding agents. From a stable checkout, one command installs dependencies, registers this local plugin, sets up its shortcuts, validates the config, reloads the running server, and verifies action discovery.

You can ask an agent:

> Read `/absolute/path/to/herdr-rename-agent/INSTALL.md` and install the plugin in my current Herdr environment.

For removal:

> Read `/absolute/path/to/herdr-rename-agent/INSTALL.md` and uninstall the plugin, preserving configuration changes made after installation.

## Install

Requirements: Linux, Herdr 0.9.3 or newer with a running target server, Python 3.11+, and `uv` or working Python `venv`/`pip`. The verified baseline is Herdr 0.9.3; the plugin was also verified on Herdr 0.9.5 (2026-10-19, see [verification results](docs/verification-results.md)). Keep this checkout in place while linked.

```bash
cd /absolute/path/to/herdr-rename-agent
bin/install --dry-run
bin/install
```

The first command shows the scoped configuration edit. The second performs it. Dependencies are prepared automatically if missing. The installed shortcuts are `prefix+shift+a` (rename the focused pane's agent) and `prefix+shift+s` (preview and confirm syncing explicit pane names to agent names across the current workspace), normally Ctrl+B followed by Shift+A or Shift+S. Installation does not rename an agent or submit input to one. Dependencies (`prompt_toolkit`, `wcwidth`, `regex`, `tomlkit`) come from the hash-locked `requirements.lock`.

An existing matching plugin action is kept, including a user-customized shortcut. Matching is by `type = "plugin_action"` and the exact `command`, not by description, so bindings written by an earlier version (with the old sync description) are kept unchanged and not duplicated. Repeated installation adds no duplicate entries and retains the original displaced-binding record. The exact legacy `printf/read/herdr agent rename "$HERDR_ACTIVE_PANE_ID"` popup documented for this project is migrated automatically. Its former binding is saved individually for uninstall.

An unrelated custom command on an equivalent shortcut stops installation before configuration/registry edits. To explicitly replace the displayed custom binding:

```bash
bin/install --replace-conflicts --dry-run
bin/install --replace-conflicts
```

For key arrays, only conflicting keys are removed from the former command; its other shortcuts remain active. Conflicts with built-in `[keys]` bindings are reported and must be resolved explicitly. `prefix+A`, `prefix+Shift+a`, and `prefix+shift+a` are equivalent; likewise S.

## Update an existing install

For an install made from an earlier version of this checkout (for example `v0.1.0`):

```bash
cd /absolute/path/to/herdr-rename-agent
git pull            # or: git checkout <ref>
bin/setup           # refreshes .venv for new locked dependencies (regex)
bin/install --dry-run
bin/install         # keeps existing bindings, validates and reloads the config
```

The plugin ID and action IDs are unchanged, so bindings and the recovery record stay valid and no migration is needed. The plugin is linked to this checkout, so what applies when depends on the kind of change:

- **Applies immediately (next shortcut):** runtime Python code under `src/` and `bin/`. Without `bin/setup` first, the new dialogs fail to import the new `regex` dependency.
- **Applies only after a config reload:** changes to `herdr-plugin.toml` (rename popup 57x7, sync popup `"80%"` x `"70%"`, action titles and descriptions, new entrypoints). `bin/install` validates and reloads the config; if you edit the checkout without running it, reload manually (`herdr server reload-config` or the UI's reload action).
- `bin/install` is the deployment step: it reuses the existing bindings, retains the recovery record, and verifies the actions.

If this checkout is already linked in a live Herdr, treat it as live: develop further changes in a separate `git worktree` so that this checkout stays stable.

## Roll back

```bash
cd /absolute/path/to/herdr-rename-agent
git checkout v0.1.0
bin/setup
bin/install
```

`v0.1.0` is the last single-pane release (the current version is 0.2.0). The bindings remain valid; after the checkout, run a config reload (done by `bin/install`) so the old manifest's popup sizes apply. In the sandbox, `v0.1.0` cannot be installed over a registration from a different checkout; roll back in the same checkout as shown. Return with `git checkout master && bin/setup && bin/install`.

## Uninstall

```bash
cd /absolute/path/to/herdr-rename-agent
bin/uninstall --dry-run
bin/uninstall
```

The command finds all `[[keys.command]]` entries with `type = "plugin_action"` and an exact `command` of `local.agent-rename.open` or `local.agent-rename.sync`. It removes those entries even if their shortcut, description, or position changed. It restores previously displaced bindings where their shortcuts are free, unlinks this local plugin, reloads configuration, and verifies the registry no longer contains it. Repeated uninstall is harmless.

**Uninstall never copies an old config over the current file.** Theme changes, new settings, other plugins, new user bindings, and standalone comments survive. Restoration operates on saved individual bindings. If a user assigned a displaced shortcut to another action after installation, that newer binding wins. Free keys are restored individually; blocked keys remain in a private recovery record and its path is printed. You can rerun uninstall after freeing that shortcut, or inspect the record and restore only the selected binding manually.

Resolved recovery records are removed. The checkout, `.venv`, and Herdr's plugin config/state directories remain; no unrelated files or active agent processes are deleted. Small lock files may remain. Finish uninstall before removing the checkout. With the checkout retained, reinstall is simply `bin/install`.

If someone installed the bindings manually, or deleted the recovery record, uninstall can still remove the plugin-owned action entries. Former displaced bindings cannot be reconstructed without their record.

## Target configuration and recovery

Both commands honor `HERDR_CONFIG_PATH`, otherwise `XDG_CONFIG_HOME/herdr/config.toml`, otherwise `~/.config/herdr/config.toml`. A config symlink is edited through its resolved target. For another config or named session, pass the same options to install and uninstall:

```bash
bin/install --config /path/to/config.toml --session my-session
bin/uninstall --config /path/to/config.toml --session my-session
```

`--config` selects the config file, not a different plugin registry; Herdr registry/state roots still follow the environment's XDG paths. `--session` selects the running server. Keep the intended environment consistent. A plugin ID registered from another source/checkout is reported rather than silently relinked.

Recovery records are mode 0600 JSON files under `${XDG_STATE_HOME:-~/.local/state}/herdr/plugins/local.agent-rename/installations/`, identified by a hash of the canonical config path. They contain only displaced command tables and a small ownership record, not a whole configuration backup. Preserve these files until uninstall has completed or blocked restorations have been resolved.

Normal operations validate the candidate with `herdr config check` before applying it. Writes preserve existing config permissions and use a temporary file plus an atomic rename. An advisory lock serializes these helpers; a contents check refuses to overwrite a detected concurrent editor change. Interrupted/failed operations report incomplete status. Keep the scoped record, fix the reported issue, and rerun the same command; the record allows selective cleanup without restoring the whole file.

For offline configuration cleanup, without contacting Herdr or changing the registry:

```bash
bin/uninstall --config-only
```

This removes bindings and performs scoped restoration. It does **not** unregister the plugin or reload a server. Once the target server is available, run normal `bin/uninstall` to finish. Inline `keys` tables or inline arrays of command objects are left untouched with a diagnostic; the supported editing layout is `[keys]` and `[[keys.command]]`.

## Instructions for an agent following this document

1. Resolve this document's checkout directory. Read the current target config, `herdr --version`, and `herdr plugin list --json`. Use the user's active environment/session; preserve other settings and agents.
2. Run `bin/install --dry-run` or `bin/uninstall --dry-run` from this checkout. Execute the corresponding normal command when the edit matches the requested scope. A request to install authorizes the two bindings and the known legacy-popup migration described above.
3. If a collision concerns an unrelated action, explain the specific collision and obtain the user's binding preference unless the task already authorizes replacing it. Use `--replace-conflicts` only for that intended replacement. Do not solve it by overwriting the whole config, deleting other bindings, or resetting keys.
4. Check the command's exit status. For install, confirm `herdr plugin action list --plugin local.agent-rename` exposes open and sync, and the current config contains the expected actions. For uninstall, confirm the action entries are gone and `herdr plugin list --json` no longer registers this plugin.
5. Report installation/removal, actual shortcut/prefix, config path, and any blocked restoration record. A `--config-only` run or failed reload is partial work; finish the remaining registry/reload step when possible. Do not launch a rename or change any agent name merely to verify installation.

## Reference patterns

The one-command, idempotent local setup is informed by [herdr-agent-manager's installer](https://github.com/bleedingfight/herdr-agent-manager/blob/main/install.sh). The local `link`/`unlink` lifecycle follows [Herdr Dev Servers](https://github.com/carellano/herdr-dev-servers/blob/main/README.md) and the [official Herdr plugin documentation](https://github.com/herdrdev/herdr/blob/master/docs/versions/0.9.3/website/src/content/docs/plugins.mdx). This project's additional contract is selective configuration restoration with tests for intervening user edits. See [verification results](docs/verification-results.md).
