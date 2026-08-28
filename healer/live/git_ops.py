"""Real git operations for live mode: checkout a PR branch into a scratch
workdir, and (only when explicitly enabled) commit + push whatever's on
disk in that workdir back to that branch.

commit_and_push does NOT apply patch.unified_diff itself — unlike eval
mode's Reviewer (which reconstructs a fix on a fresh scratch clone via
healer.patching.apply_unified_diff), live mode's Coder writes its fix
directly into this same real workdir via ScopedFileTool, same as it does
in eval mode's orchestrator.py. By the time commit_and_push runs, the
fix is already on disk; re-applying the diff on top of an already-fixed
tree just fails. `patch.touched_paths` is still needed here to know what
to `git add`.

Slice 9: checkout and local commit-building work for real; `allow_push`
defaults to False, so nothing here ever reaches a real remote unless a
caller explicitly opts in. Slice 10 is the one that actually flips it on
against a real repo, and only after asking first — see
02-architecture.md's Live-trigger integration section.
"""
import base64
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from healer.models import Patch

HEALER_ATTEMPT_TRAILER = "Healer-Attempt"
_COMMIT_AUTHOR_EMAIL = "self-healer@localhost"
_COMMIT_AUTHOR_NAME = "Terraform Self-Healer"


@dataclass(frozen=True)
class PushResult:
    pushed: bool
    commit_sha: str | None
    would_push_message: str | None  # populated whenever pushed is False, so callers/tests can assert on intent


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _repo_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def _auth_args() -> list[str]:
    """Explicit -c http.extraHeader auth for this git invocation only —
    never persisted to the workdir's .git/config (unlike embedding a
    token in the remote URL, which would leave it at rest on disk).

    Confirmed live (2026-08-27): without this, checkout_branch/
    commit_and_push silently fell back to whatever ambient git credential
    the *host machine* happened to have (macOS Keychain, in this case) —
    it worked, but only by accident, and would fail on any machine without
    one. That's the whole reason for this function. (An earlier version of
    this docstring also blamed ambient auth for pushes not triggering the
    PR's Actions run; that was wrong — the real cause was an emptied PR
    diff hitting the workflow's `paths:` filter. See
    docs/plans/terraform-self-healer/investigation-ci-trigger-flakiness.md.)
    GITHUB_TOKEN needs read access for live_watcher too — see
    02-architecture.md's External section.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=Authorization: Basic {basic}"]


def checkout_branch(owner: str, repo: str, branch: str, workdir: Path) -> None:
    # Must be absolute before it's used both as the clone destination arg
    # AND to derive `cwd=` for the subprocess call below — a relative
    # workdir gets resolved twice (once against the real process cwd for
    # `cwd=workdir.parent`, then again by git against THAT cwd for the
    # destination arg), landing the clone one level too deep and silently
    # leaving `workdir` itself never created. Confirmed live in GitHub
    # Actions (2026-08-28): every prior caller happened to pass an
    # absolute path (pytest's tmp_path, or an absolute scratch path), so
    # this was invisible until run_pr.py's CLI passed a relative one.
    workdir = workdir.resolve()
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    url = _repo_url(owner, repo)
    # --single-branch clone sets up the local branch to track the matching
    # remote branch, so a later bare `git push` (no refspec) in
    # commit_and_push lands back on this same branch.
    _run(
        ["git", *_auth_args(), "clone", "--branch", branch, "--single-branch", "--depth", "1", url, str(workdir)],
        cwd=workdir.parent,
    )


def commit_and_push(workdir: Path, patch: Patch, attempt_number: int, allow_push: bool) -> PushResult:
    if not patch.touched_paths:
        return PushResult(pushed=False, commit_sha=None, would_push_message="no files touched, nothing to commit")

    _run(["git", "add", *patch.touched_paths], cwd=workdir)
    message = f"fix: self-healer patch (attempt {attempt_number})\n\n{HEALER_ATTEMPT_TRAILER}: {attempt_number}\n"
    _run(
        [
            "git",
            "-c",
            f"user.email={_COMMIT_AUTHOR_EMAIL}",
            "-c",
            f"user.name={_COMMIT_AUTHOR_NAME}",
            "commit",
            "-m",
            message,
        ],
        cwd=workdir,
    )
    commit_sha = _run(["git", "rev-parse", "HEAD"], cwd=workdir).stdout.strip()

    if not allow_push:
        return PushResult(
            pushed=False,
            commit_sha=commit_sha,
            would_push_message=f"would push {commit_sha[:8]} to origin: {message.splitlines()[0]}",
        )

    _run(["git", *_auth_args(), "push"], cwd=workdir)
    return PushResult(pushed=True, commit_sha=commit_sha, would_push_message=None)
