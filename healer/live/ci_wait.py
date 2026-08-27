"""CI-wait: live mode's replacement for Reviewer/LocalStack. There's no
verified-fix to diff against a real PR, so the real MR's own re-triggered
CI run *is* the reviewer — push, then poll that run to conclusion.

`timeout_seconds=900` (15 min) is a starting value the user confirmed as
reasonable (see 03-program-design.md's addendum, least-confident-decision
#7) — not yet measured against this repo's actual CI runtime.
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


class CiOutcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class CiResult:
    outcome: CiOutcome
    run_id: int | None  # populated whenever a run was actually found, even on FAILURE — lets the caller fetch its log


def _find_run_for_sha(owner: str, repo: str, commit_sha: str) -> dict | None:
    data = _github.get_json(f"/repos/{owner}/{repo}/actions/runs?head_sha={commit_sha}&per_page=5")
    runs = data.get("workflow_runs", [])
    return runs[0] if runs else None


def wait_for_conclusion(
    owner: str,
    repo: str,
    commit_sha: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    _sleep=time.sleep,
    _now=time.monotonic,
) -> CiResult:
    """Polls for a workflow run against commit_sha and waits for it to
    complete. `_sleep`/`_now` are injectable so tests can drive this
    without real wall-clock waiting.
    """
    deadline = _now() + timeout_seconds
    while True:
        run = _find_run_for_sha(owner, repo, commit_sha)
        if run is not None and run.get("status") == "completed":
            outcome = CiOutcome.SUCCESS if run.get("conclusion") == "success" else CiOutcome.FAILURE
            _log(f"run {run['id']} completed: {run.get('conclusion')}")
            return CiResult(outcome=outcome, run_id=run["id"])
        _log(f"run {run['id'] if run else '(not found yet)'} status={run.get('status') if run else None}, still waiting...")
        if _now() >= deadline:
            return CiResult(outcome=CiOutcome.TIMEOUT, run_id=run["id"] if run is not None else None)
        _sleep(poll_interval_seconds)
