# Flaky test detector

Runs in GitHub Actions on every pull request to `main` and every merge to `main`, and keeps a history of every test result on a `flaky-data` branch. From that history it finds:

- **Flaky tests**: tests that passed and failed on *identical code*.
- **When each flake started**: the first flaky failure, and the commit or PR that introduced the flaky code state.
- **How severe each flake is**: how often it fails, and how often it failed on `main`.
- **Real failures**: tests that fail every time on their current code. These are reported separately so they aren't mistaken for flakes.
- **Coverage and coverage gaps**: total coverage and its trend, files below a threshold, files no test executes, and the changed lines in each PR or merge that no test covers.

Each run writes a Markdown job summary with trend charts, plus CSV/JSON graph data for dashboards.

[Flaky Test Lab](https://github.com/Shirojoufu/Flaky-Test-Lab) is a test bed for the detector. It has races, connection timeouts, startup races, node crashes, disk errors, async ordering bugs, random-data edge cases, timing assertions and order dependence, plus real regressions as controls. Its simulator replays 30 CI runs and checks every verdict against ground truth; at the time of writing, all 19 tests are classified correctly.

## How "the code didn't change" is decided

A test depends on:

- the test file and any `conftest.py` above it
- every source file the test actually executed (from per-test coverage, `--cov-context=test`)
- dependency manifests and lockfiles (`pyproject.toml`, `requirements*.txt`, `uv.lock`, …)

Each run records the hash of every measured file and which files each test executed. At analysis time, each test is **fingerprinted over every file it executed in any run**. A failing run often executes extra code, such as an error handler or the code path that broke, and it is still compared with passing runs of the same code. A change to any of those files still counts as new code.

If the same test passes and fails with the same fingerprint, it is flaky. Changes to unrelated files don't change the fingerprint, so evidence builds up across many PRs and merges, not just reruns of the same commit. When the workflow retries a failed test and the retry passes, that also counts.

When a test has no per-test coverage data, its fingerprint falls back to the whole git tree, so any change at all counts as new code. `--fingerprint-mode tree` forces that strict mode for every test.

Limitation: only files coverage measures are tracked. If a test reads a data file or runs code in a subprocess, add those files with the `fingerprint-extra` input (`--fingerprint-extra` on the command line).

## Severity

Severity is computed from executions on flaky code states:

| Severity | Rule |
| --- | --- |
| 🔴 critical | fails ≥ 25% of the time (with ≥ 3 failures), or failed on ≥ 3 pushes to main |
| 🟠 high | fails ≥ 10%, or failed on 2 pushes to main |
| 🟡 medium | fails ≥ 3%, or failed at least twice |
| ⚪ low | everything else |

A single failure is capped at medium. A flake with no failure in the last 20 runs (`--window`) is marked *dormant* and drops one level.

## Adding it to a repository

The detector is a GitHub Action. Run your tests with JUnit XML and per-test coverage, retry failures once, then hand the results to the action:

```yaml
permissions:
  contents: write  # the action pushes run records to the flaky-data branch

jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0  # diff coverage needs the base commit
          persist-credentials: false  # fine: the action authenticates with its own token input

      # ... set up Python and install your project ...

      - name: Test
        id: tests
        continue-on-error: true
        run: pytest -v --junitxml=.flaky/junit.xml --cov --cov-context=test --cov-report=json:.flaky/coverage.json

      - name: Retry failed tests
        id: retry
        if: steps.tests.outcome == 'failure'
        continue-on-error: true
        run: pytest -v --last-failed --no-cov --junitxml=.flaky/junit-retry-1.xml

      # Pin the release's commit SHA, with its tag as a comment, so Dependabot can bump both
      - uses: Shirojoufu/Flaky-Tests@ec75748fc1a393599e37fe60872e412d57e89ae8 # v0.1.0
        if: always() && steps.tests.outcome != 'skipped'
        with:
          junit: .flaky/junit.xml
          retry-junit: .flaky/junit-retry-*.xml
          coverage: .flaky/coverage.json

      - name: Fail on real test failures
        if: steps.tests.outcome == 'failure' && steps.retry.outcome != 'success'
        run: exit 1
```

In each run, the action:

1. Loads past runs from the `flaky-data` branch, records this run, and analyses the full history.
2. Writes the report to the job summary, adds `::warning` annotations for flaky failures, and uploads the report and graph data as an artifact.
3. Appends the run record to `flaky-data`. On pushes to `main` it also refreshes `reports/` there, so the latest graph data is always at a stable URL. Runs from fork PRs and Dependabot PRs are reported but not recorded, because their token is read-only.

To keep the test job read-only, upload `.flaky/` as an artifact and run the action in a separate job with `contents: write`. [DORA-Metrics](https://github.com/Shirojoufu/DORA-Metrics/blob/main/.github/workflows/ci.yml) is set up this way.

With the retry and the final step above, the job fails only when a test still fails after its retry. A test that passed on retry is reported as flaky but doesn't block the PR.

Inputs are documented in [action.yml](action.yml). They cover the history branch, the main ref, the coverage threshold, `publish: auto|true|false` and the Python interpreter. This repo's own [workflow](.github/workflows/flaky-detector.yml) is a working example.

The `flaky-data` branch is created on the first run. Its layout:

```
runs/<timestamp>-<run id>-<attempt>.json   one record per CI run attempt
reports/                                    latest analysis (refreshed on main pushes)
```

## Outputs

| File | Contents |
| --- | --- |
| `summary.md` | the job summary: this run's failures, flaky tests, failing tests, trend charts, coverage gaps |
| `runs.csv` / `runs.json` | one row per run: pass rate, failures, flaky failures, failures after retry, coverage, diff coverage |
| `test_outcomes.csv` | every attempt of every test in every run, in long format (good for heatmaps) |
| `flaky_tests.json` | each flaky or failing test: stats, severity, onset, and a per-run timeline |
| `coverage_files.csv` | per-file coverage for the latest run |
| `coverage_gaps.json` | files below threshold, files never executed, uncovered changed lines |

## Coverage configuration

Per-test fingerprints need coverage with per-test contexts in the JSON report. Add `pytest-cov` to your test dependencies, and include your tests in `source` so fingerprints cover test code (test files are left out of the reported percentages):

   ```toml
   [tool.coverage.run]
   source = ["your_package", "tests"]
   relative_files = true
   core = "ctrace"   # Python 3.14+ defaults to sysmon, which records contexts incompletely

   [tool.coverage.json]
   show_contexts = true
   ```

If coverage isn't set up, leave out the `coverage` input. Fingerprints then fall back to the whole commit tree.

## Backfilling from past Actions runs

`scan` reads per-test results from the job logs of a repository's past runs, using your `gh` login. It works on any repo whose CI runs `pytest -v`, with no artifacts or setup needed:

```sh
python -m flaky_detector scan --repo owner/name --workflow CI --store history/runs
python -m flaky_detector analyze --store history/runs --out history/report
```

Past runs have no per-test coverage, so scanned tests are fingerprinted by commit tree plus job name. Only a pass and a fail on the same commit in the same job counts as flaky, which in practice means a re-run. Scanning again fetches only new jobs. GitHub keeps logs for 90 days by default.

## Running locally

```sh
pip install -e ".[test]"
pytest --junitxml=.flaky/junit.xml --cov --cov-context=test --cov-report=json:.flaky/coverage.json
python -m flaky_detector ingest --junit .flaky/junit.xml --coverage .flaky/coverage.json --store .flaky/history/runs
python -m flaky_detector analyze --store .flaky/history/runs --out .flaky/report
```

Run `python -m flaky_detector ingest --help` and `analyze --help` for all options (coverage threshold, exclusions, extra fingerprint files, and more).

## Releasing

1. Bump `version` in `pyproject.toml` and merge to `main`. CI's `lab-validation` job must pass.
2. Create a release tagged `vX.Y.Z` on that commit: `gh release create vX.Y.Z --target <sha> --generate-notes`.

Repos that pin the action by SHA with a `# vX.Y.Z` comment get a Dependabot PR for the new release on their next weekly `github-actions` check. Flaky-Test-Lab has no Dependabot config, so bump its two pins by hand.
