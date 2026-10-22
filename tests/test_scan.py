from flaky_detector.analysis import FLAKY, analyze
from flaky_detector.scan import parse_pytest_log, scan
from flaky_detector.store import load_runs

LOG = """\
2026-10-21T01:33:50.0000000Z ##[group]Run uv run pytest -v
2026-10-21T01:33:56.1000000Z ============================= test session starts ==============================
2026-10-21T01:33:56.5859305Z tests/test_main.py::test_healthz PASSED                                  [ 25%]
2026-10-21T01:33:56.5859305Z tests/test_main.py::test_teardown PASSED                                 [ 50%]
2026-10-21T01:33:56.5859305Z tests/test_main.py::test_teardown ERROR                                  [ 50%]
2026-10-21T01:33:56.6000000Z \x1b[32mtests/test_main.py::TestApi::test_get[a b] PASSED\x1b[0m             [ 75%]
2026-10-21T01:33:56.7000000Z tests/test_main.py::test_flaky RERUN                                     [100%]
2026-10-21T01:33:56.8000000Z tests/test_main.py::test_flaky PASSED                                    [100%]
2026-10-21T01:33:57.0000000Z FAILED tests/test_main.py::test_teardown - assert 1 == 2
2026-10-21T01:34:00.0000000Z ============================= test session starts ==============================
2026-10-21T01:34:00.5000000Z [gw0] [ 50%] FAILED tests/test_main.py::test_healthz
"""


def test_parse_pytest_log():
    assert parse_pytest_log(LOG) == {
        "tests.test_main.test_healthz": ["passed", "failed"],  # second session is a new attempt
        "tests.test_main.test_teardown": ["error"],  # teardown error merges into the same attempt
        "tests.test_main.TestApi.test_get[a b]": ["passed"],
        "tests.test_main.test_flaky": ["failed", "passed"],  # RERUN starts a new attempt
    }


def test_ignores_logs_without_verbose_pytest_output():
    assert parse_pytest_log("Run npm test\n5 passing\nFAILED tests/x.py::test_a - boom\n") == {}


class FakeApi:
    def __init__(self, runs, jobs, logs):
        self.runs, self.jobs, self.logs = runs, jobs, logs
        self.log_requests = []

    def get(self, path):
        if "/actions/runs?" in path:
            return {"workflow_runs": self.runs if path.endswith("page=1") else []}
        run_id, attempt = path.split("/runs/")[1].split("/jobs")[0].split("/attempts/")
        return {"jobs": self.jobs[(int(run_id), int(attempt))]}

    def text(self, path):
        job_id = int(path.split("/jobs/")[1].split("/")[0])
        self.log_requests.append(job_id)
        return self.logs[job_id]


def _run(run_id, *, attempts=1, sha="a" * 40, event="push", message="Fix thing (#3)"):
    return {
        "id": run_id, "name": "CI", "path": ".github/workflows/ci.yml", "run_attempt": attempts,
        "event": event, "head_branch": "main", "head_sha": sha, "created_at": f"2026-10-2{run_id}T00:00:00Z",
        "head_commit": {"tree_id": "t" + sha[:5], "message": message}, "pull_requests": [],
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
    }


def _job(job_id, started, name="test", steps=("Test",), conclusion="success"):
    return {"id": job_id, "name": name, "conclusion": conclusion, "started_at": started,
            "steps": [{"name": s} for s in steps]}


def test_scan_builds_records_and_detects_rerun_flake(tmp_path):
    passing = "tests/test_a.py::test_x PASSED [100%]\n"
    failing = "tests/test_a.py::test_x FAILED [100%]\n"
    api = FakeApi(
        runs=[_run(1, attempts=2), _run(2, sha="b" * 40, message="Merge pull request #9 from me/branch")],
        jobs={
            (1, 1): [_job(11, "2026-10-21T00:00:00Z"), _job(12, "2026-10-21T00:00:01Z", name="build", steps=("Build",))],
            (1, 2): [_job(13, "2026-10-21T01:00:00Z")],
            (2, 1): [_job(21, "2026-10-22T00:00:00Z"), _job(22, "2026-10-22T00:00:00Z", name="deploy", conclusion="skipped")],
        },
        logs={11: failing, 13: passing, 21: passing},
    )
    store = tmp_path / "runs"

    saved = scan(api, "o/r", store)

    assert len(saved) == 3
    assert sorted(api.log_requests) == [11, 13, 21]  # build job has no test step; deploy was skipped
    runs = load_runs(store)
    assert [(r["run_id"], r["run_attempt"]) for r in runs] == [("1-test", 1), ("1-test", 2), ("2-test", 1)]
    assert runs[0]["ref"] == "refs/heads/main"
    assert runs[0]["tests"]["tests.test_a.test_x"]["fingerprint"] == "tree:taaaaa:test"
    assert runs[2]["merged_pr"] == 9
    # The re-run attempt passed on the same commit the first attempt failed on.
    verdict = analyze(runs)["tests.test_a.test_x"]
    assert verdict.status == FLAKY
    assert verdict.first_flaky_failure["run_id"] == "1-test"

    # Scanning again only fetches what is new.
    api.log_requests.clear()
    assert scan(api, "o/r", store) == []
    assert api.log_requests == []
