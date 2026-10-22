from flaky_detector.junit import ERROR, FAILED, PASSED, SKIPPED, parse_junit

REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
  <testcase classname="tests.test_app" name="test_ok" time="0.010"/>
  <testcase classname="tests.test_app" name="test_bad" time="0.020">
    <failure message="AssertionError: 1 != 2">trace</failure>
  </testcase>
  <testcase classname="tests.test_app.TestThing" name="test_param[a|b]" time="0.001">
    <skipped message="not today"/>
  </testcase>
  <testcase classname="tests.test_app" name="test_teardown" time="0.5"/>
  <testcase classname="tests.test_app" name="test_teardown" time="0.1">
    <error message="teardown blew up"/>
  </testcase>
</testsuite></testsuites>
"""


def test_parses_outcomes_and_keys(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(REPORT, encoding="utf-8")

    results = parse_junit(path)

    assert {k: r.outcome for k, r in results.items()} == {
        "tests.test_app.test_ok": PASSED,
        "tests.test_app.test_bad": FAILED,
        "tests.test_app.TestThing.test_param[a|b]": SKIPPED,
        "tests.test_app.test_teardown": ERROR,
    }
    assert results["tests.test_app.test_bad"].message == "AssertionError: 1 != 2"


def test_duplicate_entries_keep_worst_outcome_and_total_time(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(REPORT, encoding="utf-8")

    teardown = parse_junit(path)["tests.test_app.test_teardown"]

    assert teardown.outcome == ERROR
    assert teardown.duration == 0.6
