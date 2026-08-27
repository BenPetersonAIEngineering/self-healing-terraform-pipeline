from healer import thread as thread_module
from healer.models import (
    AttemptRecord,
    ConfidenceVerdict,
    FileList,
    Patch,
    CommitDecision,
    ReviewFeedback,
    StructuredError,
)
from healer.thread import Thread


def _sample_attempt(n: int) -> AttemptRecord:
    return AttemptRecord(
        attempt_number=n,
        structured_error=StructuredError(
            resource_address="aws_instance.web",
            error_class="InvalidParameterValue",
            raw_excerpt="Unknown instance type",
            aws_service="ec2",
        ),
        file_list=FileList(paths=["main.tf"], rationale="stub"),
        patch=Patch(unified_diff="--- a\n+++ b\n", touched_paths=["main.tf"]),
        confidence=ConfidenceVerdict(score=0.9, decision=CommitDecision.COMMIT, reason="stub"),
        review=ReviewFeedback(passed=True, resource=None, symptom=None, attempt_delta=None),
    )


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path)

    t = Thread(bug_id="bug-001", run_id="run-1", attempts=[_sample_attempt(1)])
    t.save()

    loaded = Thread.load("run-1", "bug-001")
    assert loaded.bug_id == "bug-001"
    assert len(loaded.attempts) == 1
    assert loaded.attempts[0].confidence.decision == CommitDecision.COMMIT
    assert loaded.attempts[0].review.passed is True


def test_load_missing_thread_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path)

    loaded = Thread.load("run-missing", "bug-001")
    assert loaded.attempts == []


def test_latest_feedback_skips_withheld_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path)

    withheld = _sample_attempt(1)
    withheld.review = None
    fed_back = _sample_attempt(2)
    fed_back.review = ReviewFeedback(passed=False, resource="aws_instance.web", symptom="still broken", attempt_delta="no change")

    t = Thread(bug_id="bug-001", run_id="run-1", attempts=[withheld, fed_back])
    assert t.latest_feedback() is fed_back.review
