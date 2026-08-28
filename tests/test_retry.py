import json
import shutil
from pathlib import Path

from healer import corpus, llm, localstack, orchestrator
from healer import thread as thread_module
from fake_llm import fixing_fake_complete

REAL_CORPUS = Path(__file__).resolve().parent.parent / "corpus"


def _copy_bug_001_corpus(tmp_path: Path) -> Path:
    dest_root = tmp_path / "corpus"
    shutil.copytree(REAL_CORPUS / "bug-001", dest_root / "bug-001")
    return dest_root


def _watcher_json(user: str) -> str:
    return json.dumps(
        {
            "resource_address": "aws_instance.web",
            "error_class": "InvalidParameterValue",
            "raw_excerpt": user.strip()[:500],
            "aws_service": "ec2",
        }
    )


def _analyzer_json(user: str) -> str:
    first_file = user.split("--- ", 1)[1].split(" ---")[0]
    return json.dumps({"paths": [first_file], "rationale": "fake: only file"})


def test_retry_stops_at_max_attempts_when_review_always_fails(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(
        localstack,
        "diff_state",
        lambda bug_id, patch: {"aws_instance.web.instance_type": {"candidate": "x", "verified": "y"}},
    )

    coder_calls = {"n": 0}

    def fake_complete(system, user, max_tokens=2048):
        if "Watcher agent" in system:
            return _watcher_json(user)
        if "Analyzer agent" in system:
            return _analyzer_json(user)
        if "Coder agent" in system:
            coder_calls["n"] += 1
            path = user.split("--- ", 1)[1].split(" ---")[0]
            content = user.split(f"--- {path} ---\n", 1)[1]
            # Always makes *a* change so confidence never withholds, but
            # never the change the (mocked) reviewer will accept.
            new_content = content + f"\n# attempt-{coder_calls['n']}\n"
            return json.dumps({"files": [{"path": path, "content": new_content}]})
        if "Confidence-check agent" in system:
            return json.dumps({"score": 0.9, "reason": "fake: patch matches the diagnosed error"})
        raise AssertionError(f"unexpected system prompt: {system[:60]!r}")

    monkeypatch.setattr(llm, "complete", fake_complete)

    thread = orchestrator.run_bug(run_id="test-run", bug_id="bug-001", max_attempts=3)

    assert len(thread.attempts) == 3
    assert all(a.review is not None and a.review.passed is False for a in thread.attempts)
    assert orchestrator.bug_status(thread) == "not_fixed"


def test_confidence_withhold_still_consumes_an_attempt_and_retries(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(localstack, "diff_state", lambda bug_id, patch: {})

    coder_calls = {"n": 0}

    def fake_complete(system, user, max_tokens=2048):
        if "Watcher agent" in system:
            return _watcher_json(user)
        if "Analyzer agent" in system:
            return _analyzer_json(user)
        if "Coder agent" in system:
            coder_calls["n"] += 1
            if coder_calls["n"] == 1:
                return json.dumps({"files": []})  # no-op -> WITHHOLD
            path = user.split("--- ", 1)[1].split(" ---")[0]
            content = user.split(f"--- {path} ---\n", 1)[1]
            fixed = content.replace("t2.micrio", "t2.micro")
            return json.dumps({"files": [{"path": path, "content": fixed}]})
        if "Confidence-check agent" in system:
            return json.dumps({"score": 0.9, "reason": "fake: patch matches the diagnosed error"})
        raise AssertionError(f"unexpected system prompt: {system[:60]!r}")

    monkeypatch.setattr(llm, "complete", fake_complete)

    thread = orchestrator.run_bug(run_id="test-run", bug_id="bug-001", max_attempts=3)

    assert len(thread.attempts) == 2
    assert thread.attempts[0].review is None, "withheld attempt should record no review"
    assert thread.attempts[1].review is not None and thread.attempts[1].review.passed is True
    assert orchestrator.bug_status(thread) == "fixed"


def test_only_withholding_forever_reports_withheld(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")

    def fake_complete(system, user, max_tokens=2048):
        if "Watcher agent" in system:
            return _watcher_json(user)
        if "Analyzer agent" in system:
            return _analyzer_json(user)
        if "Coder agent" in system:
            return json.dumps({"files": []})  # always a no-op
        raise AssertionError(f"unexpected system prompt: {system[:60]!r}")

    monkeypatch.setattr(llm, "complete", fake_complete)

    thread = orchestrator.run_bug(run_id="test-run", bug_id="bug-001", max_attempts=3)

    assert len(thread.attempts) == 3
    assert all(a.review is None for a in thread.attempts)
    assert orchestrator.bug_status(thread) == "withheld"


def test_failed_review_feedback_is_threaded_into_next_analyzer_call(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")

    diff_state_calls = {"n": 0}

    def fake_diff_state(bug_id, patch):
        diff_state_calls["n"] += 1
        if diff_state_calls["n"] == 1:
            return {"aws_instance.web.instance_type": {"candidate": "wrong", "verified": "t2.micro"}}
        return {}

    monkeypatch.setattr(localstack, "diff_state", fake_diff_state)

    analyzer_prompts = []

    def fake_complete(system, user, max_tokens=2048):
        if "Watcher agent" in system:
            return _watcher_json(user)
        if "Analyzer agent" in system:
            analyzer_prompts.append(user)
            return _analyzer_json(user)
        if "Coder agent" in system:
            path = user.split("--- ", 1)[1].split(" ---")[0]
            content = user.split(f"--- {path} ---\n", 1)[1]
            fixed = content.replace("t2.micrio", "t2.micro")
            return json.dumps({"files": [{"path": path, "content": fixed}]})
        if "Confidence-check agent" in system:
            return json.dumps({"score": 0.9, "reason": "fake: patch matches the diagnosed error"})
        raise AssertionError(f"unexpected system prompt: {system[:60]!r}")

    monkeypatch.setattr(llm, "complete", fake_complete)

    orchestrator.run_bug(run_id="test-run", bug_id="bug-001", max_attempts=3)

    assert len(analyzer_prompts) >= 2
    assert "This is a retry" not in analyzer_prompts[0]
    assert "This is a retry" in analyzer_prompts[1]
    assert "aws_instance.web" in analyzer_prompts[1]
