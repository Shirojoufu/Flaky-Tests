"""Read coverage.py JSON reports: totals, per-file gaps, per-test dependencies and diff coverage."""

from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from pathlib import Path

# Test code is measured (so fingerprints can include it) but left out of coverage percentages.
DEFAULT_EXCLUDE = (
    "tests/*",
    "*/tests/*",
    "test_*.py",
    "*/test_*.py",
    "*_test.py",
    "conftest.py",
    "*/conftest.py",
)


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _pct(covered: int, statements: int) -> float:
    return round(100.0 * covered / statements, 2) if statements else 100.0


def is_excluded(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def ranges(lines) -> list[list[int]]:
    """Collapse line numbers into inclusive ``[start, end]`` ranges."""
    out: list[list[int]] = []
    for number in sorted(lines):
        if out and number == out[-1][1] + 1:
            out[-1][1] = number
        else:
            out.append([number, number])
    return out


def mangle_nodeid(nodeid: str) -> str:
    """Turn a pytest node id into the ``classname.name`` key pytest writes to JUnit XML."""
    path, bracket, params = nodeid.partition("[")
    names = path.split("::")
    names[0] = re.sub(r"\.py$", "", names[0].replace("/", "."))
    names[-1] += bracket + params
    return ".".join(names)


def summarize(raw: dict, exclude=DEFAULT_EXCLUDE) -> dict:
    files = {}
    total_statements = total_covered = 0
    for path, data in raw.get("files", {}).items():
        path = _norm(path)
        if is_excluded(path, exclude):
            continue
        executed = data.get("executed_lines", [])
        missing = data.get("missing_lines", [])
        statements = len(executed) + len(missing)
        files[path] = {
            "statements": statements,
            "covered": len(executed),
            "percent": _pct(len(executed), statements),
            "missing": ranges(missing),
        }
        total_statements += statements
        total_covered += len(executed)
    return {
        "percent": _pct(total_covered, total_statements),
        "statements": total_statements,
        "covered": total_covered,
        "files": dict(sorted(files.items())),
    }


def dependencies_by_test(raw: dict) -> dict[str, dict]:
    """Map each test's JUnit key to its file and the measured files it executed.

    Needs a report produced with ``--cov-context=test`` and ``show_contexts = true``.
    """
    tests: dict[str, dict] = {}
    for path, data in raw.get("files", {}).items():
        path = _norm(path)
        contexts: set[str] = set()
        for line_contexts in data.get("contexts", {}).values():
            contexts.update(line_contexts)
        for context in contexts:
            # pytest-cov names contexts "<nodeid>|run" (also "|setup" and "|teardown").
            nodeid = context.rpartition("|")[0] or context
            if not nodeid:
                continue
            entry = tests.setdefault(
                mangle_nodeid(nodeid), {"file": _norm(nodeid.split("::")[0]), "deps": set()}
            )
            entry["deps"].add(path)
    return tests


def diff_coverage(raw: dict, changed: dict[str, set[int]], exclude=DEFAULT_EXCLUDE) -> dict:
    """Coverage of the statements a change added or modified."""
    measured = {_norm(path): data for path, data in raw.get("files", {}).items()}
    extensions = {Path(path).suffix for path in measured}
    files = {}
    unmeasured = []
    total_statements = total_covered = 0
    for path, lines in sorted(changed.items()):
        if is_excluded(path, exclude) or not lines:
            continue
        data = measured.get(path)
        if data is None:
            # A changed source file that no test ever imported.
            if Path(path).suffix in extensions:
                unmeasured.append(path)
            continue
        missing = set(data.get("missing_lines", []))
        statements = lines & (missing | set(data.get("executed_lines", [])))
        if not statements:
            continue
        uncovered = statements & missing
        files[path] = {
            "changed_statements": len(statements),
            "covered": len(statements) - len(uncovered),
            "uncovered": ranges(uncovered),
        }
        total_statements += len(statements)
        total_covered += len(statements) - len(uncovered)
    return {
        "percent": _pct(total_covered, total_statements) if total_statements else None,
        "changed_statements": total_statements,
        "covered": total_covered,
        "files": files,
        "unmeasured_files": unmeasured,
    }
