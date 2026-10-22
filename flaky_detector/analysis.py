"""Judge every test across the run history: flaky, fixed, consistently failing, or healthy.

A test is flaky when it both passed and failed with the same fingerprint, i.e. on
identical code. A retry inside one run is the strongest case of this. In a CI matrix each
environment is judged separately, so a failure only one environment has is not a flake.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .fingerprint import ABSENT, digest
from .junit import FAILURES, PASSED

SEVERITIES = ("critical", "high", "medium", "low")
MAIN_REF = "refs/heads/main"
FLAKY = "flaky"
FIXED = "fixed"
FAILING = "failing"
HEALTHY = "healthy"
# A flake counts as fixed once its new code has passed so often that, were it still failing at
# its old rate, a streak that long would happen less than 5% of the time.
FIXED_CONFIDENCE = 0.95
MIN_PASSES_TO_FIX = 3

_REF_FIELDS = ("sha", "timestamp", "event", "ref", "pr_number", "merged_pr", "run_id", "run_attempt",
               "environment")


def verdict_key(key: str, environment: str | None) -> str:
    """The name a test is judged under: its key, plus the matrix environment if there is one."""
    return f"{key} [{environment}]" if environment else key


@dataclass
class Observation:
    run_index: int
    outcome: str
    fingerprint: str
    attempt: int

    @property
    def failed(self) -> bool:
        return self.outcome in FAILURES


@dataclass
class Verdict:
    key: str
    status: str = HEALTHY
    severity: str | None = None
    observations: int = 0
    failures: int = 0
    # Executions on code states that produced both passes and failures.
    flaky_observations: int = 0
    flaky_failures: int = 0
    failure_rate: float = 0.0
    flips: int = 0
    retry_flakes: int = 0
    main_failures: int = 0
    active: bool = False
    flaky_fingerprints: list[str] = field(default_factory=list)
    flaky_runs: list[int] = field(default_factory=list)  # runs of this test on flaky code states
    first_flaky_failure: dict | None = None
    # First run on the code state that turned out flaky, and the last run on the code before it.
    introduced_in: dict | None = None
    previous_code_seen_in: dict | None = None
    failing_since: dict | None = None
    fixed_in: dict | None = None  # first run on the code that stopped the flake
    passes_since_fix: int = 0
    last_failure: dict | None = None
    last_seen: dict | None = None
    timeline: list[list] = field(default_factory=list)

    @property
    def sort_key(self) -> tuple:
        if self.severity:
            rank = SEVERITIES.index(self.severity)
        else:
            rank = len(SEVERITIES) + (FAILING, FIXED, HEALTHY).index(self.status)
        return rank, -self.failure_rate, -self.failures, self.key

    def to_dict(self) -> dict:
        return asdict(self)


def is_main_push(run: dict, main_ref: str = MAIN_REF) -> bool:
    """A run on the main branch itself: a push or merge, or a scheduled or manual run of main."""
    return run.get("event") != "pull_request" and run.get("ref") == main_ref


def run_ref(runs: list[dict], index: int) -> dict:
    run = runs[index]
    return {"index": index, **{name: run.get(name) for name in _REF_FIELDS}}


def fingerprints(runs: list[dict]) -> dict[tuple[int, str], str]:
    """Each test's fingerprint in each run, over every file the test executed in any run.

    Tests without recorded dependencies (tree mode, scanned logs) keep their stored fingerprint.
    """
    files = [dict(map(tuple, run.get("files", []))) for run in runs]
    paths = [[path for path, _ in run.get("files", [])] for run in runs]
    union: dict[str, set[str]] = {}
    for index, run in enumerate(runs):
        for key, test in run.get("tests", {}).items():
            if "deps" in test:
                union.setdefault(key, set()).update(paths[index][i] for i in test["deps"])
    result = {}
    for index, run in enumerate(runs):
        for key, test in run.get("tests", {}).items():
            if "deps" in test:
                result[(index, key)] = digest(union[key], lambda path: files[index].get(path, ABSENT))
            else:
                result[(index, key)] = test.get("fingerprint", "")
    return result


def analyze(runs: list[dict], window: int = 20, main_ref: str = MAIN_REF) -> dict[str, Verdict]:
    """Verdicts keyed by ``verdict_key``. ``window`` is how many recent CI runs count as active."""
    fingerprint = fingerprints(runs)
    observations: dict[str, list[Observation]] = {}
    for index, run in enumerate(runs):
        for key, test in run.get("tests", {}).items():
            for attempt, outcome in enumerate(test.get("attempts", [])):
                if outcome == PASSED or outcome in FAILURES:
                    observations.setdefault(verdict_key(key, run.get("environment")), []).append(
                        Observation(index, outcome, fingerprint[(index, key)], attempt)
                    )
    # Matrix environments of one CI run are separate records but one run for recency.
    ordinal, seen = [], {}
    for run in runs:
        ordinal.append(seen.setdefault((str(run.get("run_id")), run.get("run_attempt", 1)), len(seen)))
    recent_from = len(seen) - window
    verdicts = [_judge(key, obs, runs, ordinal, recent_from, main_ref) for key, obs in observations.items()]
    return {verdict.key: verdict for verdict in sorted(verdicts, key=lambda v: v.sort_key)}


def _judge(key: str, obs: list[Observation], runs: list[dict], ordinal: list[int], recent_from: int,
           main_ref: str) -> Verdict:
    by_fingerprint: dict[str, list[Observation]] = {}
    by_run: dict[int, list[Observation]] = {}
    for o in obs:
        by_fingerprint.setdefault(o.fingerprint, []).append(o)
        by_run.setdefault(o.run_index, []).append(o)

    failures = [o for o in obs if o.failed]
    verdict = Verdict(
        key=key,
        observations=len(obs),
        failures=len(failures),
        retry_flakes=sum(1 for group in by_run.values() if _mixed(group)),
        last_seen=run_ref(runs, obs[-1].run_index),
        last_failure=run_ref(runs, failures[-1].run_index) if failures else None,
        timeline=[[o.run_index, o.attempt, o.outcome] for o in obs],
    )

    flaky_fingerprints = [fp for fp, group in by_fingerprint.items() if _mixed(group)]
    if not flaky_fingerprints:
        if obs[-1].failed:
            verdict.status = FAILING
            streak_start = obs[-1]
            for o in reversed(obs):
                if not o.failed:
                    break
                streak_start = o
            verdict.failing_since = run_ref(runs, streak_start.run_index)
        return verdict

    flaky_set = set(flaky_fingerprints)
    flaky_obs = [o for o in obs if o.fingerprint in flaky_set]
    flaky_failures = [o for o in flaky_obs if o.failed]
    introduced = min(by_fingerprint[fp][0].run_index for fp in flaky_fingerprints)
    previous = next((o for o in reversed(obs) if o.run_index < introduced), None)
    main_failure_runs = {o.run_index for o in flaky_failures if is_main_push(runs[o.run_index], main_ref)}

    verdict.status = FLAKY
    verdict.flaky_fingerprints = flaky_fingerprints
    verdict.flaky_runs = sorted({o.run_index for o in flaky_obs})
    verdict.flaky_observations = len(flaky_obs)
    verdict.flaky_failures = len(flaky_failures)
    verdict.failure_rate = round(len(flaky_failures) / len(flaky_obs), 4)
    verdict.flips = sum(_flips(by_fingerprint[fp]) for fp in flaky_fingerprints)
    verdict.main_failures = len(main_failure_runs)
    verdict.active = ordinal[flaky_failures[-1].run_index] >= recent_from
    verdict.first_flaky_failure = run_ref(runs, flaky_failures[0].run_index)
    verdict.introduced_in = run_ref(runs, introduced)
    verdict.previous_code_seen_in = run_ref(runs, previous.run_index) if previous else None

    current = by_fingerprint[obs[-1].fingerprint]
    if obs[-1].fingerprint not in flaky_set and not any(o.failed for o in current):
        verdict.passes_since_fix = len(current)
        if len(current) >= passes_to_trust_fix(verdict.failure_rate):
            verdict.status = FIXED
            verdict.fixed_in = run_ref(runs, current[0].run_index)
            return verdict
    verdict.severity = severity(verdict.failure_rate, verdict.flaky_failures, verdict.main_failures, verdict.active)
    return verdict


def passes_to_trust_fix(failure_rate: float) -> int:
    """Consecutive passes on new code needed before a flake with this failure rate counts as fixed."""
    if failure_rate >= 1:
        return MIN_PASSES_TO_FIX
    needed = math.log(1 - FIXED_CONFIDENCE) / math.log(1 - failure_rate)
    return max(MIN_PASSES_TO_FIX, math.ceil(needed))


def severity(rate: float, failures: int, main_failures: int, active: bool) -> str:
    """Rank how much a flaky test hurts: how often it fails, and how often it broke main."""
    if (rate >= 0.25 and failures >= 3) or main_failures >= 3:
        level = 0
    elif rate >= 0.10 or main_failures >= 2:
        level = 1
    elif rate >= 0.03 or failures >= 2:
        level = 2
    else:
        level = 3
    if failures < 2:
        level = max(level, 2)  # one failure is too little evidence for high or critical
    if not active:
        level = min(level + 1, len(SEVERITIES) - 1)  # no flaky failure in the recent window
    return SEVERITIES[level]


def _mixed(group: list[Observation]) -> bool:
    return any(o.failed for o in group) and any(not o.failed for o in group)


def _flips(group: list[Observation]) -> int:
    return sum(1 for a, b in zip(group, group[1:]) if a.failed != b.failed)


def is_flaky_failure(verdict: Verdict | None, test: dict, run_index: int) -> bool:
    attempts = test.get("attempts") or []
    if any(a == PASSED for a in attempts[1:]):
        return True
    return bool(verdict and run_index in verdict.flaky_runs)


def classify_current(runs: list[dict], index: int, verdicts: dict[str, Verdict]) -> list[dict]:
    """Explain each test that failed its first attempt in ``runs[index]``."""
    out = []
    environment = runs[index].get("environment")
    for key, test in sorted(runs[index].get("tests", {}).items()):
        attempts = test.get("attempts") or []
        if not attempts or attempts[0] not in FAILURES:
            continue
        verdict = verdicts.get(verdict_key(key, environment))
        if any(a == PASSED for a in attempts[1:]):
            kind = "passed-on-retry"
        elif is_flaky_failure(verdict, test, index):
            kind = "known-flaky"
        else:
            kind = "failure"
        out.append({
            "key": verdict_key(key, environment),
            "kind": kind,
            "attempts": attempts,
            "severity": verdict.severity if verdict else None,
            "message": test.get("message", ""),
        })
    return out


def run_rows(runs: list[dict], verdicts: dict[str, Verdict], main_ref: str = MAIN_REF) -> list[dict]:
    """One row per run: the time series behind the trend charts."""
    rows = []
    for index, run in enumerate(runs):
        counts = dict(tests=0, passed=0, failed=0, skipped=0, failed_after_retry=0, flaky_failures=0)
        for key, test in run.get("tests", {}).items():
            attempts = test.get("attempts") or []
            if not attempts:
                continue
            if attempts[0] not in FAILURES and attempts[0] != PASSED:
                counts["skipped"] += 1
                continue
            counts["tests"] += 1
            if attempts[0] in FAILURES:
                counts["failed"] += 1
                counts["failed_after_retry"] += attempts[-1] in FAILURES
                verdict = verdicts.get(verdict_key(key, run.get("environment")))
                counts["flaky_failures"] += is_flaky_failure(verdict, test, index)
            else:
                counts["passed"] += 1
        coverage = run.get("coverage") or {}
        diff = run.get("diff_coverage") or {}
        rows.append({
            **run_ref(runs, index),
            "main": is_main_push(run, main_ref),
            **counts,
            "pass_rate": round(100.0 * counts["passed"] / counts["tests"], 2) if counts["tests"] else None,
            "coverage": coverage.get("percent"),
            "diff_coverage": diff.get("percent"),
        })
    return rows
