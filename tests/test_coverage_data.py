from flaky_detector.coverage_data import (
    dependencies_by_test,
    diff_coverage,
    mangle_nodeid,
    ranges,
    summarize,
)

RAW = {
    "files": {
        "app/core.py": {
            "executed_lines": [1, 2, 3, 5],
            "missing_lines": [6, 7, 9],
            "contexts": {
                "1": [""],
                "2": ["tests/test_core.py::test_add|run"],
                "3": ["tests/test_core.py::TestMul::test_mul[2-3]|run", "tests/test_core.py::test_add|run"],
            },
        },
        "app\\unused.py": {"executed_lines": [], "missing_lines": [1, 2]},
        "tests/test_core.py": {
            "executed_lines": [1, 2],
            "missing_lines": [],
            "contexts": {"2": ["tests/test_core.py::test_add|setup"]},
        },
    }
}


def test_mangle_matches_pytest_junit_names():
    assert mangle_nodeid("tests/test_core.py::test_add") == "tests.test_core.test_add"
    assert mangle_nodeid("tests/sub/test_x.py::TestA::test_b[1.py-a/b]") == "tests.sub.test_x.TestA.test_b[1.py-a/b]"


def test_ranges_collapse_consecutive_lines():
    assert ranges([9, 1, 2, 3, 5, 6]) == [[1, 3], [5, 6], [9, 9]]
    assert ranges([]) == []


def test_summary_excludes_tests_and_normalises_paths():
    summary = summarize(RAW)

    assert list(summary["files"]) == ["app/core.py", "app/unused.py"]
    assert summary["files"]["app/core.py"] == {"statements": 7, "covered": 4, "percent": 57.14, "missing": [[6, 7], [9, 9]]}
    assert summary["statements"] == 9
    assert summary["covered"] == 4
    assert summary["percent"] == 44.44


def test_dependencies_per_test():
    deps = dependencies_by_test(RAW)

    assert deps["tests.test_core.test_add"] == {"file": "tests/test_core.py", "deps": {"app/core.py", "tests/test_core.py"}}
    assert deps["tests.test_core.TestMul.test_mul[2-3]"]["deps"] == {"app/core.py"}
    assert "" not in deps


def test_diff_coverage_reports_uncovered_changed_statements():
    changed = {
        "app/core.py": {3, 4, 6, 7},  # 4 is not a statement
        "app/new.py": {1, 2},  # never imported by any test
        "README.md": {1},
        "tests/test_core.py": {2},
    }

    diff = diff_coverage(RAW, changed)

    assert diff["changed_statements"] == 3
    assert diff["covered"] == 1
    assert diff["percent"] == 33.33
    assert diff["files"] == {"app/core.py": {"changed_statements": 3, "covered": 1, "uncovered": [[6, 7]]}}
    assert diff["unmeasured_files"] == ["app/new.py"]


def test_diff_coverage_without_measurable_changes():
    assert diff_coverage(RAW, {"README.md": {1}})["percent"] is None
