import subprocess
from pathlib import Path

from healer.live import git_ops
from healer.models import Patch

_ENV = ["-c", "user.email=fixture@localhost", "-c", "user.name=Fixture"]


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _make_bare_repo_with_branch(tmp_path: Path, branch: str, filename: str, content: str) -> Path:
    """Builds a real local bare repo with one branch and one file — a
    stand-in "remote" so checkout_branch/commit_and_push can be tested
    against real git plumbing without ever touching GitHub."""
    bare = tmp_path / "remote.git"
    _run(["git", "init", "--bare", "-b", branch, str(bare)], cwd=tmp_path)

    seed = tmp_path / "seed"
    _run(["git", "clone", str(bare), str(seed)], cwd=tmp_path)
    (seed / filename).write_text(content)
    _run(["git", *_ENV, "add", filename], cwd=seed)
    _run(["git", *_ENV, "commit", "-m", "seed"], cwd=seed)
    _run(["git", "push", "origin", branch], cwd=seed)
    return bare


def _bare_branch_tip(bare: Path, branch: str) -> str:
    out = _run(["git", "rev-parse", branch], cwd=bare)
    return out.stdout.strip()


def test_checkout_branch_clones_the_right_branch_content(tmp_path, monkeypatch):
    bare = _make_bare_repo_with_branch(tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micrio"\n')
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))

    workdir = tmp_path / "workdir"
    git_ops.checkout_branch("me", "repo", "fix-branch", workdir)

    assert (workdir / "main.tf").read_text() == 'instance_type = "t2.micrio"\n'
    current_branch = _run(["git", "branch", "--show-current"], cwd=workdir).stdout.strip()
    assert current_branch == "fix-branch"


def test_checkout_branch_works_with_a_relative_workdir_path(tmp_path, monkeypatch):
    """Regression test: a relative `workdir` used to get resolved twice —
    once for the subprocess's cwd=, once by git against that cwd — landing
    the clone one directory too deep and leaving `workdir` itself empty.
    Only surfaced once a real caller (run_pr.py, for the automatic
    self-heal trigger) passed a relative path instead of an absolute one
    from a tmp_path fixture. Confirmed live in GitHub Actions 2026-08-28."""
    bare = _make_bare_repo_with_branch(tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micrio"\n')
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))
    monkeypatch.chdir(tmp_path)

    workdir = Path("relative") / "workdir"
    git_ops.checkout_branch("me", "repo", "fix-branch", workdir)

    assert (tmp_path / "relative" / "workdir" / "main.tf").read_text() == 'instance_type = "t2.micrio"\n'


def test_commit_and_push_dry_run_commits_locally_but_never_touches_remote(tmp_path, monkeypatch):
    bare = _make_bare_repo_with_branch(tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micrio"\n')
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))

    workdir = tmp_path / "workdir"
    git_ops.checkout_branch("me", "repo", "fix-branch", workdir)
    tip_before = _bare_branch_tip(bare, "fix-branch")

    # commit_and_push doesn't apply the diff itself — in real use the Coder
    # already wrote the fix straight into workdir (see git_ops.py's
    # docstring); simulate that here rather than relying on patch(1).
    (workdir / "main.tf").write_text('instance_type = "t2.micro"\n')
    patch = Patch(
        unified_diff='--- a/main.tf\n+++ b/main.tf\n@@ -1 +1 @@\n-instance_type = "t2.micrio"\n+instance_type = "t2.micro"\n',
        touched_paths=["main.tf"],
    )
    result = git_ops.commit_and_push(workdir, patch, attempt_number=1, allow_push=False)

    assert result.pushed is False
    assert result.commit_sha is not None
    assert "would push" in result.would_push_message
    assert (workdir / "main.tf").read_text() == 'instance_type = "t2.micro"\n'

    log_message = _run(["git", "log", "-1", "--format=%B"], cwd=workdir).stdout
    assert "Healer-Attempt: 1" in log_message

    assert _bare_branch_tip(bare, "fix-branch") == tip_before, "dry run must never advance the remote branch"


def test_commit_and_push_real_push_advances_the_remote_branch(tmp_path, monkeypatch):
    bare = _make_bare_repo_with_branch(tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micrio"\n')
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))

    workdir = tmp_path / "workdir"
    git_ops.checkout_branch("me", "repo", "fix-branch", workdir)
    tip_before = _bare_branch_tip(bare, "fix-branch")

    (workdir / "main.tf").write_text('instance_type = "t2.micro"\n')
    patch = Patch(
        unified_diff='--- a/main.tf\n+++ b/main.tf\n@@ -1 +1 @@\n-instance_type = "t2.micrio"\n+instance_type = "t2.micro"\n',
        touched_paths=["main.tf"],
    )
    result = git_ops.commit_and_push(workdir, patch, attempt_number=1, allow_push=True)

    assert result.pushed is True
    assert result.commit_sha is not None
    assert result.would_push_message is None
    assert _bare_branch_tip(bare, "fix-branch") == result.commit_sha
    assert _bare_branch_tip(bare, "fix-branch") != tip_before


def test_auth_args_includes_explicit_basic_auth_when_token_set(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "abc123")
    args = git_ops._auth_args()
    assert args[0] == "-c"
    assert args[1].startswith("http.extraHeader=Authorization: Basic ")


def test_auth_args_empty_when_no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert git_ops._auth_args() == []


def test_commit_and_push_noop_when_patch_touches_nothing(tmp_path, monkeypatch):
    bare = _make_bare_repo_with_branch(tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micro"\n')
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))

    workdir = tmp_path / "workdir"
    git_ops.checkout_branch("me", "repo", "fix-branch", workdir)

    result = git_ops.commit_and_push(workdir, Patch(unified_diff="", touched_paths=[]), attempt_number=1, allow_push=True)

    assert result == git_ops.PushResult(pushed=False, commit_sha=None, would_push_message="no files touched, nothing to commit")
