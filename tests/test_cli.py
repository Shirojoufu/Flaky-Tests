import json

import pytest

from flaky_detector.cli import main
from flaky_detector.gitutil import changed_lines, run_git
from flaky_detector.store import load_runs

JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_core" name="test_add" time="0.1">{add}</testcase>
  <testcase classname="tests.test_core" name="test_sub" time="0.1">{sub}</testcase>
</testsuite></testsuites>
"""
FAILURE = '<failure message="boom"/>'


def commit(repo, files, message):
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    run_git(["add", "-A"], repo)
    run_git(["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message], repo)
    return run_git(["rev-parse", "HEAD"], repo).strip()


def write_reports(tmp_path, name, *, add_fails=False, sub_fails=False):
    junit = tmp_path / f"{name}.xml"
    junit.write_text(JUNIT.format(add=FAILURE if add_fails else "", sub=FAILURE if sub_fails else ""), encoding="utf-8")
    coverage = tmp_path / f"{name}.json"
    coverage.write_text(json.dumps({"files": {
        "app/core.py": {"executed_lines": [1, 2], "missing_lines": [], "contexts": {
            "2": ["tests/test_core.py::test_add|run"]}},
        "app/other.py": {"executed_lines": [1, 2], "missing_lines": [4], "contexts": {
            "2": ["tests/test_core.py::test_sub|run"]}},
        "app/dead.py": {"executed_lines": [], "missing_lines": [1, 2]},
    }}), encoding="utf-8")
    return junit, coverage


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(["init", "-q"], repo)
    for name in ("GITHUB_EVENT_PATH", "GITHUB_REPOSITORY", "GITHUB_WORKFLOW", "GITHUB_SHA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    return repo


def ingest(repo, store, junit, coverage, monkeypatch, run_id, event=None):
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    if event:
        event_path = store.parent / f"event-{run_id}.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    args = ["ingest", "--junit", str(junit), "--coverage", str(coverage), "--store", str(store), "--root", str(repo)]
    assert main(args) == 0


def test_ingest_and_analyze_end_to_end(repo, tmp_path, monkeypatch, capsys):
    store = tmp_path / "history"
    first = commit(repo, {
        "app/core.py": "def add(a, b):\n    return a + b\n",
        "app/other.py": "def sub(a, b):\n    return a - b\n\nOFFSET = 0\n",
        "tests/test_core.py": "def test_add(): pass\ndef test_sub(): pass\n",
    }, "initial")

    # Run 1 and 2: same commit, test_add passes then fails -> flaky.
    ingest(repo, store, *write_reports(tmp_path, "r1"), monkeypatch, "1")
    ingest(repo, store, *write_reports(tmp_path, "r2", add_fails=True), monkeypatch, "2")

    # Run 3: a merged PR changes only other.py; test_sub starts failing -> real failure, not flaky.
    second = commit(repo, {"app/other.py": "def sub(a, b):\n    return a - b - 1\n\nOFFSET = 1\n"},
                    "Change sub (#7)")
    ingest(repo, store, *write_reports(tmp_path, "r3", sub_fails=True), monkeypatch, "3",
           event={"before": first, "head_commit": {"message": "Change sub (#7)"}})

    runs = load_runs(store)
    assert [r["run_id"] for r in runs] == ["1", "2", "3"]
    assert runs[2]["merged_pr"] == 7
    assert runs[2]["sha"] == second
    # test_add's code did not change, so its fingerprint is stable; test_sub's changed with other.py.
    assert len({r["tests"]["tests.test_core.test_add"]["fingerprint"] for r in runs}) == 1
    assert runs[1]["tests"]["tests.test_core.test_sub"]["fingerprint"] != runs[2]["tests"]["tests.test_core.test_sub"]["fingerprint"]
    assert runs[2]["diff_coverage"]["files"] == {"app/other.py": {"changed_statements": 2, "covered": 1, "uncovered": [[4, 4]]}}

    out = tmp_path / "report"
    summary_file = tmp_path / "step-summary.md"
    assert main(["analyze", "--store", str(store), "--out", str(out), "--summary-file", str(summary_file),
                 "--annotate"]) == 0

    flaky = {t["key"]: t for t in json.loads((out / "flaky_tests.json").read_text(encoding="utf-8"))}
    assert flaky["tests.test_core.test_add"]["status"] == "flaky"
    assert flaky["tests.test_core.test_sub"]["status"] == "failing"
    assert flaky["tests.test_core.test_sub"]["failing_since"]["merged_pr"] == 7

    gaps = json.loads((out / "coverage_gaps.json").read_text(encoding="utf-8"))
    assert gaps["never_executed"] == ["app/dead.py"]

    summary = summary_file.read_text(encoding="utf-8")
    assert "`tests.test_core.test_add`" in summary
    assert "Consistently failing tests" in summary
    assert "Untested lines in this change" in summary
    for name in ("runs.csv", "runs.json", "test_outcomes.csv", "coverage_files.csv", "summary.md"):
        assert (out / name).is_file()
    assert "::warning" not in capsys.readouterr().out  # the current run's only failure is a real one


def test_ingest_requires_junit(tmp_path, capsys):
    assert main(["ingest", "--junit", str(tmp_path / "missing.xml"), "--store", str(tmp_path)]) == 2
    assert "not found" in capsys.readouterr().err


def test_changed_lines_parses_added_and_modified_lines(repo):
    base = commit(repo, {"a.py": "1\n2\n3\n", "gone.py": "x\n"}, "base")
    (repo / "gone.py").unlink()
    head = commit(repo, {"a.py": "1\ntwo\n3\n4\n", "b.py": "+++ tricky\n"}, "head")

    assert changed_lines(base, head, repo) == {"a.py": {2, 4}, "b.py": {1}}
