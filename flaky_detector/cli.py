"""Command line.

In CI: ``fetch-history``, then ``ingest`` this run, ``analyze`` the history, and ``publish``
the new run record. ``scan`` backfills history from a repository's past Actions runs.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import datetime
from pathlib import Path

from . import coverage_data
from .analysis import FLAKY, MAIN_REF, analyze, classify_current
from .fingerprint import DEFAULT_EXTRA, PRECISE, TREE
from .history import DEFAULT_BRANCH, fetch_history, publish
from .ingest import build_record
from .report import write_reports
from .scan import GhApi, scan
from .store import load_run, load_runs, run_identity, save_run

TOKEN_ENV = "FLAKY_GIT_TOKEN"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flaky-detector",
        description="Detect flaky tests by comparing outcomes on unchanged code across CI runs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="record one run's test and coverage results")
    ingest.add_argument("--junit", type=Path, required=True, help="JUnit XML from the main test run")
    ingest.add_argument("--rerun", nargs="*", default=[], metavar="PATH",
                        help="JUnit XML from retries of failed tests (globs allowed, missing files ignored)")
    ingest.add_argument("--coverage", type=Path, help="coverage.py JSON report made with --cov-context=test")
    ingest.add_argument("--store", type=Path, required=True, help="directory of run records")
    ingest.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    ingest.add_argument("--base-sha", help="commit to diff against for diff coverage (default: from the event)")
    ingest.add_argument("--timestamp", type=datetime.fromisoformat,
                        help="when the run happened, ISO 8601 (default: now)")
    ingest.add_argument("--exclude", nargs="*", default=list(coverage_data.DEFAULT_EXCLUDE), metavar="GLOB",
                        help="files left out of coverage percentages (default: test files)")
    ingest.add_argument("--fingerprint-extra", nargs="*", default=[], metavar="GLOB",
                        help="more files every test depends on, added to the defaults "
                             f"({', '.join(DEFAULT_EXTRA)}); e.g. code run in subprocesses")
    ingest.add_argument("--fingerprint-mode", choices=[PRECISE, TREE], default=PRECISE,
                        help="precise: files each test executed; tree: the whole commit tree")

    report = commands.add_parser("analyze", help="detect flaky tests and write reports")
    report.add_argument("--store", type=Path, required=True, help="directory of run records")
    report.add_argument("--out", type=Path, required=True, help="directory for the reports")
    report.add_argument("--current", type=Path, help="run record to report on (default: the newest)")
    report.add_argument("--window", type=int, default=20, help="runs that count as recent (default: 20)")
    _report_options(report)
    report.add_argument("--summary-file", type=Path, help="append the Markdown report here, e.g. $GITHUB_STEP_SUMMARY")
    report.add_argument("--annotate", action="store_true", help="print GitHub Actions warnings for flaky failures")

    fetch = commands.add_parser("fetch-history", help="copy run records from the history branch")
    fetch.add_argument("--store", type=Path, required=True, help="directory to copy run records into")
    _history_options(fetch)

    push = commands.add_parser("publish", help="append a run record to the history branch and push it")
    push.add_argument("run_file", type=Path, help="run record written by ingest")
    push.add_argument("--with-reports", action="store_true", help="also regenerate reports/ on the branch")
    push.add_argument("--best-effort", action="store_true",
                      help="warn instead of failing when the push is refused, e.g. with a read-only token")
    _history_options(push)
    _report_options(push)

    backfill = commands.add_parser("scan", help="backfill history from past GitHub Actions runs (uses gh)")
    backfill.add_argument("--repo", required=True, help="owner/name")
    backfill.add_argument("--store", type=Path, required=True, help="directory of run records")
    backfill.add_argument("--workflow", help="only runs of this workflow (name or file name)")
    backfill.add_argument("--limit", type=int, default=200, help="most recent runs to read (default: 200)")
    backfill.add_argument("--job", help="only jobs whose name matches this regex")
    backfill.add_argument("--step", default="test",
                          help="only jobs with a step matching this regex (default: test; '' for all jobs)")

    args = parser.parse_args(argv)
    handlers = {"ingest": _ingest, "analyze": _analyze, "fetch-history": _fetch, "publish": _publish, "scan": _scan}
    return handlers[args.command](args)


def _report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coverage-threshold", type=float, default=80.0, help="flag files below this percent")
    parser.add_argument("--main-ref", default=MAIN_REF, help=f"ref of the main branch (default: {MAIN_REF})")


def _history_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, default=Path("."), help="git checkout to use (default: .)")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help=f"history branch (default: {DEFAULT_BRANCH})")
    parser.epilog = f"Set {TOKEN_ENV} to authenticate to GitHub when the checkout has no stored credentials."


def _ingest(args: argparse.Namespace) -> int:
    if not args.junit.is_file():
        print(f"error: JUnit report {args.junit} not found; did the test run crash?", file=sys.stderr)
        return 2
    coverage = args.coverage
    if coverage and not coverage.is_file():
        print(f"warning: coverage report {coverage} not found; continuing without coverage", file=sys.stderr)
        coverage = None
    record = build_record(
        junit=args.junit,
        reruns=_expand(args.rerun),
        coverage=coverage,
        root=args.root.resolve(),
        env=dict(os.environ),
        base_sha=args.base_sha,
        exclude=tuple(args.exclude),
        extra=(*DEFAULT_EXTRA, *args.fingerprint_extra),
        mode=args.fingerprint_mode,
        now=args.timestamp,
    )
    print(save_run(record, args.store))
    return 0


def _analyze(args: argparse.Namespace) -> int:
    runs = load_runs(args.store)
    if not runs:
        print(f"error: no run records in {args.store}", file=sys.stderr)
        return 2
    current_index = len(runs) - 1
    if args.current:
        identity = run_identity(load_run(args.current))
        current_index = next((i for i, r in enumerate(runs) if run_identity(r) == identity), current_index)

    verdicts = analyze(runs, window=args.window, main_ref=args.main_ref)
    markdown = write_reports(
        runs, verdicts, args.out,
        current_index=current_index, threshold=args.coverage_threshold, main_ref=args.main_ref,
    )
    if args.summary_file:
        with args.summary_file.open("a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    if args.annotate:
        for item in classify_current(runs, current_index, verdicts):
            if item["kind"] != "failure":
                print(f"::warning title=Flaky test ({item['severity'] or 'new'})::{item['key']} "
                      f"failed on code where it also passes (attempts: {', '.join(item['attempts'])})")
    flaky = sum(1 for v in verdicts.values() if v.status == FLAKY)
    print(f"Analysed {len(runs)} runs, {len(verdicts)} tests, {flaky} flaky. Reports in {args.out}", file=sys.stderr)
    return 0


def _fetch(args: argparse.Namespace) -> int:
    count = fetch_history(args.repo, args.branch, args.store, token=os.environ.get(TOKEN_ENV))
    print(f"Fetched {count} run records from {args.branch}", file=sys.stderr)
    return 0


def _publish(args: argparse.Namespace) -> int:
    try:
        publish(args.run_file, args.repo, args.branch, token=os.environ.get(TOKEN_ENV),
                with_reports=args.with_reports, main_ref=args.main_ref, threshold=args.coverage_threshold)
    except RuntimeError as exc:
        if args.best_effort:
            print(f"::warning title=Run not recorded::{exc}; this run is reported but not saved to history")
            return 0
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Recorded {args.run_file.name} on {args.branch}", file=sys.stderr)
    return 0


def _scan(args: argparse.Namespace) -> int:
    saved = scan(GhApi(), args.repo, args.store, workflow=args.workflow, limit=args.limit,
                 job_pattern=args.job, step_pattern=args.step or None)
    print(f"Saved {len(saved)} new run records to {args.store}", file=sys.stderr)
    return 0


def _expand(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern)) or ([pattern] if Path(pattern).is_file() else [])
        paths += [Path(match) for match in matches]
    return paths
