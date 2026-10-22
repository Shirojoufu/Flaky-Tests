import json

from flaky_detector import history
from flaky_detector.gitutil import auth_env, run_git
from flaky_detector.history import fetch_history, publish
from flaky_detector.store import run_filename


def _clone_with_remote(tmp_path, name, remote):
    work = tmp_path / name
    run_git(["clone", "-q", str(remote), str(work)], tmp_path)
    return work


def _record(tmp_path, run_id, timestamp):
    record = {
        "schema": 1, "run_id": run_id, "run_attempt": 1, "timestamp": timestamp, "sha": "abc",
        "event": "push", "ref": "refs/heads/main",
        "tests": {"t": {"attempts": ["passed"], "fingerprint": "f"}},
    }
    path = tmp_path / run_filename(record)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_publish_creates_branch_then_appends_from_another_clone(tmp_path, monkeypatch):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    remote = tmp_path / "remote.git"
    run_git(["init", "-q", "--bare", str(remote)], tmp_path)
    seed = _clone_with_remote(tmp_path, "seed", remote)
    (seed / "README").write_text("hi", encoding="utf-8")
    run_git(["add", "."], seed)
    run_git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], seed)
    run_git(["push", "-q", "origin", "HEAD:refs/heads/main"], seed)

    first = _clone_with_remote(tmp_path, "first", remote)
    second = _clone_with_remote(tmp_path, "second", remote)  # cloned before the branch existed
    publish(_record(tmp_path, "1", "2026-10-01T00:00:00Z"), first, "flaky-data")
    publish(_record(tmp_path, "2", "2026-10-02T00:00:00Z"), second, "flaky-data", with_reports=True)

    files = run_git(["ls-tree", "-r", "--name-only", "flaky-data"], remote).split()
    assert [f for f in files if f.startswith("runs/")] == [
        "runs/20261001T000000Z-1-1.json", "runs/20261002T000000Z-2-1.json"]
    assert "reports/summary.md" in files
    assert run_git(["log", "--format=%an", "flaky-data"], remote).split("\n")[0] == "github-actions[bot]"
    # Temporary worktrees and branches are cleaned up.
    assert run_git(["worktree", "list"], first).count("\n") == 1
    assert "flaky-data-new" not in run_git(["branch"], first)

    store = tmp_path / "fetched"
    assert fetch_history(_clone_with_remote(tmp_path, "third", remote), "flaky-data", store) == 2
    assert sorted(p.name for p in store.iterdir()) == ["20261001T000000Z-1-1.json", "20261002T000000Z-2-1.json"]


def test_fetch_history_without_branch(tmp_path):
    remote = tmp_path / "remote.git"
    run_git(["init", "-q", "--bare", str(remote)], tmp_path)
    work = _clone_with_remote(tmp_path, "work", remote)

    assert fetch_history(work, "flaky-data", tmp_path / "store") == 0


def test_auth_env_uses_config_env_not_arguments():
    env = auth_env("s3cret")

    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")
    assert "s3cret" not in env["GIT_CONFIG_VALUE_0"]
    assert auth_env(None) is None
