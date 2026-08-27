"""LiveWatcher: polls GitHub Actions for failing open-PR runs and builds a
LiveCase per bug — the live-mode equivalent of corpus.load_case.

Read-only. No repo checkout, no git writes, no LLM calls — see
02-architecture.md's Live-trigger integration section and slice 8 in
04-slices.md. Safe to run against a real repo at any time.
"""
import sys
from dataclasses import dataclass

from healer.live import _github

HEALER_ATTEMPT_TRAILER = "Healer-Attempt:"

MAX_LOG_EXCERPT_CHARS = 4000


@dataclass(frozen=True)
class LiveCase:
    pr_number: int
    owner: str
    repo: str
    branch: str
    head_sha: str
    error_output: str
    is_healer_authored_head: bool


def is_healer_authored_commit(commit_message: str) -> bool:
    return HEALER_ATTEMPT_TRAILER in commit_message


def _find_latest_failing_run(owner: str, repo: str, head_sha: str) -> dict | None:
    data = _github.get_json(f"/repos/{owner}/{repo}/actions/runs?head_sha={head_sha}&per_page=5")
    for run in data.get("workflow_runs", []):
        if run.get("status") == "completed" and run.get("conclusion") == "failure":
            return run
    return None


def _fetch_failure_log_excerpt(owner: str, repo: str, run_id: int, max_chars: int = MAX_LOG_EXCERPT_CHARS) -> str:
    jobs = _github.get_json(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs").get("jobs", [])
    failing_jobs = [j for j in jobs if j.get("conclusion") == "failure"]
    if not failing_jobs:
        return ""
    job_id = failing_jobs[0]["id"]
    # This endpoint 302s to a signed blob-storage URL; the initial request
    # needs the standard github+json Accept header (an explicit text/plain
    # here gets a 415 from the API itself, before the redirect even
    # happens) — the redirected response body is plain text regardless of
    # what we asked for, and we just read it as bytes/text either way.
    log_text = _github.get_text(f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
    return _extract_error_excerpt(log_text, max_chars)


def _extract_error_excerpt(log_text: str, max_chars: int) -> str:
    """Confirmed live: a job's log keeps going well past its actual
    failure (container teardown, git cleanup, deprecation warnings) — the
    last N chars of the raw log is mostly that noise, not the error. GitHub
    Actions marks real failures with a literal "##[error]" line; window
    backward from the last one instead of just taking the tail."""
    last_marker = log_text.rfind("##[error]")
    if last_marker == -1:
        return log_text[-max_chars:]
    line_end = log_text.find("\n", last_marker)
    end = line_end + 1 if line_end != -1 else len(log_text)
    start = max(0, end - max_chars)
    return log_text[start:end]


def _fetch_commit_message(owner: str, repo: str, sha: str) -> str:
    data = _github.get_json(f"/repos/{owner}/{repo}/commits/{sha}")
    return data.get("commit", {}).get("message", "")


def build_live_case(owner: str, repo: str, pr: dict) -> LiveCase | None:
    """Returns a LiveCase for this PR if its head commit's latest run
    failed, else None (PR is healthy or has no completed runs yet)."""
    head = pr["head"]
    head_sha = head["sha"]
    branch = head["ref"]

    failing_run = _find_latest_failing_run(owner, repo, head_sha)
    if failing_run is None:
        return None

    error_output = _fetch_failure_log_excerpt(owner, repo, failing_run["id"])
    commit_message = _fetch_commit_message(owner, repo, head_sha)

    return LiveCase(
        pr_number=pr["number"],
        owner=owner,
        repo=repo,
        branch=branch,
        head_sha=head_sha,
        error_output=error_output,
        is_healer_authored_head=is_healer_authored_commit(commit_message),
    )


def poll_failing_prs(owner: str, repo: str) -> list[LiveCase]:
    prs = _github.get_json(f"/repos/{owner}/{repo}/pulls?state=open&per_page=100")
    cases = []
    for pr in prs:
        try:
            case = build_live_case(owner, repo, pr)
        except Exception as exc:
            print(f"pr=#{pr.get('number')} skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if case is not None:
            cases.append(case)
    return cases


def main(argv: list[str]) -> int:
    """One-shot manual poll for slice-8 verification — not the continuous
    `healer watch` loop (that's slice 11). Read-only: prints what it found,
    touches nothing."""
    import argparse

    parser = argparse.ArgumentParser(prog="healer-live-watch", description="Poll a repo's open PRs for failing CI runs (read-only)")
    parser.add_argument("owner")
    parser.add_argument("repo")
    args = parser.parse_args(argv)

    cases = poll_failing_prs(args.owner, args.repo)
    if not cases:
        print("no failing open PRs found")
        return 0

    for case in cases:
        origin = "healer's own prior attempt" if case.is_healer_authored_head else "human commit (fresh problem)"
        print(f"PR #{case.pr_number} ({case.branch} @ {case.head_sha[:8]}) — {origin}")
        print(f"  error excerpt ({len(case.error_output)} chars captured):")
        for line in case.error_output.splitlines()[-10:]:
            print(f"    {line}")
    return 0


def main_entry() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    main_entry()
