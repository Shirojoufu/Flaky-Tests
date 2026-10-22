from flaky_detector.analysis import (
    FAILING,
    FLAKY,
    HEALTHY,
    analyze,
    classify_current,
    run_rows,
    severity,
)


def run(index, tests, *, event="pull_request", ref="refs/pull/1/merge", pr=1, coverage=None):
    record = {
        "run_id": str(index),
        "run_attempt": 1,
        "timestamp": f"2026-10-{index + 1:02d}T00:00:00Z",
        "sha": f"{index:07d}",
        "event": event,
        "ref": ref,
        "pr_number": pr if event == "pull_request" else None,
        "tests": {
            key: {"attempts": attempts if isinstance(attempts, list) else [attempts], "fingerprint": fp}
            for key, (attempts, fp) in tests.items()
        },
    }
    if coverage is not None:
        record["coverage"] = {"percent": coverage, "statements": 10, "covered": 1, "files": {}}
    return record


def main_run(index, tests, **kwargs):
    return run(index, tests, event="push", ref="refs/heads/main", **kwargs)


def test_same_code_pass_and_fail_is_flaky_and_onset_is_tracked():
    runs = [
        main_run(0, {"t": ("passed", "old")}),
        main_run(1, {"t": ("passed", "new")}),  # code state that will turn out flaky
        run(2, {"t": ("passed", "new")}),
        run(3, {"t": ("failed", "new")}),
        main_run(4, {"t": ("passed", "new")}),
        main_run(5, {"t": ("failed", "new")}),
    ]

    verdict = analyze(runs)["t"]

    assert verdict.status == FLAKY
    assert verdict.flaky_fingerprints == ["new"]
    assert (verdict.flaky_failures, verdict.flaky_observations) == (2, 5)
    assert verdict.failure_rate == 0.4
    assert verdict.flips == 3
    assert verdict.main_failures == 1
    assert verdict.first_flaky_failure["index"] == 3
    assert verdict.introduced_in["index"] == 1
    assert verdict.previous_code_seen_in["index"] == 0
    assert verdict.last_failure["index"] == 5
    assert verdict.severity == "high"


def test_failure_after_code_change_is_not_flaky():
    runs = [
        run(0, {"t": ("passed", "a")}),
        run(1, {"t": ("failed", "b")}),
        run(2, {"t": ("failed", "b")}),
    ]

    verdict = analyze(runs)["t"]

    assert verdict.status == FAILING
    assert verdict.severity is None
    assert verdict.failing_since["index"] == 1


def test_fixed_regression_is_healthy():
    runs = [run(0, {"t": ("failed", "a")}), run(1, {"t": ("passed", "b")})]

    assert analyze(runs)["t"].status == HEALTHY


def test_pass_on_retry_within_one_run_is_flaky():
    runs = [run(0, {"t": (["failed", "passed"], "a")})]

    verdict = analyze(runs)["t"]

    assert verdict.status == FLAKY
    assert verdict.retry_flakes == 1
    assert verdict.previous_code_seen_in is None
    assert classify_current(runs[0], {"t": verdict})[0]["kind"] == "passed-on-retry"


def test_skipped_attempts_are_ignored():
    runs = [run(0, {"t": ("skipped", "a")}), run(1, {"t": ("passed", "a")})]

    assert analyze(runs)["t"].observations == 1


def test_severity_levels():
    assert severity(0.30, 5, 0, active=True) == "critical"
    assert severity(0.01, 2, 3, active=True) == "critical"
    assert severity(0.12, 3, 0, active=True) == "high"
    assert severity(0.05, 2, 0, active=True) == "medium"
    assert severity(0.01, 1, 0, active=True) == "low"
    assert severity(0.50, 1, 0, active=True) == "medium"  # one failure is not enough evidence
    assert severity(0.30, 5, 0, active=False) == "high"  # dormant flakes drop a level


def test_dormant_flake_is_inactive():
    runs = [run(0, {"t": ("failed", "a")}), run(1, {"t": ("passed", "a")})]
    runs += [run(i, {"t": ("passed", "a")}) for i in range(2, 30)]

    assert analyze(runs, window=20)["t"].active is False


def test_classify_current_known_flaky_and_real_failures():
    runs = [
        run(0, {"flaky": ("passed", "a"), "real": ("passed", "x")}),
        run(1, {"flaky": ("failed", "a"), "real": ("failed", "y")}),
    ]
    verdicts = analyze(runs)

    kinds = {item["key"]: item["kind"] for item in classify_current(runs[1], verdicts)}

    assert kinds == {"flaky": "known-flaky", "real": "failure"}


def test_run_rows_count_flaky_failures_and_coverage():
    runs = [
        run(0, {"a": ("passed", "1"), "b": ("passed", "1"), "s": ("skipped", "1")}, coverage=90.0),
        main_run(1, {"a": (["failed", "passed"], "1"), "b": ("failed", "2")}, coverage=88.5),
    ]

    rows = run_rows(runs, analyze(runs))

    assert (rows[0]["tests"], rows[0]["skipped"], rows[0]["pass_rate"]) == (2, 1, 100.0)
    assert rows[1]["main"] is True
    assert (rows[1]["failed"], rows[1]["failed_after_retry"], rows[1]["flaky_failures"]) == (2, 1, 1)
    assert rows[1]["coverage"] == 88.5
