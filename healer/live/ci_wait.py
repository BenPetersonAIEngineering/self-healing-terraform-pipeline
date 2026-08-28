"""CI-wait: live mode's replacement for Reviewer/LocalStack. There's no
verified-fix to diff against a real PR, so the real MR's own re-triggered
CI run *is* the reviewer — push, then poll that run to conclusion.

`timeout_seconds=900` (15 min) is a starting value the user confirmed as
reasonable (see 03-program-design.md's addendum, least-confident-decision
#7) — not yet measured against this repo's actual CI runtime.

Sitting out the full 15 minutes is the wrong answer when GitHub dispatched
no run *at all*: that's never transient, so `no_run_grace_seconds` fails
fast and `_no_run_reason` says why. The cause we hit for real (2026-08-27,
PR #1) was a PR whose diff had become empty — see the NO_RUN docstring.
"""
import sys
import time
from dataclasses import dataclass
from enum import Enum

from healer.live import _github


def _log(msg: str) -> None:
    print(f"[ci_wait] {msg}", flush=True, file=sys.stderr)

DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_POLL_INTERVAL_SECONDS = 15
DEFAULT_NO_RUN_GRACE_SECONDS = 90


class CiOutcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    NO_RUN = "no_run"
    """GitHub never dispatched a run for this SHA — distinct from TIMEOUT
    (a run that started and didn't finish in time). Not transient, so
    re-pushing the same tree won't help. The real-world case: a workflow
    filtered on `paths:` skips a pull_request whose *cumulative* diff
    against base is empty, which is exactly what happens when the healer
    correctly restores a file to match base.
    """


@dataclass(frozen=True)
class CiResult:
    outcome: CiOutcome
    run_id: int | None  # populated whenever a run was actually found, even on FAILURE — lets the caller fetch its log
    detail: str | None = None  # populated on NO_RUN with the best-effort diagnosis


def _find_run_for_sha(owner: str, repo: str, commit_sha: str) -> dict | None:
    data = _github.get_json(f"/repos/{owner}/{repo}/actions/runs?head_sha={commit_sha}&per_page=5")
    runs = data.get("workflow_runs", [])
    return runs[0] if runs else None


def _no_run_reason(owner: str, repo: str, commit_sha: str) -> str:
    """Best-effort explanation for why no run exists. Diagnosis only —
    never raises, since a failure to explain must not mask the NO_RUN.
    """
    try:
        prs = _github.get_json(f"/repos/{owner}/{repo}/commits/{commit_sha}/pulls")
        if not prs:
            return "no pull request contains this commit, so nothing would fire pull_request:synchronize"
        number = prs[0]["number"]
        pr = _github.get_json(f"/repos/{owner}/{repo}/pulls/{number}")
        changed = pr.get("changed_files")
        if changed == 0:
            return (
                f"PR #{number} now has 0 changed files against {pr['base']['ref']} — the fix restored the "
                "branch to match base, so a workflow filtered on `paths:` matches nothing and GitHub "
                "dispatches no run. A fix that empties the PR diff can never be validated by that PR's CI."
            )
        return f"PR #{number} has {changed} changed file(s); no run dispatched for some other reason"
    except Exception as exc:  # noqa: BLE001 - diagnosis is strictly best-effort
        return f"could not diagnose (GitHub API error: {exc})"


def wait_for_conclusion(
    owner: str,
    repo: str,
    commit_sha: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    no_run_grace_seconds: int = DEFAULT_NO_RUN_GRACE_SECONDS,
    _sleep=time.sleep,
    _now=time.monotonic,
) -> CiResult:
    """Polls for a workflow run against commit_sha and waits for it to
    complete. `_sleep`/`_now` are injectable so tests can drive this
    without real wall-clock waiting.
    """
    start = _now()
    deadline = start + timeout_seconds
    no_run_deadline = start + no_run_grace_seconds
    while True:
        run = _find_run_for_sha(owner, repo, commit_sha)
        if run is not None and run.get("status") == "completed":
            outcome = CiOutcome.SUCCESS if run.get("conclusion") == "success" else CiOutcome.FAILURE
            _log(f"run {run['id']} completed: {run.get('conclusion')}")
            return CiResult(outcome=outcome, run_id=run["id"])
        if run is None and _now() >= no_run_deadline:
            reason = _no_run_reason(owner, repo, commit_sha)
            _log(f"no run appeared within {no_run_grace_seconds}s — giving up early: {reason}")
            return CiResult(outcome=CiOutcome.NO_RUN, run_id=None, detail=reason)
        _log(f"run {run['id'] if run else '(not found yet)'} status={run.get('status') if run else None}, still waiting...")
        if _now() >= deadline:
            return CiResult(outcome=CiOutcome.TIMEOUT, run_id=run["id"] if run is not None else None)
        _sleep(poll_interval_seconds)
