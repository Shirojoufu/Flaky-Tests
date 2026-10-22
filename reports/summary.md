# Flaky test report

**Runs analysed:** 6 (6 on main, 0 from pull requests) · **Tests tracked:** 29 · **Flaky:** 0 (🔴 0 critical, 🟠 0 high, 🟡 0 medium, ⚪ 0 low) · **Consistently failing:** 0

## This run

main · commit `4c1f6f0` · Document union fingerprints, the lab, and the read-only test job pattern

All 29 tests passed.

## Flaky tests

No flaky tests detected: no test has both passed and failed on the same code.

## Trends

```mermaid
xychart-beta
    title "First-attempt pass rate (%), last 6 runs"
    x-axis ["c9eafd7", "c120253", "fa52739", "37ca78a", "fcbe75a", "4c1f6f0"]
    y-axis "Pass rate" 0 --> 100
    line [96.15, 100.0, 100.0, 100.0, 100.0, 100.0]
```

```mermaid
xychart-beta
    title "Flaky failures per run, last 6 runs"
    x-axis ["c9eafd7", "c120253", "fa52739", "37ca78a", "fcbe75a", "4c1f6f0"]
    y-axis "Flaky failures" 0 --> 1
    bar [0, 0, 0, 0, 0, 0]
```

```mermaid
xychart-beta
    title "Line coverage (%), last 6 runs"
    x-axis ["c9eafd7", "c120253", "fa52739", "37ca78a", "fcbe75a", "4c1f6f0"]
    y-axis "Coverage" 0 --> 100
    line [88.65, 92.11, 92.13, 92.54, 92.26, 92.26]
```

## Coverage

**Total:** 92.26% of 969 statements (+0.00 vs main at `fcbe75a`)

### Files no test executes

- `flaky_detector/__main__.py`

### Files below 80% coverage

| File | Coverage | Missing lines |
| --- | --- | --- |
| `flaky_detector/__main__.py` | 0.0% | 1, 3 |
| `flaky_detector/cli.py` | 79.34% | 105-106, 126-127, 130-131, 144, 152-154, 158-159, 161-168, 172, 174-175, 181-182 |

## Graph data

- `runs.csv`: one row per run: pass rate, failures, flaky failures, coverage, diff coverage
- `runs.json`: same as runs.csv, as JSON
- `test_outcomes.csv`: every test attempt in every run (long format, for heatmaps)
- `flaky_tests.json`: flaky and failing tests with stats, onset and per-run timeline
- `coverage_files.csv`: per-file coverage for the latest run with coverage
- `coverage_gaps.json`: files below threshold, never-executed files, uncovered changed lines
