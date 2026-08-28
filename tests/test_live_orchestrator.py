import json
import subprocess

from healer import llm
from healer import thread as thread_module
from healer.live import ci_wait, git_ops, live_orchestrator, live_watcher, pr_comment
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
        if "t2.micrio" in content:
            fixed = content.replace("t2.micrio", "t2.micro")
        else:
            # Typo's already fixed but CI still failed for some other
            # reason (per a retry's fresh error text) — guarantee a real,
            # distinguishable second change so the test can tell attempt 2
            # apart from a no-op WITHHOLD.
            fixed = content + "# second-attempt-marker\n"
        return json.dumps({"files": [{"path": path, "content": fixed}]})
    if "Confidence-check agent" in system:
        return json.dumps({"score": 0.9, "reason": "fake: patch matches the diagnosed error"})
    raise AssertionError(f"unexpected system prompt: {system[:60]!r}")


def _setup(tmp_path, monkeypatch, initial_content, posted_comments=None):
    bare = _make_bare_repo_with_branch(tmp_path, "fix-branch", "main.tf", initial_content)
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(llm, "complete", _fake_llm_that_fixes_typo)
    if posted_comments is None:
        posted_comments = []
    monkeypatch.setattr(
        pr_comment, "post_comment", lambda owner, repo, pr_number, body: posted_comments.append((pr_number, body))
    )
    return bare


def _case(pr_number=42, error="Error: InvalidParameterValue: t2.micrio is not a valid instance type"):
    return LiveCase(
        pr_number=pr_number,
        owner="me",
        repo="repo",
        branch="fix-branch",
        head_sha="deadbeef",
        error_output=error,
        is_healer_authored_head=False,
    )


def test_run_live_dry_run_never_pushes_to_the_real_remote(tmp_path, monkeypatch):
    bare = _setup(tmp_path, monkeypatch, 'instance_type = "t2.micrio"\n')
    tip_before = _bare_branch_tip(bare, "fix-branch")

    thread, push_result = live_orchestrator.run_live(_case(), run_id="live-test", workdir_root=tmp_path / "live", allow_push=False)

    assert len(thread.attempts) == 1
    assert push_result.pushed is False
    assert push_result.commit_sha is not None
    assert "would push" in push_result.would_push_message
    assert (tmp_path / "live" / "42" / "workdir" / "main.tf").read_text() == 'instance_type = "t2.micro"\n'
    assert _bare_branch_tip(bare, "fix-branch") == tip_before


def test_run_live_dry_run_records_withhold_without_committing(tmp_path, monkeypatch):
    _make_bare_repo_with_branch(tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micro"\n')
    bare = tmp_path / "remote.git"
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")

    def fake_complete_no_op(system, user, max_tokens=2048):
        if "Watcher agent" in system:
            return json.dumps({"resource_address": None, "error_class": "Unknown", "raw_excerpt": user[:200], "aws_service": None})
        if "Analyzer agent" in system:
            first_file = user.split("--- ", 1)[1].split(" ---")[0]
            return json.dumps({"paths": [first_file], "rationale": "fake"})
        if "Coder agent" in system:
            return json.dumps({"files": []})  # genuinely no-op
        raise AssertionError(f"unexpected system prompt: {system[:60]!r}")

    monkeypatch.setattr(llm, "complete", fake_complete_no_op)

    thread, push_result = live_orchestrator.run_live(
        _case(pr_number=43, error="Error: something else"), run_id="live-test", workdir_root=tmp_path / "live", allow_push=False
    )

    assert thread.attempts[0].confidence.decision.value == "withhold"
    assert push_result is None


def test_run_live_allow_push_stops_immediately_on_ci_success(tmp_path, monkeypatch):
    bare = _setup(tmp_path, monkeypatch, 'instance_type = "t2.micrio"\n')
    tip_before = _bare_branch_tip(bare, "fix-branch")

    monkeypatch.setattr(
        ci_wait, "wait_for_conclusion", lambda owner, repo, sha, **kw: ci_wait.CiResult(outcome=ci_wait.CiOutcome.SUCCESS, run_id=1)
    )

    thread, push_result = live_orchestrator.run_live(_case(pr_number=44), run_id="live-test", workdir_root=tmp_path / "live", allow_push=True)

    assert len(thread.attempts) == 1
    assert thread.attempts[0].review.passed is True
    assert push_result.pushed is True
    assert _bare_branch_tip(bare, "fix-branch") == push_result.commit_sha
    assert _bare_branch_tip(bare, "fix-branch") != tip_before


def test_run_live_allow_push_retries_on_ci_failure_then_succeeds(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 'instance_type = "t2.micrio"\n')

    wait_calls = {"n": 0}

    def fake_wait(owner, repo, sha, **kw):
        wait_calls["n"] += 1
        if wait_calls["n"] == 1:
            return ci_wait.CiResult(outcome=ci_wait.CiOutcome.FAILURE, run_id=101)
        return ci_wait.CiResult(outcome=ci_wait.CiOutcome.SUCCESS, run_id=102)

    monkeypatch.setattr(ci_wait, "wait_for_conclusion", fake_wait)
    monkeypatch.setattr(live_watcher, "_fetch_failure_log_excerpt", lambda owner, repo, run_id: "Error: still broken somehow")

    thread, push_result = live_orchestrator.run_live(_case(pr_number=45), run_id="live-test", workdir_root=tmp_path / "live", allow_push=True)

    # attempt 1: coder already fixes the typo correctly on the first pass (fake LLM is deterministic),
    # so what matters here is that a FAILURE result triggers a second attempt at all.
    assert len(thread.attempts) == 2
    assert thread.attempts[0].review.passed is False
    assert thread.attempts[0].review.symptom == "CI failed again after this fix"
    assert thread.attempts[1].review.passed is True
    assert push_result.pushed is True
    assert wait_calls["n"] == 2


def test_run_live_allow_push_stops_on_timeout_without_retrying(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 'instance_type = "t2.micrio"\n')

    monkeypatch.setattr(
        ci_wait, "wait_for_conclusion", lambda owner, repo, sha, **kw: ci_wait.CiResult(outcome=ci_wait.CiOutcome.TIMEOUT, run_id=None)
    )

    thread, push_result = live_orchestrator.run_live(_case(pr_number=46), run_id="live-test", workdir_root=tmp_path / "live", allow_push=True)

    assert len(thread.attempts) == 1
    assert thread.attempts[0].review.passed is False
    assert thread.attempts[0].review.attempt_delta == "timeout"


def test_run_live_allow_push_stops_on_no_run_without_retrying(tmp_path, monkeypatch):
    """NO_RUN is never transient (GitHub dispatched nothing), so re-pushing the
    same tree would just burn attempts — stop after one and carry the reason."""
    _setup(tmp_path, monkeypatch, 'instance_type = "t2.micrio"\n')

    monkeypatch.setattr(
        ci_wait,
        "wait_for_conclusion",
        lambda owner, repo, sha, **kw: ci_wait.CiResult(
            outcome=ci_wait.CiOutcome.NO_RUN, run_id=None, detail="PR #1 now has 0 changed files against main"
        ),
    )

    thread, push_result = live_orchestrator.run_live(_case(pr_number=46), run_id="live-test", workdir_root=tmp_path / "live", allow_push=True)

    assert len(thread.attempts) == 1
    assert thread.attempts[0].review.passed is False
    assert thread.attempts[0].review.attempt_delta == "no-run"
    assert "0 changed files" in thread.attempts[0].review.symptom


def test_run_live_allow_push_stops_at_max_attempts_when_always_failing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 'instance_type = "t2.micrio"\n')

    monkeypatch.setattr(
        ci_wait, "wait_for_conclusion", lambda owner, repo, sha, **kw: ci_wait.CiResult(outcome=ci_wait.CiOutcome.FAILURE, run_id=999)
    )
    monkeypatch.setattr(live_watcher, "_fetch_failure_log_excerpt", lambda owner, repo, run_id: "Error: still broken")

    thread, push_result = live_orchestrator.run_live(
        _case(pr_number=47), run_id="live-test", workdir_root=tmp_path / "live", max_attempts=3, allow_push=True
    )

    assert len(thread.attempts) == 3
    assert all(a.review.passed is False for a in thread.attempts)


