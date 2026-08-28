from healer.live import _github, pr_comment
from healer.live.ci_wait import CiOutcome, CiResult
from healer.models import CommitDecision, ConfidenceVerdict, Patch


def test_format_comment_includes_confidence_and_diff():
    verdict = ConfidenceVerdict(score=0.97, decision=CommitDecision.COMMIT, reason="matches the diagnosed error")
    patch = Patch(unified_diff="--- a/main.tf\n+++ b/main.tf\n@@\n-old\n+new\n", touched_paths=["main.tf"])
    ci_result = CiResult(outcome=CiOutcome.SUCCESS, run_id=1)

    body = pr_comment.format_comment(1, verdict, patch, ci_result)

    assert "0.97" in body
    assert "COMMIT" in body
    assert "matches the diagnosed error" in body
    assert "main.tf" in body
    assert "-old" in body and "+new" in body
    assert "success" in body


def test_format_comment_withhold_has_no_ci_line_and_no_diff():
    verdict = ConfidenceVerdict(score=0.2, decision=CommitDecision.WITHHOLD, reason="doesn't address the error")
    patch = Patch(unified_diff="", touched_paths=[])

    body = pr_comment.format_comment(1, verdict, patch, None)

    assert "WITHHOLD" in body
    assert "No changes made" in body
    assert "CI result" not in body


def test_post_comment_calls_the_issues_comments_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(_github, "post_json", lambda path, payload: calls.append((path, payload)))

    pr_comment.post_comment("me", "repo", 7, "hello")

    assert calls == [("/repos/me/repo/issues/7/comments", {"body": "hello"})]
