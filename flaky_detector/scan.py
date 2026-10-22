"""Backfill run history from a repository's past GitHub Actions runs.

Reads per-test results from the job logs of runs that ran ``pytest -v`` (no JUnit artifacts
needed). Past runs have no per-test coverage, so tests are fingerprinted by the commit's tree
plus the job name: only a pass and a fail on the same commit in the same job counts as flaky.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from .coverage_data import mangle_nodeid
from .fingerprint import TREE
from .ingest import merged_pr_number
from .junit import ERROR, FAILED, PASSED, SKIPPED
from .store import SCHEMA_VERSION, load_runs, run_identity, save_run

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ")
_OUTCOMES = "PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS|RERUN"
# "tests/test_x.py::test_a PASSED  [ 20%]" (pytest -v) and "[gw0] [ 20%] PASSED tests/..." (xdist)
_VERBOSE = re.compile(rf"^(?P<nodeid>\S+?\.py::.+?) (?P<outcome>{_OUTCOMES})(?:\s+\[\s*\d+%\])?\s*$")
_XDIST = re.compile(rf"^\[gw\d+\] \[\s*\d+%\] (?P<outcome>{_OUTCOMES}) (?P<nodeid>\S+?\.py::.+?)\s*$")
_SESSION_START = re.compile(r"^=+ test session starts =+$")
_OUTCOME_MAP = {
    "PASSED": PASSED, "XPASS": PASSED, "FAILED": FAILED, "RERUN": FAILED,
    "ERROR": ERROR, "SKIPPED": SKIPPED, "XFAIL": SKIPPED,
}
_RANK = {SKIPPED: 0, PASSED: 1, FAILED: 2, ERROR: 3}


class GhApi:
    """GitHub REST calls through the ``gh`` CLI, which already holds the user's credentials."""

    def get(self, path: str) -> dict:
        return json.loads(self._call(path))

    def text(self, path: str) -> str:
        # Job logs contain colour codes, which recent gh versions refuse to print without this flag.
        # parse_pytest_log strips them.
        try:
            return self._call(path, "--allow-escape-sequences")
        except RuntimeError as exc:
            if "unknown flag" not in str(exc):
                raise
            return self._call(path)

    @staticmethod
    def _call(path: str, *flags: str) -> str:
        result = subprocess.run(
            ["gh", "api", *flags, "-H", "Accept: application/vnd.github+json", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"gh api {path} failed")
        return result.stdout


def parse_pytest_log(log: str) -> dict[str, list[str]]:
    """Map each test's JUnit-style key to its outcomes, one per attempt, in log order.

    A new ``pytest`` session or a ``RERUN`` line starts a new attempt; other repeated lines for
    a test (such as a teardown ERROR after PASSED) belong to the same attempt.
    """
    attempts: dict[str, list[str]] = {}
    closed: set[str] = set()  # tests whose next line starts a new attempt
    for raw in log.splitlines():
        line = _STAMP.sub("", _ANSI.sub("", raw)).strip()
        if _SESSION_START.match(line):
            closed = set(attempts)
            continue
        match = _VERBOSE.match(line) or _XDIST.match(line)
        if not match:
            continue
        key = mangle_nodeid(match["nodeid"])
        outcome = _OUTCOME_MAP[match["outcome"]]
        history = attempts.setdefault(key, [])
        if history and key not in closed:
            if _RANK[outcome] > _RANK[history[-1]]:
                history[-1] = outcome
        else:
            history.append(outcome)
            closed.discard(key)
        if match["outcome"] == "RERUN":
            closed.add(key)
    return attempts


def list_runs(api, repo: str, workflow: str | None, limit: int) -> list[dict]:
    runs: list[dict] = []
    page = 1
    while len(runs) < limit:
        batch = api.get(f"repos/{repo}/actions/runs?status=completed&per_page=100&page={page}")["workflow_runs"]
        if not batch:
            break
        runs += [r for r in batch if not workflow or workflow in (r.get("name"), Path(r.get("path", "")).name)]
        page += 1
    return runs[:limit]


def scan(
    api,
    repo: str,
    store: Path,
    *,
    workflow: str | None = None,
    limit: int = 200,
    job_pattern: str | None = None,
    step_pattern: str | None = "test",
) -> list[Path]:
    """Save a run record for every job attempt with pytest -v output. Already-scanned jobs are skipped."""
    known = {run_identity(r) for r in load_runs(store)}
    saved = []
    for run in list_runs(api, repo, workflow, limit):
        for attempt in range(1, run.get("run_attempt", 1) + 1):
            jobs = api.get(f"repos/{repo}/actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100")["jobs"]
            for job in jobs:
                if not _wanted(job, job_pattern, step_pattern):
                    continue
                run_id = f"{run['id']}-{_slug(job['name'])}"
                if (run_id, attempt) in known:
                    continue
                try:
                    log = api.text(f"repos/{repo}/actions/jobs/{job['id']}/logs")
                except RuntimeError as exc:
                    print(f"warning: no log for {repo} run {run['id']} job {job['name']!r}: {exc}", file=sys.stderr)
                    continue
                tests = parse_pytest_log(log)
                if tests:
                    saved.append(save_run(_record(repo, run, job, run_id, attempt, tests), store))
    return saved


def _wanted(job: dict, job_pattern: str | None, step_pattern: str | None) -> bool:
    if job.get("conclusion") in (None, "skipped", "cancelled"):
        return False
    if job_pattern and not re.search(job_pattern, job["name"]):
        return False
    if step_pattern:
        return any(re.search(step_pattern, step.get("name", ""), re.IGNORECASE) for step in job.get("steps", []))
    return True


def _record(repo: str, run: dict, job: dict, run_id: str, attempt: int, tests: dict[str, list[str]]) -> dict:
    head_commit = run.get("head_commit") or {}
    tree = head_commit.get("tree_id") or run["head_sha"]
    pulls = run.get("pull_requests") or []
    pr_number = pulls[0]["number"] if pulls and run["event"] == "pull_request" else None
    if run["event"] == "pull_request":
        ref = f"refs/pull/{pr_number}/merge" if pr_number else ""
    else:
        ref = f"refs/heads/{run['head_branch']}"
    message = head_commit.get("message") or ""
    fingerprint = f"tree:{tree}:{job['name']}"  # the job name separates matrix legs and environments
    return {
        "schema": SCHEMA_VERSION,
        "source": "actions-log",
        "run_id": run_id,
        "run_attempt": attempt,
        "timestamp": job.get("started_at") or run.get("run_started_at") or run["created_at"],
        "sha": run["head_sha"],
        "tree": tree,
        "base_sha": None,
        "commit_message": message.splitlines()[0] if message else "",
        "repository": repo,
        "workflow": run.get("name", ""),
        "job": job["name"],
        "event": run["event"],
        "ref": ref,
        "pr_number": pr_number,
        "pr_head_sha": run["head_sha"] if pr_number else None,
        "pr_head_ref": run.get("head_branch") if pr_number else None,
        "merged_pr": merged_pr_number(message) if run["event"] == "push" else None,
        "url": run.get("html_url"),
        "tests": {
            key: {"attempts": outcomes, "duration": 0.0, "fingerprint": fingerprint, "fingerprint_kind": TREE}
            for key, outcomes in sorted(tests.items())
        },
    }


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "job"
