import json
import subprocess

from healer import llm
from healer import thread as thread_module
from healer.live import git_ops, live_orchestrator
from healer.live.live_watcher import LiveCase

_ENV = ["-c", "user.email=fixture@localhost", "-c", "user.name=Fixture"]


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _make_bare_repo_with_branch(tmp_path, branch, filename, content):
    bare = tmp_path / "remote.git"
    _run(["git", "init", "--bare", "-b", branch, str(bare)], cwd=tmp_path)
    seed = tmp_path / "seed"
    _run(["git", "clone", str(bare), str(seed)], cwd=tmp_path)
    (seed / filename).write_text(content)
    _run(["git", *_ENV, "add", filename], cwd=seed)
    _run(["git", *_ENV, "commit", "-m", "seed"], cwd=seed)
    _run(["git", "push", "origin", branch], cwd=seed)
    return bare


def _bare_branch_tip(bare, branch):
    return _run(["git", "rev-parse", branch], cwd=bare).stdout.strip()


def _fake_llm_that_fixes_typo(system, user, max_tokens=2048):
    if "Watcher agent" in system:
        return json.dumps(
            {"resource_address": "aws_instance.web", "error_class": "InvalidParameterValue", "raw_excerpt": user[:200], "aws_service": "ec2"}
        )
    if "Analyzer agent" in system:
        first_file = user.split("--- ", 1)[1].split(" ---")[0]
        return json.dumps({"paths": [first_file], "rationale": "fake: only file"})
    if "Coder agent" in system:
        path = user.split("--- ", 1)[1].split(" ---")[0]
        content = user.split(f"--- {path} ---\n", 1)[1]
        fixed = content.replace("t2.micrio", "t2.micro")
        return json.dumps({"files": [{"path": path, "content": fixed}]})
    raise AssertionError(f"unexpected system prompt: {system[:60]!r}")


def test_run_live_dry_never_pushes_to_the_real_remote(tmp_path, monkeypatch):
    bare = _make_bare_repo_with_branch(
        tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micrio"\n'
    )
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(llm, "complete", _fake_llm_that_fixes_typo)

    tip_before = _bare_branch_tip(bare, "fix-branch")

    case = LiveCase(
        pr_number=42,
        owner="me",
        repo="repo",
        branch="fix-branch",
        head_sha="deadbeef",
        error_output="Error: InvalidParameterValue: t2.micrio is not a valid instance type",
        is_healer_authored_head=False,
    )

    thread, push_result = live_orchestrator.run_live_dry(case, run_id="live-test", workdir_root=tmp_path / "live")

    assert len(thread.attempts) == 1
    assert push_result is not None
    assert push_result.pushed is False
    assert push_result.commit_sha is not None
    assert "would push" in push_result.would_push_message

    workdir = tmp_path / "live" / "42" / "workdir"
    assert (workdir / "main.tf").read_text() == 'instance_type = "t2.micro"\n'

    assert _bare_branch_tip(bare, "fix-branch") == tip_before, "dry run must never advance the real remote branch"


def test_run_live_dry_records_withhold_without_committing(tmp_path, monkeypatch):
    bare = _make_bare_repo_with_branch(
        tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micro"\n'  # already correct
    )
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(llm, "complete", _fake_llm_that_fixes_typo)

    case = LiveCase(
        pr_number=43,
        owner="me",
        repo="repo",
        branch="fix-branch",
        head_sha="deadbeef",
        error_output="Error: something else entirely",
        is_healer_authored_head=False,
    )

    thread, push_result = live_orchestrator.run_live_dry(case, run_id="live-test", workdir_root=tmp_path / "live")

    assert thread.attempts[0].confidence.decision.value == "withhold"
    assert push_result is None
