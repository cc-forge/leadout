"""Shared test helpers: import path, network guard, fixtures, CLI capture, temp git repos.

Importing this module:
- puts scripts/ on sys.path;
- points LEADOUT_FIXTURES at an empty fixture folder, so any gh call that a test
  did not prepare fails with "missing fixture";
- replaces subprocess.run with a guard that fails the test on any `gh` process.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
BUNDLED_RUBRIC = str(ROOT / "scripts" / "rubric.json")

_real_run = subprocess.run


def _guarded_run(args, *a, **kw):
    if args and str(args[0]) == "gh":
        raise AssertionError(f"tests must not run gh: {args}")
    return _real_run(args, *a, **kw)


subprocess.run = _guarded_run
os.environ.pop("LEADOUT_RECORD", None)
os.environ.pop("LEADOUT_DEBUG", None)
os.environ["LEADOUT_FIXTURES"] = str(FIXTURES / "gh" / "_none")


@contextlib.contextmanager
def gh_fixtures(name_or_path):
    """Serve gh responses from tests/fixtures/gh/<name> (or an absolute folder)."""
    p = Path(name_or_path)
    folder = p if p.is_absolute() else FIXTURES / "gh" / str(name_or_path)
    old = os.environ.get("LEADOUT_FIXTURES")
    os.environ["LEADOUT_FIXTURES"] = str(folder)
    try:
        yield folder
    finally:
        os.environ["LEADOUT_FIXTURES"] = old or ""


def load_facts(name: str) -> dict:
    with open(FIXTURES / "facts" / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def deep(d: dict) -> dict:
    return copy.deepcopy(d)


def run_cli(main, argv):
    """Run a script's main(argv); return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def git(path, *args) -> str:
    p = _real_run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                   "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                   "-c", "core.hooksPath=/dev/null", *args],
                  cwd=str(path), capture_output=True, text=True, timeout=30, check=True)
    return p.stdout


def make_repo(path, files: dict, branch: str = "main", origin: str | None = None) -> Path:
    """Create a git repository with one commit of `files` ({relative path: text})."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "--quiet")
    git(path, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "--quiet", "-m", "initial")
    if origin:
        git(path, "remote", "add", "origin", origin)
    return path
