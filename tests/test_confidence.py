import json

from healer import llm
from healer.agents import confidence
from healer.models import (
    AttemptRecord,
    CommitDecision,
    ConfidenceVerdict,
    FileList,
    Patch,
    ReviewFeedback,
    StructuredError,
)
from healer.thread import Thread

_ERROR = StructuredError(
    resource_address="aws_instance.web", error_class="InvalidParameterValue", raw_excerpt="bad type", aws_service="ec2"
)
_FILE_LIST = FileList(paths=["main.tf"], rationale="only file")


def _refuse_llm(system, user, max_tokens=2048):
    raise AssertionError("llm.complete should not have been called")


def _attempt(number, unified_diff, passed):
    patch = Patch(unified_diff=unified_diff, touched_paths=["main.tf"])
    return AttemptRecord(
        attempt_number=number,
        structured_error=_ERROR,
        file_list=_FILE_LIST,
        patch=patch,
        confidence=ConfidenceVerdict(score=0.9, decision=CommitDecision.COMMIT, reason="x"),
        review=ReviewFeedback(passed=passed, resource=None, symptom=None, attempt_delta=None) if passed is not None else None,
    )


def test_empty_patch_withholds_without_llm_call(monkeypatch):
    monkeypatch.setattr(llm, "complete", _refuse_llm)
    patch = Patch(unified_diff="", touched_paths=[])
    thread = Thread(bug_id="b", run_id="r")

    verdict = confidence.assess(patch, _FILE_LIST, _ERROR, thread)

    assert verdict.decision == CommitDecision.WITHHOLD
    assert verdict.score == 0.0


def test_repeat_of_failed_attempt_withholds_without_llm_call(monkeypatch):
    monkeypatch.setattr(llm, "complete", _refuse_llm)
    diff = "--- a/main.tf\n+++ b/main.tf\n@@\n-old\n+new\n"
    thread = Thread(bug_id="b", run_id="r", attempts=[_attempt(1, diff, passed=False)])
    patch = Patch(unified_diff=diff, touched_paths=["main.tf"])

    verdict = confidence.assess(patch, _FILE_LIST, _ERROR, thread)

    assert verdict.decision == CommitDecision.WITHHOLD
    assert "prior attempt" in verdict.reason


def test_repeat_of_successful_attempt_does_not_short_circuit(monkeypatch):
    diff = "--- a/main.tf\n+++ b/main.tf\n@@\n-old\n+new\n"
    thread = Thread(bug_id="b", run_id="r", attempts=[_attempt(1, diff, passed=True)])
    patch = Patch(unified_diff=diff, touched_paths=["main.tf"])

    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=2048: json.dumps({"score": 0.9, "reason": "ok"}))

    verdict = confidence.assess(patch, _FILE_LIST, _ERROR, thread)

    assert verdict.decision == CommitDecision.COMMIT


def test_high_score_commits(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=2048: json.dumps({"score": 0.85, "reason": "solid fix"}))
    patch = Patch(unified_diff="diff", touched_paths=["main.tf"])
    thread = Thread(bug_id="b", run_id="r")

    verdict = confidence.assess(patch, _FILE_LIST, _ERROR, thread)

    assert verdict.decision == CommitDecision.COMMIT
    assert verdict.score == 0.85


def test_low_score_withholds(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=2048: json.dumps({"score": 0.3, "reason": "unrelated change"}))
    patch = Patch(unified_diff="diff", touched_paths=["main.tf"])
    thread = Thread(bug_id="b", run_id="r")

    verdict = confidence.assess(patch, _FILE_LIST, _ERROR, thread)

    assert verdict.decision == CommitDecision.WITHHOLD
    assert verdict.score == 0.3


def test_score_exactly_at_threshold_commits(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=2048: json.dumps({"score": 0.6, "reason": "borderline"}))
    patch = Patch(unified_diff="diff", touched_paths=["main.tf"])
    thread = Thread(bug_id="b", run_id="r")

    verdict = confidence.assess(patch, _FILE_LIST, _ERROR, thread)

    assert verdict.decision == CommitDecision.COMMIT


def test_malformed_llm_response_withholds_defensively(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=2048: "not json at all")
    patch = Patch(unified_diff="diff", touched_paths=["main.tf"])
    thread = Thread(bug_id="b", run_id="r")

    verdict = confidence.assess(patch, _FILE_LIST, _ERROR, thread)

    assert verdict.decision == CommitDecision.WITHHOLD
    assert "could not parse" in verdict.reason