def test_allow_push_success_posts_one_pr_comment_with_confidence_and_diff(tmp_path, monkeypatch):
    posted = []
    _setup(tmp_path, monkeypatch, 'instance_type = "t2.micrio"\n', posted_comments=posted)
    monkeypatch.setattr(
        ci_wait, "wait_for_conclusion", lambda owner, repo, sha, **kw: ci_wait.CiResult(outcome=ci_wait.CiOutcome.SUCCESS, run_id=1)
    )

    live_orchestrator.run_live(_case(pr_number=48), run_id="live-test", workdir_root=tmp_path / "live", allow_push=True)

    assert len(posted) == 1
    pr_number, body = posted[0]
    assert pr_number == 48
    assert "Self-Healer" in body
    assert "t2.micro" in body  # the diff is in the comment
    assert "success" in body


def test_dry_run_never_posts_a_pr_comment(tmp_path, monkeypatch):
    posted = []
    _setup(tmp_path, monkeypatch, 'instance_type = "t2.micrio"\n', posted_comments=posted)

    live_orchestrator.run_live(_case(pr_number=49), run_id="live-test", workdir_root=tmp_path / "live", allow_push=False)

    assert posted == []


def test_allow_push_withhold_posts_a_comment_without_a_ci_result(tmp_path, monkeypatch):
    posted = []
    _make_bare_repo_with_branch(tmp_path, "fix-branch", "main.tf", 'instance_type = "t2.micro"\n')
    bare = tmp_path / "remote.git"
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, repo: str(bare))
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        pr_comment, "post_comment", lambda owner, repo, pr_number, body: posted.append((pr_number, body))
    )

    def fake_complete_no_op(system, user, max_tokens=2048):
        if "Watcher agent" in system:
            return json.dumps({"resource_address": None, "error_class": "Unknown", "raw_excerpt": user[:200], "aws_service": None})
        if "Analyzer agent" in system:
            first_file = user.split("--- ", 1)[1].split(" ---")[0]
            return json.dumps({"paths": [first_file], "rationale": "fake"})
        if "Coder agent" in system:
            return json.dumps({"files": []})  # genuinely no-op
        raise AssertionError(f"unexpected system prompt: {system[:60]!r}")

    monkeypatch.setattr(llm, "complete", fake_complete_no_op)

    live_orchestrator.run_live(
        _case(pr_number=50, error="Error: something else"), run_id="live-test", workdir_root=tmp_path / "live", allow_push=True
    )

    assert len(posted) == 1
    pr_number, body = posted[0]
    assert pr_number == 50
    assert "WITHHOLD" in body
    assert "CI result" not in body
