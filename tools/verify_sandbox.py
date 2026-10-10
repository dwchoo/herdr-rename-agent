#!/usr/bin/env python3
"""Run the real Herdr PTY checks in a disposable Linux bubblewrap sandbox.

Only system runtime files, the selected binaries, and Python dependencies are
mounted read-only. The checkout is copied, HOME is empty, host sockets are absent,
and network/PID namespaces are separate. No credentials or model prompt are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SANDBOX_ROOT = Path("/workspace/agent-rename")
NEXT_RUN = "docs/next-run"  # in-sandbox evidence directory for the new suite
TIMEOUTS = {"old": 240, "next": 1500, "all": 1800}


def native_codex(value: str | None) -> Path:
    candidate = Path(value or shutil.which("codex") or "").resolve()
    if not candidate.is_file():
        raise RuntimeError("Install Codex or pass --codex-binary with its native ELF executable.")
    with candidate.open("rb") as handle:
        if handle.read(4) == b"\x7fELF":
            return candidate
    # Resolve the installed npm launcher without mounting Node or the host HOME.
    architecture = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine())
    package = candidate.parent.parent
    matches = list(package.glob(f"node_modules/@openai/codex-linux-{architecture}/vendor/*/bin/codex"))
    matches += list(package.glob("vendor/*/bin/codex"))
    if len(matches) != 1:
        raise RuntimeError("Cannot resolve the native Codex binary; pass --codex-binary explicitly.")
    with matches[0].open("rb") as handle:
        if handle.read(4) != b"\x7fELF":
            raise RuntimeError("The selected Codex binary must be a Linux ELF executable.")
    return matches[0].resolve()


def inside(suite: str = "old", only: list[str] | None = None) -> None:
    """Check isolation before starting any Herdr process, then run the selected E2E suite(s)."""
    home = Path(os.environ["HOME"])
    denied = json.loads(os.environ["HERDR_RENAME_SANDBOX_DENIED_PATHS"])
    host_namespaces = json.loads(os.environ["HERDR_RENAME_SANDBOX_HOST_NAMESPACES"])
    namespaces = {name: os.readlink(f"/proc/self/ns/{name}") for name in host_namespaces}
    readonly = {}
    for path in ("/opt/bin/herdr", "/opt/bin/codex", "/usr/bin/true", str(Path(sys.executable).resolve())):
        readonly[path] = {"read_only_mount": bool(os.statvfs(path).f_flag & os.ST_RDONLY)}
        try:
            descriptor = os.open(path, os.O_WRONLY)
        except OSError as error:
            readonly[path].update(write_open_denied=True, errno=error.errno)
        else:
            os.close(descriptor)
            readonly[path].update(write_open_denied=False)
    writable_probe = ROOT / ".sandbox-write-probe"
    writable_probe.write_text("sandbox copy only")
    writable = writable_probe.read_text() == "sandbox copy only"
    writable_probe.unlink()
    checks = {
        "empty_home": home.is_dir() and not any(home.iterdir()),
        "host_paths_hidden": all(not Path(value).exists() for value in denied),
        "network_loopback_only": [name for _, name in socket.if_nameindex()] == ["lo"],
        "separate_pid_namespace": Path("/proc/1/comm").read_text().strip() == "bwrap",
        "separate_namespaces": all(namespaces[key] != value for key, value in host_namespaces.items()),
        "runtime_mounts_read_only": all(value["read_only_mount"] and value["write_open_denied"] for value in readonly.values()),
        "copied_checkout_writable": writable,
        "credentials_not_in_environment": not any(
            key.endswith(("_API_KEY", "_TOKEN")) for key in os.environ
        ),
    }
    evidence = {
        "mode": "Linux bubblewrap with --unshare-all and clean environment",
        "home": str(home),
        "network_interfaces": [name for _, name in socket.if_nameindex()],
        "namespaces": {name: {"host": host_namespaces[name], "sandbox": value} for name, value in namespaces.items()},
        "read_only_write_probes": readonly,
        "checks": checks,
        "writable_mounts": [str(SANDBOX_ROOT), "/tmp", str(home)],
        "read_only_mounts": ["system runtime directories", "Herdr/Codex binaries", "Python runtime and venv"],
        "model_prompt_submitted": False,
        "user_codex_credentials_mounted": False,
        "versions": {"python": platform.python_version(), "herdr": subprocess.check_output(["herdr", "--version"], text=True).strip(),
                     "codex": subprocess.check_output(["codex", "--version"], text=True, stderr=subprocess.DEVNULL).strip()},
        "scope": "Local PTY/process/API behavior with a test-only no-auth provider; remote model operation is not exercised",
    }
    (ROOT / "docs/isolation.json").write_text(json.dumps(evidence, indent=2) + "\n")
    (ROOT / NEXT_RUN).mkdir(parents=True, exist_ok=True)
    (ROOT / NEXT_RUN / "isolation.json").write_text(json.dumps(evidence, indent=2) + "\n")
    if not all(checks.values()):
        raise RuntimeError(f"Sandbox isolation checks failed: {checks}")
    print("Passed: sandbox isolation checks", flush=True)
    codex_home = Path(os.environ["CODEX_HOME"])
    codex_home.mkdir()
    # A provider needing no authentication avoids login interaction. Its endpoint
    # is intentionally closed, and the runner never submits a model prompt.
    (codex_home / "config.toml").write_text('''model = "gpt-6.1-sol"
model_provider = "offline-test"
disable_update_check = true
[model_providers.offline-test]
name = "Offline E2E test"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
''')
    codex_arguments = ("--no-daemon", "--no-alt-screen", "--sandbox", "read-only")
    status = 0
    if suite in ("old", "all"):
        from verify_herdr import Verification
        verification = Verification(ROOT / "docs/captures", codex_arguments=codex_arguments)
        try:
            verification.run()
        finally:
            verification.finish()
    if suite in ("next", "all"):
        from next_suite import run_suite
        status = run_suite(ROOT / NEXT_RUN, codex_arguments, only)
    if status:
        raise SystemExit(status)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run real Herdr E2E in a disposable sandbox")
    parser.add_argument("--codex-binary", help="Native Linux Codex executable, if auto-discovery fails")
    parser.add_argument("--suite", choices=("old", "next", "all"), default="old",
                        help="old: original scenarios + installer lifecycle (docs/sandbox); next: PLAN section 10 "
                             "scenarios (docs/sandbox-next); all: both")
    parser.add_argument("--only", help="next suite: comma-separated scenario ID prefixes, e.g. S1,R03,T")
    parser.add_argument("--output", type=Path, default=None,
                        help="Evidence destination for the old suite (default docs/sandbox)")
    parser.add_argument("--next-output", type=Path, default=ROOT / "docs/sandbox-next",
                        help="Evidence destination for the next suite (default docs/sandbox-next)")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    only = [item for item in (args.only or "").split(",") if item] or None
    if args.inside:
        inside(args.suite, only)
        return 0
    if args.output is None:
        args.output = ROOT / "docs/sandbox"
    if args.suite == "next" and args.output != ROOT / "docs/sandbox":
        args.next_output = args.output  # --output is accepted for the only suite being run
    if not args.run:
        parser.error("Pass --run to perform opt-in sandbox E2E verification.")
    bwrap = shutil.which("bwrap")
    herdr = shutil.which("herdr")
    if sys.platform != "linux" or not bwrap or not herdr:
        parser.error("Requires Linux, bubblewrap, Herdr, and enabled unprivileged user namespaces.")
    codex = native_codex(args.codex_binary)
    python = ROOT / ".venv/bin/python"
    if not python.is_file():
        parser.error("Run bin/setup and install requirements-test.lock first.")
    # The venv references this interpreter prefix; retain its original path.
    runtime = python.resolve().parent.parent
    runtime_alias = Path(os.path.abspath(python.parent / os.readlink(python))).parent.parent if python.is_symlink() else runtime
    denied = [str(Path.home() / ".codex"), str(ROOT), str(Path.home() / ".config/herdr")]
    if os.environ.get("HERDR_SOCKET_PATH"):
        denied.append(os.environ["HERDR_SOCKET_PATH"])
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="herdr-rename-sandbox-") as temporary:
        checkout = Path(temporary) / "checkout"
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "graphify-out", ".graphify-cache",
            "dist", "captures", "sandbox", "sandbox-next", "next-run", "r1-live", "actual-results.json", "isolation.json",
        ))
        system_config = Path(temporary) / "system-config"
        system_config.mkdir()
        (system_config / "passwd").write_text(f"tester:x:{os.getuid()}:{os.getgid()}:Sandbox tester:/sandbox/home:/bin/bash\n")
        (system_config / "group").write_text(f"tester:x:{os.getgid()}:\n")
        (system_config / "nsswitch.conf").write_text("passwd: files\ngroup: files\nhosts: files\n")
        (system_config / "hosts").write_text("127.0.0.1 localhost\n::1 localhost\n")
        command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--hostname", "herdr-rename-sandbox", "--cap-drop", "ALL", "--clearenv"]
        for directory in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(directory).exists():
                command += ["--ro-bind", directory, directory]
        command += ["--dir", "/etc"]
        for name in ("passwd", "group", "nsswitch.conf", "hosts"):
            command += ["--ro-bind", str(system_config / name), f"/etc/{name}"]
        for path in ("/etc/ld.so.cache", "/etc/fonts", "/etc/localtime"):
            if Path(path).exists():
                command += ["--ro-bind", path, path]
        command += [
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run",
            "--tmpfs", "/sandbox/home", "--ro-bind", str(runtime), str(runtime),
            "--bind", str(checkout), str(SANDBOX_ROOT),
            "--ro-bind", str(ROOT / ".venv"), str(SANDBOX_ROOT / ".venv"),
            "--ro-bind", str(Path(herdr).resolve()), "/opt/bin/herdr",
            "--ro-bind", str(codex), "/opt/bin/codex",
        ]
        if runtime_alias != runtime:
            command += ["--ro-bind", str(runtime), str(runtime_alias)]
        environment = {
            "HOME": "/sandbox/home", "PATH": "/opt/bin:/usr/bin:/bin", "SHELL": "/bin/bash",
            "TERM": "xterm-256color", "COLORTERM": "truecolor", "LANG": "C.UTF-8",
            "USER": "tester", "LOGNAME": "tester", "PYTHONDONTWRITEBYTECODE": "1",
            "HERDR_RENAME_SANDBOX_DENIED_PATHS": json.dumps(denied),
            "HERDR_RENAME_SANDBOX_HOST_NAMESPACES": json.dumps({name: os.readlink(f"/proc/self/ns/{name}") for name in ("pid", "net", "mnt")}),
            "XDG_CONFIG_HOME": "/sandbox/home/config", "XDG_STATE_HOME": "/sandbox/home/state",
            "XDG_CACHE_HOME": "/sandbox/home/cache", "CODEX_HOME": "/sandbox/home/.codex",
        }
        for key, value in environment.items():
            command += ["--setenv", key, value]
        command += ["--chdir", str(SANDBOX_ROOT), str(SANDBOX_ROOT / ".venv/bin/python"),
                    str(SANDBOX_ROOT / "tools/verify_sandbox.py"), "--inside",
                    "--suite", args.suite, *(["--only", args.only] if args.only else [])]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, timeout=TIMEOUTS[args.suite])
            returncode = result.returncode
        except subprocess.TimeoutExpired:
            # Killing PID namespace init terminates all descendants, including servers.
            print("Sandbox E2E timed out; its process namespace was terminated.", file=sys.stderr)
            returncode = 124
        def collect(source_dir: Path, target_dir: Path) -> None:
            target_dir.mkdir(parents=True, exist_ok=True)
            for name in ("captures", "actual-results.json", "isolation.json"):
                source, target = source_dir / name, target_dir / name
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                if source.is_dir():
                    shutil.copytree(source, target)
                elif source.is_file():
                    shutil.copy2(source, target)
        if args.suite in ("old", "all"):
            collect(checkout / "docs", destination)
        if args.suite in ("next", "all"):
            next_destination = args.next_output.resolve()
            collect(checkout / NEXT_RUN, next_destination)
            if args.suite == "next":
                destination = next_destination
            else:
                (next_destination / "run.json").write_text(json.dumps({"exit_code": returncode}, indent=2) + "\n")
        (destination / "run.json").write_text(json.dumps({
            "suite": args.suite, "exit_code": returncode, "date": datetime.now().astimezone().isoformat(),
            "cleanup": "Disposable checkout removed; namespace processes terminated on exit",
        }, indent=2) + "\n")
        print(f"Sandbox evidence: {destination}", flush=True)
        return returncode


if __name__ == "__main__":
    raise SystemExit(main())
