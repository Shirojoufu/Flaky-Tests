"""Turn one CI run's test and coverage reports into a run record."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import coverage_data
from .fingerprint import DEFAULT_EXTRA, PRECISE, Fingerprinter
from .gitutil import changed_lines, is_null_sha, try_git
from .junit import FAILURES, CaseResult, parse_junit
from .store import SCHEMA_VERSION

_MERGED_PR = (re.compile(r"^Merge pull request #(\d+)"), re.compile(r"\(#(\d+)\)\s*$"))


def merged_pr_number(message: str | None) -> int | None:
    """The PR a merge or squash commit came from, read from GitHub's default commit message."""
    first_line = (message or "").splitlines()[0] if message else ""
    for pattern in _MERGED_PR:
        if match := pattern.search(first_line):
            return int(match[1])
    return None


def github_context(env: dict[str, str]) -> dict:
    payload: dict = {}
    event_path = env.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pr = payload.get("pull_request") or {}
    head_commit = payload.get("head_commit") or {}
    return {
        "repository": env.get("GITHUB_REPOSITORY", ""),
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "event": env.get("GITHUB_EVENT_NAME", "local"),
        "ref": env.get("GITHUB_REF", ""),
        "run_id": env.get("GITHUB_RUN_ID"),
        "run_attempt": int(env.get("GITHUB_RUN_ATTEMPT", "1")),
        "pr_number": pr.get("number"),
        "pr_head_sha": (pr.get("head") or {}).get("sha"),
        "pr_head_ref": (pr.get("head") or {}).get("ref"),
        "base_sha": (pr.get("base") or {}).get("sha") if pr else payload.get("before"),
        "merged_pr": merged_pr_number(head_commit.get("message")),
    }


def build_record(
    *,
    junit: Path,
    reruns: list[Path],
    coverage: Path | None,
    root: Path,
    env: dict[str, str],
    base_sha: str | None = None,
    exclude=coverage_data.DEFAULT_EXCLUDE,
    extra=DEFAULT_EXTRA,
    mode: str = PRECISE,
    now: datetime | None = None,
) -> dict:
    context = github_context(env)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = context.pop("run_id") or f"local-{timestamp.replace(':', '')}"
    sha = try_git(["rev-parse", "HEAD"], root) or env.get("GITHUB_SHA", "")
    tree = try_git(["rev-parse", "HEAD^{tree}"], root) or f"run:{run_id}"
    event_base = context.pop("base_sha")
    base = base_sha or event_base
    if not context["merged_pr"]:
        context["merged_pr"] = merged_pr_number(try_git(["log", "-1", "--format=%s"], root))

    attempts: dict[str, list[CaseResult]] = {key: [result] for key, result in parse_junit(junit).items()}
    for path in reruns:
        for key, result in parse_junit(path).items():
            attempts.setdefault(key, []).append(result)

    raw = coverage_data.load(coverage) if coverage else None
    dependencies = coverage_data.dependencies_by_test(raw) if raw else {}
    fingerprinter = Fingerprinter(root, tree, extra, mode)

    tests = {}
    for key, results in sorted(attempts.items()):
        fingerprint, kind = fingerprinter.for_test(dependencies.get(key))
        entry = {
            "attempts": [result.outcome for result in results],
            "duration": round(results[0].duration, 4),
            "fingerprint": fingerprint,
            "fingerprint_kind": kind,
        }
        message = next((r.message for r in results if r.outcome in FAILURES and r.message), "")
        if message:
            entry["message"] = message
        tests[key] = entry

    record = {
        "schema": SCHEMA_VERSION,
        "run_id": str(run_id),
        "timestamp": timestamp,
        "sha": sha,
        "tree": tree,
        "base_sha": base,
        "commit_message": try_git(["log", "-1", "--format=%s"], root) or "",
        **context,
        "tests": tests,
    }
    if raw:
        record["coverage"] = coverage_data.summarize(raw, exclude)
        if sha and not is_null_sha(base):
            try:
                changed = changed_lines(base, sha, root)
            except subprocess.CalledProcessError as exc:
                print(f"warning: could not diff {base}..{sha}: {exc.stderr.strip()}", file=sys.stderr)
            else:
                record["diff_coverage"] = coverage_data.diff_coverage(raw, changed, exclude)
    return record
