"""Keep the run history on a git branch: fetch it before analysis, append to it after."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from .analysis import MAIN_REF, analyze
from .gitutil import auth_env, run_git, run_git_bytes
from .report import write_reports
from .store import load_runs

DEFAULT_BRANCH = "flaky-data"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def fetch_history(repo: Path, branch: str, store: Path, token: str | None = None) -> int:
    """Copy the branch's run records into ``store``. Returns how many there were (0 if no branch yet)."""
    env = auth_env(token)
    if not _fetch(repo, branch, env):
        return 0
    try:
        archive = run_git_bytes(["archive", "--format=tar", "FETCH_HEAD", "runs"], repo, env)
    except subprocess.CalledProcessError:
        return 0  # branch exists but has no runs/ yet
    store.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.startswith("runs/") and member.name.endswith(".json"):
                (store / Path(member.name).name).write_bytes(tar.extractfile(member).read())
                count += 1
    return count


def publish(
    run_file: Path,
    repo: Path,
    branch: str = DEFAULT_BRANCH,
    *,
    token: str | None = None,
    with_reports: bool = False,
    main_ref: str = MAIN_REF,
    threshold: float = 80.0,
    attempts: int = 5,
) -> None:
    """Append ``run_file`` to ``runs/`` on the history branch and push.

    Each attempt starts from the branch's latest remote state, so concurrent CI runs never
    conflict: the loser of a push race fetches again and retries. With ``with_reports`` the
    branch's ``reports/`` is regenerated from the full history.
    """
    env = auth_env(token)
    message = (
        f"Record run {os.environ.get('GITHUB_RUN_ID', 'local')}.{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
        f" for {os.environ.get('GITHUB_SHA', 'HEAD')[:12]}"
    )
    for attempt in range(1, attempts + 1):
        tmp = Path(tempfile.mkdtemp(prefix="flaky-history-"))
        work = tmp / "history"
        orphan = f"flaky-data-new-{os.getpid()}-{attempt}"
        try:
            if _fetch(repo, branch, env):
                run_git(["worktree", "add", "--quiet", "--detach", str(work), "FETCH_HEAD"], repo)
            else:
                # First run ever: start the history branch with no parent and no files.
                run_git(["worktree", "add", "--quiet", "--detach", str(work), "HEAD"], repo)
                run_git(["checkout", "--quiet", "--orphan", orphan], work)
                run_git(["rm", "-r", "-f", "--quiet", "--ignore-unmatch", "."], work)
            (work / "runs").mkdir(exist_ok=True)
            shutil.copy2(run_file, work / "runs" / Path(run_file).name)
            if with_reports:
                shutil.rmtree(work / "reports", ignore_errors=True)
                runs = load_runs(work / "runs")
                write_reports(runs, analyze(runs, main_ref=main_ref), work / "reports",
                              current_index=len(runs) - 1, threshold=threshold, main_ref=main_ref)
            run_git(["add", "-A"], work)
            run_git(["-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
                     "commit", "--quiet", "-m", message], work)
            try:
                run_git(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], work, env)
                return
            except subprocess.CalledProcessError as exc:
                print(f"warning: push to {branch} failed (attempt {attempt} of {attempts}), "
                      f"probably a concurrent run: {exc.stderr.strip()}", file=sys.stderr)
        finally:
            _remove_worktree(repo, work, orphan)
            shutil.rmtree(tmp, ignore_errors=True)
        if attempt < attempts:
            time.sleep(attempt * 3)
    raise RuntimeError(f"could not push to {branch} after {attempts} attempts")


def _fetch(repo: Path, branch: str, env: dict[str, str] | None) -> bool:
    try:
        run_git(["fetch", "--quiet", "origin", f"refs/heads/{branch}"], repo, env)
        return True
    except subprocess.CalledProcessError:
        return False


def _remove_worktree(repo: Path, work: Path, orphan: str) -> None:
    for args in (["worktree", "remove", "--force", str(work)], ["worktree", "prune"], ["branch", "-D", orphan]):
        try:
            run_git(args, repo)
        except subprocess.CalledProcessError:
            pass
