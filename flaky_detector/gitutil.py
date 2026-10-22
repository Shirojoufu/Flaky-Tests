"""Thin wrappers around the git CLI."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
from pathlib import Path

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def auth_env(token: str | None) -> dict[str, str] | None:
    """Environment that authenticates git to GitHub with ``token``, kept out of argv and .git/config."""
    if not token:
        return None
    credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credentials}",
    }


def run_git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def run_git_bytes(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, check=True).stdout


def try_git(args: list[str], cwd: Path) -> str | None:
    try:
        return run_git(args, cwd).strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def is_null_sha(sha: str | None) -> bool:
    return not sha or set(sha) == {"0"}


def blob_hash(path: Path) -> str:
    """Hash a file the way ``git hash-object`` does, without spawning git per file."""
    try:
        data = path.read_bytes()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return "missing"
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def changed_lines(base: str, head: str, cwd: Path) -> dict[str, set[int]]:
    """Map each file changed between two commits to the line numbers added or modified in ``head``."""
    diff = run_git(
        [
            "-c", "core.quotePath=false",
            "diff", "--unified=0", "--no-color", "--no-ext-diff",
            "--src-prefix=a/", "--dst-prefix=b/",
            base, head,
        ],
        cwd,
    )
    changed: dict[str, set[int]] = {}
    current: str | None = None
    previous = ""
    for line in diff.splitlines():
        if line.startswith("+++ ") and previous.startswith("--- "):
            target = line[4:]
            current = target[2:] if target.startswith("b/") else None
        elif current and (match := _HUNK.match(line)):
            start = int(match[1])
            count = int(match[2]) if match[2] is not None else 1
            if count:
                changed.setdefault(current, set()).update(range(start, start + count))
        previous = line
    return changed
