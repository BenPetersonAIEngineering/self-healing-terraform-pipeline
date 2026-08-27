from healer import localstack
from healer.agents import reviewer
from healer.models import Patch


def test_failing_review_scrubs_raw_state_values(monkeypatch):
    def fake_diff_state(bug_id, patch):
        return {
            "aws_instance.web.instance_type": {"candidate": "t2.micrio", "verified": "t2.micro"},
        }

    monkeypatch.setattr(localstack, "diff_state", fake_diff_state)
    feedback = reviewer.review("bug-001", Patch(unified_diff="", touched_paths=[]))

    assert feedback.passed is False
    assert feedback.resource == "aws_instance.web"
    text = " ".join(filter(None, [feedback.resource, feedback.symptom, feedback.attempt_delta]))
    assert "t2.micrio" not in text
    assert "t2.micro" not in text


def test_passing_review_when_no_mismatches(monkeypatch):
    monkeypatch.setattr(localstack, "diff_state", lambda bug_id, patch: {})
    feedback = reviewer.review("bug-001", Patch(unified_diff="+irrelevant", touched_paths=["main.tf"]))

    assert feedback.passed is True
    assert feedback.resource is None
    assert feedback.symptom is None
    assert feedback.attempt_delta is None


def test_multiple_mismatches_counted_without_leaking_values(monkeypatch):
    def fake_diff_state(bug_id, patch):
        return {
            "aws_instance.web.instance_type": {"candidate": "t2.micrio", "verified": "t2.micro"},
            "aws_instance.web.ami": {"candidate": "ami-wrong", "verified": "ami-0c55b159cbfafe1f0"},
        }

    monkeypatch.setattr(localstack, "diff_state", fake_diff_state)
    feedback = reviewer.review("bug-001", Patch(unified_diff="", touched_paths=[]))

    assert "2 attribute" in feedback.attempt_delta
    assert "ami-wrong" not in feedback.attempt_delta
    assert "ami-0c55b159cbfafe1f0" not in feedback.attempt_delta
