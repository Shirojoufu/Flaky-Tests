"""Parse JUnit XML test reports into per-test outcomes."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

PASSED = "passed"
FAILED = "failed"
ERROR = "error"
SKIPPED = "skipped"
FAILURES = frozenset({FAILED, ERROR})

# When a test appears more than once in a report (e.g. a teardown error), the worst outcome wins.
_RANK = {SKIPPED: 0, PASSED: 1, FAILED: 2, ERROR: 3}
MESSAGE_LIMIT = 500


@dataclass
class CaseResult:
    key: str
    outcome: str
    duration: float
    message: str = ""


def case_key(classname: str, name: str) -> str:
    """Identify a test the way pytest's JUnit report does: ``classname.name``."""
    return f"{classname}.{name}" if classname else name


def parse_junit(path: Path) -> dict[str, CaseResult]:
    root = ET.parse(path).getroot()
    results: dict[str, CaseResult] = {}
    for case in root.iter("testcase"):
        key = case_key(case.get("classname", ""), case.get("name", ""))
        outcome, message = _outcome(case)
        try:
            duration = float(case.get("time") or 0)
        except ValueError:
            duration = 0.0
        result = CaseResult(key, outcome, duration, message[:MESSAGE_LIMIT])
        previous = results.get(key)
        if previous is None:
            results[key] = result
        elif _RANK[outcome] > _RANK[previous.outcome]:
            result.duration += previous.duration
            results[key] = result
    return results


def _outcome(case: ET.Element) -> tuple[str, str]:
    for tag, outcome in (("error", ERROR), ("failure", FAILED), ("skipped", SKIPPED)):
        element = case.find(tag)
        if element is not None:
            return outcome, (element.get("message") or element.text or "").strip()
    return PASSED, ""
