"""Judge every test across the run history: flaky, consistently failing, or healthy.

A test is flaky when it both passed and failed with the same fingerprint, i.e. on
identical code. A retry inside one run is the strongest case of this.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .junit import FAILURES, PASSED

SEVERITIES = ("critical", "high", "medium", "low")
MAIN_REF = "refs/heads/main"
FLAKY = "flaky"
FAILING = "failing"
HEALTHY = "healthy"

_REF_FIELDS = ("sha", "timestamp", "event", "ref", "pr_number", "merged_pr", "run_id", "run_attempt")


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
    first_flaky_failure: dict | None = None
    # First run on the code state that turned out flaky, and the last run on the code before it.
    introduced_in: dict | None = None
    previous_code_seen_in: dict | None = None
    failing_since: dict | None = None
    last_failure: dict | None = None
    last_seen: dict | None = None
    timeline: list[list] = field(default_factory=list)

    @property
    def sort_key(self) -> tuple:
        if self.severity:
            rank = SEVERITIES.index(self.severity)
        else:
            rank = len(SEVERITIES) + (0 if self.status == FAILING else 1)
        return rank, -self.failure_rate, -self.failures, self.key

    def to_dict(self) -> dict:
        return asdict(self)


def is_main_push(run: dict, main_ref: str = MAIN_REF) -> bool:
    """A run on the main branch itself: a push or merge, or a scheduled or manual run of main."""
    return run.get("event") != "pull_request" and run.get("ref") == main_ref


def run_ref(runs: list[dict], index: int) -> dict:
    run = runs[index]
    return {"index": index, **{name: run.get(name) for name in _REF_FIELDS}}


def analyze(runs: list[dict], window: int = 20, main_ref: str = MAIN_REF) -> dict[str, Verdict]:
    observations: dict[str, list[Observation]] = {}
    for index, run in enumerate(runs):
        for key, test in run.get("tests", {}).items():
            for attempt, outcome in enumerate(test.get("attempts", [])):
                if outcome == PASSED or outcome in FAILURES:
                    observations.setdefault(key, []).append(
                        Observation(index, outcome, test.get("fingerprint", ""), attempt)
                    )
    verdicts = [_judge(key, obs, runs, window, main_ref) for key, obs in observations.items()]
    return {verdict.key: verdict for verdict in sorted(verdicts, key=lambda v: v.sort_key)}


def _judge(key: str, obs: list[Observation], runs: list[dict], window: int, main_ref: str) -> Verdict:
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
    verdict.flaky_observations = len(flaky_obs)
    verdict.flaky_failures = len(flaky_failures)
    verdict.failure_rate = round(len(flaky_failures) / len(flaky_obs), 4)
    verdict.flips = sum(_flips(by_fingerprint[fp]) for fp in flaky_fingerprints)
    verdict.main_failures = len(main_failure_runs)
    verdict.active = flaky_failures[-1].run_index >= len(runs) - window
    verdict.first_flaky_failure = run_ref(runs, flaky_failures[0].run_index)
    verdict.introduced_in = run_ref(runs, introduced)
    verdict.previous_code_seen_in = run_ref(runs, previous.run_index) if previous else None
    verdict.severity = severity(verdict.failure_rate, verdict.flaky_failures, verdict.main_failures, verdict.active)
    return verdict


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


def is_flaky_failure(verdict: Verdict | None, test: dict) -> bool:
    attempts = test.get("attempts") or []
    if any(a == PASSED for a in attempts[1:]):
        return True
    return bool(verdict and test.get("fingerprint") in verdict.flaky_fingerprints)


def classify_current(run: dict, verdicts: dict[str, Verdict]) -> list[dict]:
    """Explain each test that failed its first attempt in ``run``."""
    out = []
    for key, test in sorted(run.get("tests", {}).items()):
        attempts = test.get("attempts") or []
        if not attempts or attempts[0] not in FAILURES:
            continue
        verdict = verdicts.get(key)
        if any(a == PASSED for a in attempts[1:]):
            kind = "passed-on-retry"
        elif is_flaky_failure(verdict, test):
            kind = "known-flaky"
        else:
            kind = "failure"
        out.append({
            "key": key,
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
                counts["flaky_failures"] += is_flaky_failure(verdicts.get(key), test)
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
