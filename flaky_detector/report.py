"""Write the analysis as Markdown (for the job summary) plus CSV and JSON graph data."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .analysis import FAILING, FLAKY, HEALTHY, MAIN_REF, SEVERITIES, Verdict, classify_current, is_main_push, run_rows

SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}
TABLE_LIMIT = 50
CHART_POINTS = 30

GRAPH_FILES = {
    "runs.csv": "one row per run: pass rate, failures, flaky failures, coverage, diff coverage",
    "runs.json": "same as runs.csv, as JSON",
    "test_outcomes.csv": "every test attempt in every run (long format, for heatmaps)",
    "flaky_tests.json": "flaky and failing tests with stats, onset and per-run timeline",
    "coverage_files.csv": "per-file coverage for the latest run with coverage",
    "coverage_gaps.json": "files below threshold, never-executed files, uncovered changed lines",
}


def write_reports(
    runs: list[dict],
    verdicts: dict[str, Verdict],
    out: Path,
    *,
    current_index: int,
    threshold: float = 80.0,
    main_ref: str = MAIN_REF,
) -> str:
    out.mkdir(parents=True, exist_ok=True)
    rows = run_rows(runs, verdicts, main_ref)
    _write_csv(out / "runs.csv", rows)
    _write_json(out / "runs.json", rows)
    _write_outcomes(out / "test_outcomes.csv", runs)
    _write_json(out / "flaky_tests.json", [v.to_dict() for v in verdicts.values() if v.status != HEALTHY])
    gaps = coverage_gaps(runs, current_index, threshold, main_ref)
    _write_json(out / "coverage_gaps.json", gaps)
    if gaps["available"]:
        files = runs[gaps["run_index"]]["coverage"]["files"]
        _write_csv(
            out / "coverage_files.csv",
            [
                {"file": path, "statements": f["statements"], "covered": f["covered"], "percent": f["percent"],
                 "missing": _format_ranges(f["missing"])}
                for path, f in files.items()
            ],
        )
    markdown = render_markdown(runs, verdicts, rows, gaps, current_index, main_ref)
    (out / "summary.md").write_text(markdown, encoding="utf-8")
    return markdown


def coverage_gaps(runs: list[dict], current_index: int, threshold: float, main_ref: str = MAIN_REF) -> dict:
    index = next((i for i in range(current_index, -1, -1) if runs[i].get("coverage")), None)
    if index is None:
        return {"available": False}
    coverage = runs[index]["coverage"]
    baseline_index = next(
        (i for i in range(index - 1, -1, -1) if runs[i].get("coverage") and is_main_push(runs[i], main_ref)),
        None,
    )
    baseline = None
    if baseline_index is not None:
        base_percent = runs[baseline_index]["coverage"]["percent"]
        baseline = {
            "run_index": baseline_index,
            "sha": runs[baseline_index].get("sha"),
            "percent": base_percent,
            "delta": round(coverage["percent"] - base_percent, 2),
        }
    files = coverage["files"]
    below = sorted(
        (
            {"file": path, "percent": f["percent"], "statements": f["statements"], "covered": f["covered"],
             "missing": f["missing"]}
            for path, f in files.items()
            if f["statements"] and f["percent"] < threshold
        ),
        key=lambda f: (f["percent"], f["file"]),
    )
    return {
        "available": True,
        "run_index": index,
        "sha": runs[index].get("sha"),
        "percent": coverage["percent"],
        "statements": coverage["statements"],
        "covered": coverage["covered"],
        "threshold": threshold,
        "baseline": baseline,
        "below_threshold": below,
        "never_executed": [path for path, f in files.items() if f["statements"] and not f["covered"]],
        "diff": runs[index].get("diff_coverage"),
    }


def render_markdown(runs, verdicts, rows, gaps, current_index, main_ref=MAIN_REF) -> str:
    flaky = [v for v in verdicts.values() if v.status == FLAKY]
    failing = [v for v in verdicts.values() if v.status == FAILING]
    by_severity = {s: sum(1 for v in flaky if v.severity == s) for s in SEVERITIES}
    main_runs = sum(1 for r in runs if is_main_push(r, main_ref))
    pr_runs = sum(1 for r in runs if r.get("event") == "pull_request")

    lines = ["# Flaky test report", ""]
    lines.append(
        f"**Runs analysed:** {len(runs)} ({main_runs} on main, {pr_runs} from pull requests) · "
        f"**Tests tracked:** {len(verdicts)} · **Flaky:** {len(flaky)} "
        f"({', '.join(f'{SEVERITY_ICON[s]} {n} {s}' for s, n in by_severity.items())}) · "
        f"**Consistently failing:** {len(failing)}"
    )
    lines.append("")

    lines += _current_section(runs, current_index, verdicts)
    lines += _flaky_section(flaky)
    lines += _failing_section(failing)
    lines += _trend_section(rows)
    lines += _coverage_section(gaps)

    lines += ["## Graph data", ""]
    lines += [f"- `{name}`: {what}" for name, what in GRAPH_FILES.items()]
    lines.append("")
    return "\n".join(lines)


def _current_section(runs: list[dict], index: int, verdicts: dict[str, Verdict]) -> list[str]:
    run = runs[index]
    lines = ["## This run", "", f"{_where(run)} · commit `{_short(run.get('sha'))}`"]
    if run.get("commit_message"):
        lines[-1] += f" · {_cell(run['commit_message'])}"
    lines.append("")
    failures = classify_current(runs, index, verdicts)
    if not failures:
        lines += [f"All {sum(1 for t in run.get('tests', {}).values() if t.get('attempts'))} tests passed.", ""]
        return lines
    labels = {
        "passed-on-retry": "🔁 flaky: failed, then passed on retry",
        "known-flaky": "⚠️ known flaky: failed on code where it has also passed",
        "failure": "❌ failure: no evidence of flakiness, likely a real failure",
    }
    lines += ["| Test | Verdict | Severity | Message |", "| --- | --- | --- | --- |"]
    for item in failures:
        severity = f"{SEVERITY_ICON[item['severity']]} {item['severity']}" if item["severity"] else ""
        lines.append(
            f"| `{_cell(item['key'])}` | {labels[item['kind']]} | {severity} | {_cell(item['message'][:120])} |"
        )
    lines.append("")
    return lines


def _flaky_section(flaky: list[Verdict]) -> list[str]:
    lines = ["## Flaky tests", ""]
    if not flaky:
        return lines + ["No flaky tests detected: no test has both passed and failed on the same code.", ""]
    lines += [
        "A test is flaky when it passed and failed on identical code (same test, conftest, dependency "
        "manifests and executed source files). *Flaky code since* is the first run on the code state "
        "that turned out flaky. The commit before it is the most likely place the flake started.",
        "",
        "| Severity | Test | Fail rate | Flips | Broke main | First flaky failure | Flaky code since | Last failure |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for v in flaky[:TABLE_LIMIT]:
        dormant = "" if v.active else " (dormant)"
        lines.append(
            f"| {SEVERITY_ICON[v.severity]} {v.severity}{dormant} | `{_cell(v.key)}` "
            f"| {v.failure_rate:.0%} ({v.flaky_failures}/{v.flaky_observations}) | {v.flips} "
            f"| {v.main_failures} | {_ref(v.first_flaky_failure)} | {_ref(v.introduced_in)}"
            f"{' (oldest run in history)' if v.previous_code_seen_in is None else ''} "
            f"| {_ref(v.last_failure)} |"
        )
    if len(flaky) > TABLE_LIMIT:
        lines.append(f"\n…and {len(flaky) - TABLE_LIMIT} more in `flaky_tests.json`.")
    lines.append("")
    return lines


def _failing_section(failing: list[Verdict]) -> list[str]:
    if not failing:
        return []
    lines = [
        "## Consistently failing tests",
        "",
        "These fail on their current code and have never passed on it: likely real breakages, not flakes.",
        "",
        "| Test | Failing since | Failures |",
        "| --- | --- | --- |",
    ]
    for v in failing[:TABLE_LIMIT]:
        lines.append(f"| `{_cell(v.key)}` | {_ref(v.failing_since)} | {v.failures} |")
    lines.append("")
    return lines


def _trend_section(rows: list[dict]) -> list[str]:
    recent = [r for r in rows if r["pass_rate"] is not None][-CHART_POINTS:]
    if len(recent) < 2:
        return []
    labels = _labels(recent)
    lines = [
        "## Trends",
        "",
        "```mermaid",
        "xychart-beta",
        f'    title "First-attempt pass rate (%), last {len(recent)} runs"',
        f"    x-axis [{labels}]",
        '    y-axis "Pass rate" 0 --> 100',
        f"    line [{', '.join(str(r['pass_rate']) for r in recent)}]",
        "```",
        "",
        "```mermaid",
        "xychart-beta",
        f'    title "Flaky failures per run, last {len(recent)} runs"',
        f"    x-axis [{labels}]",
        f'    y-axis "Flaky failures" 0 --> {max(1, *(r["flaky_failures"] for r in recent))}',
        f"    bar [{', '.join(str(r['flaky_failures']) for r in recent)}]",
        "```",
        "",
    ]
    covered = [r for r in rows if r["coverage"] is not None][-CHART_POINTS:]
    if len(covered) >= 2:
        lines += [
            "```mermaid",
            "xychart-beta",
            f'    title "Line coverage (%), last {len(covered)} runs"',
            f"    x-axis [{_labels(covered)}]",
            '    y-axis "Coverage" 0 --> 100',
            f"    line [{', '.join(str(r['coverage']) for r in covered)}]",
            "```",
            "",
        ]
    return lines


def _labels(rows: list[dict]) -> str:
    return ", ".join(f'"{_short(r["sha"])}"' for r in rows)


def _coverage_section(gaps: dict) -> list[str]:
    lines = ["## Coverage", ""]
    if not gaps["available"]:
        return lines + ["No coverage data. Run pytest with `--cov --cov-context=test --cov-report=json`.", ""]
    summary = f"**Total:** {gaps['percent']}% of {gaps['statements']} statements"
    if gaps["baseline"]:
        b = gaps["baseline"]
        summary += f" ({b['delta']:+.2f} vs main at `{_short(b['sha'])}`)"
    diff = gaps.get("diff")
    if diff and diff["percent"] is not None:
        summary += f" · **This change:** {diff['percent']}% of {diff['changed_statements']} changed statements covered"
    lines += [summary, ""]

    if diff and any(f["uncovered"] for f in diff["files"].values()):
        lines += ["### Untested lines in this change", "", "| File | Uncovered changed lines |", "| --- | --- |"]
        for path, f in diff["files"].items():
            if f["uncovered"]:
                lines.append(f"| `{_cell(path)}` | {_format_ranges(f['uncovered'])} |")
        lines.append("")
    if diff and diff["unmeasured_files"]:
        lines += ["Changed files no test executed: " + ", ".join(f"`{p}`" for p in diff["unmeasured_files"]), ""]

    if gaps["never_executed"]:
        lines += ["### Files no test executes", ""]
        lines += [f"- `{path}`" for path in gaps["never_executed"][:TABLE_LIMIT]]
        lines.append("")
    if gaps["below_threshold"]:
        lines += [
            f"### Files below {gaps['threshold']:g}% coverage",
            "",
            "| File | Coverage | Missing lines |",
            "| --- | --- | --- |",
        ]
        for f in gaps["below_threshold"][:TABLE_LIMIT]:
            lines.append(f"| `{_cell(f['file'])}` | {f['percent']}% | {_format_ranges(f['missing'], limit=12)} |")
        lines.append("")
    return lines


def _where(run: dict) -> str:
    if run.get("pr_number"):
        return f"PR #{run['pr_number']}"
    ref = run.get("ref") or ""
    if ref.startswith("refs/heads/"):
        branch = ref.removeprefix("refs/heads/")
        if run.get("merged_pr"):
            return f"{branch} (merge of #{run['merged_pr']})"
        return branch if run.get("event") == "push" else f"{branch} ({run.get('event')})"
    return run.get("event") or "run"


def _ref(ref: dict | None) -> str:
    if not ref:
        return ""
    date = (ref.get("timestamp") or "")[:10]
    return f"`{_short(ref.get('sha'))}` {_where(ref)} {date}".strip()


def _short(sha: str | None) -> str:
    return (sha or "unknown")[:7]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _format_ranges(ranges: list[list[int]], limit: int | None = None) -> str:
    parts = [str(a) if a == b else f"{a}-{b}" for a, b in ranges]
    if limit and len(parts) > limit:
        parts = parts[:limit] + [f"…(+{len(parts) - limit})"]
    return ", ".join(parts)


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_outcomes(path: Path, runs: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run_index", "timestamp", "sha", "event", "pr_number", "test", "attempt", "outcome",
                         "fingerprint"])
        for index, run in enumerate(runs):
            for key, test in sorted(run.get("tests", {}).items()):
                for attempt, outcome in enumerate(test.get("attempts", [])):
                    writer.writerow([index, run.get("timestamp"), run.get("sha"), run.get("event"),
                                     run.get("pr_number") or "", key, attempt, outcome, test.get("fingerprint")])
