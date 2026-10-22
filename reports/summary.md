# Flaky test report

**Runs analysed:** 1 (1 on main, 0 from pull requests) · **Tests tracked:** 26 · **Flaky:** 0 (🔴 0 critical, 🟠 0 high, 🟡 0 medium, ⚪ 0 low) · **Consistently failing:** 1

## This run

main · commit `c9eafd7` · Quote action input description containing a colon

| Test | Verdict | Severity | Message |
| --- | --- | --- | --- |
| `tests.test_history.test_publish_creates_branch_then_appends_from_another_clone` | ❌ failure: no evidence of flakiness, likely a real failure |  | subprocess.CalledProcessError: Command '['git', 'worktree', 'add', '--quiet', '--detach', '/tmp/flaky-history-8awddfhq/h |

## Flaky tests

No flaky tests detected: no test has both passed and failed on the same code.

## Consistently failing tests

These fail on their current code and have never passed on it: likely real breakages, not flakes.

| Test | Failing since | Failures |
| --- | --- | --- |
| `tests.test_history.test_publish_creates_branch_then_appends_from_another_clone` | `c9eafd7` main 2026-10-22 | 2 |

## Coverage

**Total:** 88.65% of 925 statements

### Files no test executes

- `flaky_detector/__main__.py`

### Files below 80% coverage

| File | Coverage | Missing lines |
| --- | --- | --- |
| `flaky_detector/__main__.py` | 0.0% | 1, 3 |
| `flaky_detector/history.py` | 57.89% | 30-41, 72, 76-83, 85-86, 88-92, 97-99, 105 |

## Graph data

- `runs.csv`: one row per run: pass rate, failures, flaky failures, coverage, diff coverage
- `runs.json`: same as runs.csv, as JSON
- `test_outcomes.csv`: every test attempt in every run (long format, for heatmaps)
- `flaky_tests.json`: flaky and failing tests with stats, onset and per-run timeline
- `coverage_files.csv`: per-file coverage for the latest run with coverage
- `coverage_gaps.json`: files below threshold, never-executed files, uncovered changed lines
