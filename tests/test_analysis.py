from flaky_detector.analysis import (
    FAILING,
    FIXED,
    FLAKY,
    HEALTHY,
    analyze,
    classify_current,
    passes_to_trust_fix,
    run_rows,
    severity,
)
from flaky_detector.store import load_runs, save_run


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
    assert classify_current(runs, 0, {"t": verdict})[0]["kind"] == "passed-on-retry"


def test_skipped_attempts_are_ignored():
    runs = [run(0, {"t": ("skipped", "a")}), run(1, {"t": ("passed", "a")})]

    assert analyze(runs)["t"].observations == 1


def precise_run(index, outcome, deps, files):
    record = run(index, {})
    paths = sorted(files)
    record["files"] = [[path, files[path]] for path in paths]
    record["tests"]["t"] = {"attempts": [outcome], "fingerprint": f"per-run-{index}",
                            "deps": [paths.index(path) for path in deps]}
    return record


def test_failure_path_touching_extra_files_is_still_same_code():
    files = {"app.py": "a1", "errors.py": "e1", "test_app.py": "t1"}
    runs = [
        precise_run(0, "passed", ["app.py", "test_app.py"], files),
        precise_run(1, "failed", ["app.py", "errors.py", "test_app.py"], files),  # error path ran errors.py
    ]

    assert analyze(runs)["t"].status == FLAKY


def test_change_to_a_file_only_the_failure_path_runs_is_new_code():
    runs = [
        precise_run(0, "passed", ["app.py", "test_app.py"], {"app.py": "a1", "errors.py": "e1", "test_app.py": "t1"}),
        precise_run(1, "failed", ["app.py", "errors.py", "test_app.py"],
                    {"app.py": "a1", "errors.py": "e2", "test_app.py": "t1"}),
    ]

    assert analyze(runs)["t"].status == FAILING


def test_environment_specific_failure_is_not_flaky():
    runs = []
    for i in range(3):
        for env, outcome in (("utc", "passed"), ("pst", "failed")):
            record = run(i, {"t": (outcome, "same-code")})
            record["environment"] = env
            runs.append(record)

    verdicts = analyze(runs)

    assert verdicts["t [utc]"].status == HEALTHY
    assert verdicts["t [pst]"].status == FAILING
    assert classify_current(runs, len(runs) - 1, verdicts)[0]["key"] == "t [pst]"


def test_matrix_environments_count_as_one_run_for_recency():
    runs = []
    for i in range(12):
        for env in ("a", "b"):
            outcome = "failed" if i == 0 and env == "a" else "passed"
            record = run(i, {"t": (outcome, "x")})
            record["environment"] = env
            runs.append(record)

    assert analyze(runs, window=11)["t [a]"].active is False
    assert analyze(runs, window=12)["t [a]"].active is True


def test_flake_fixed_by_a_code_change():
    runs = [run(i, {"t": ("failed" if i % 2 else "passed", "racy")}) for i in range(6)]  # 50% fail rate
    runs += [run(i, {"t": ("passed", "with-lock")}) for i in range(6, 11)]

    verdict = analyze(runs)["t"]

    assert verdict.status == FIXED
    assert verdict.fixed_in["index"] == 6
    assert verdict.passes_since_fix == 5
    assert verdict.severity is None


def test_fix_needs_enough_passes_for_the_old_failure_rate():
    runs = [run(0, {"t": ("failed", "racy")})] + [run(i, {"t": ("passed", "racy")}) for i in range(1, 10)]
    runs += [run(i, {"t": ("passed", "with-lock")}) for i in range(10, 20)]  # 10% rate needs 29 passes

    verdict = analyze(runs)["t"]

    assert verdict.status == FLAKY
    assert verdict.passes_since_fix == 10
    assert passes_to_trust_fix(0.1) == 29
    assert passes_to_trust_fix(0.5) == 5
    assert passes_to_trust_fix(0.9) == 3


def test_matrix_records_of_one_run_are_stored_side_by_side(tmp_path):
    for env in ("py3.11", "py3.12"):
        record = run(0, {"t": ("passed", "x")}) | {"environment": env, "schema": 1}
        save_run(record, tmp_path)

    assert [r["environment"] for r in load_runs(tmp_path)] == ["py3.11", "py3.12"]


def test_scheduled_runs_of_main_count_as_main():
    runs = [
        main_run(0, {"t": ("passed", "a")}),
        run(1, {"t": ("failed", "a")}, event="schedule", ref="refs/heads/main"),
        run(2, {"t": ("failed", "a")}, event="pull_request"),
    ]

    assert analyze(runs)["t"].main_failures == 1


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

    kinds = {item["key"]: item["kind"] for item in classify_current(runs, 1, verdicts)}

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
