"""Version detection used by the verification tools to label their results."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from verify_herdr import herdr_version  # noqa: E402


def fake_run(monkeypatch, stdout="", error=None):
    def run(*args, **kwargs):
        if error:
            raise error
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
    monkeypatch.setattr(subprocess, "run", run)


def test_parses_bare_version(monkeypatch):
    fake_run(monkeypatch, "herdr 0.9.5\n")
    assert herdr_version("/x/herdr") == "0.9.5"


def test_unparseable_output_is_unknown(monkeypatch):
    fake_run(monkeypatch, "no version here\n")
    assert herdr_version("/x/herdr") == "unknown"


def test_missing_binary_is_unknown(monkeypatch):
    fake_run(monkeypatch, error=FileNotFoundError())
    assert herdr_version("/x/herdr") == "unknown"
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert herdr_version() == "unknown"
