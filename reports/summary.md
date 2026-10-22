# Flaky test report

**Runs analysed:** 3 (3 on main, 0 from pull requests) · **Tests tracked:** 27 · **Flaky:** 0 (🔴 0 critical, 🟠 0 high, 🟡 0 medium, ⚪ 0 low) · **Consistently failing:** 0

## This run

main · commit `fa52739` · Count scheduled runs of main as main; add fingerprint-extra input

All 27 tests passed.

## Flaky tests

No flaky tests detected: no test has both passed and failed on the same code.

## Trends

```mermaid
xychart-beta
    title "First-attempt pass rate (%), last 3 runs"
    x-axis ["c9eafd7", "c120253", "fa52739"]
    y-axis "Pass rate" 0 --> 100
    line [96.15, 100.0, 100.0]
```

```mermaid
xychart-beta
    title "Flaky failures per run, last 3 runs"
    x-axis ["c9eafd7", "c120253", "fa52739"]
    y-axis "Flaky failures" 0 --> 1
    bar [0, 0, 0]
```

```mermaid
xychart-beta
    title "Line coverage (%), last 3 runs"
    x-axis ["c9eafd7", "c120253", "fa52739"]
    y-axis "Coverage" 0 --> 100
    line [88.65, 92.11, 92.13]
```

## Coverage

**Total:** 92.13% of 928 statements (+0.02 vs main at `c120253`) · **This change:** 100.0% of 8 changed statements covered

### Files no test executes

- `flaky_detector/__main__.py`

### Files below 80% coverage

| File | Coverage | Missing lines |
| --- | --- | --- |
| `flaky_detector/__main__.py` | 0.0% | 1, 3 |

## Graph data

- `runs.csv`: one row per run: pass rate, failures, flaky failures, coverage, diff coverage
- `runs.json`: same as runs.csv, as JSON
- `test_outcomes.csv`: every test attempt in every run (long format, for heatmaps)
- `flaky_tests.json`: flaky and failing tests with stats, onset and per-run timeline
- `coverage_files.csv`: per-file coverage for the latest run with coverage
- `coverage_gaps.json`: files below threshold, never-executed files, uncovered changed lines
