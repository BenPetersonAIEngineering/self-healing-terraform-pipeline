import json
from pathlib import Path

from healer import cli, corpus
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
from healer.orchestrator import UnsupportedBug
from healer.thread import Thread


def _write_case(corpus_root: Path, bug_id: str, localstack_unsupported: bool = False, skip_reason: str | None = None) -> None:
    bug_dir = corpus_root / bug_id
    bug_dir.mkdir(parents=True)
    data = {"error_output": "some error", "source": "test fixture"}
    if localstack_unsupported:
        data["localstack_unsupported"] = True
        data["skip_reason"] = skip_reason or "test reason"
    import yaml

    (bug_dir / "case.yaml").write_text(yaml.safe_dump(data))


def _attempt(n: int, *, passed: bool | None, decision: CommitDecision) -> AttemptRecord:
    review = None if passed is None else ReviewFeedback(passed=passed, resource=None, symptom=None, attempt_delta=None)
    return AttemptRecord(
        attempt_number=n,
        structured_error=StructuredError(resource_address=None, error_class="X", raw_excerpt="x", aws_service=None),
        file_list=FileList(paths=["main.tf"], rationale="x"),
        patch=Patch(unified_diff="x", touched_paths=["main.tf"]),
        confidence=ConfidenceVerdict(score=0.9, decision=decision, reason="x"),
        review=review,
    )


def test_run_one_bug_skips_unsupported_case_without_llm(tmp_path, monkeypatch):
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    _write_case(corpus_root, "bug-unsup", localstack_unsupported=True, skip_reason="provider not emulated by LocalStack")
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")

    bug_id, status = cli._run_one_bug("test-run", "bug-unsup")

    assert bug_id == "bug-unsup"
    assert status == "unsupported"
    assert not (tmp_path / "runs" / "test-run" / "bug-unsup").exists(), "unsupported bugs shouldn't create a workdir/thread"


def test_recompute_summary_aggregates_across_bugs(tmp_path, monkeypatch):
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    runs_root = tmp_path / "runs"
    _write_case(corpus_root, "bug-fixed-fast")
    _write_case(corpus_root, "bug-fixed-slow")
    _write_case(corpus_root, "bug-failed-open")
    _write_case(corpus_root, "bug-withheld")
    _write_case(corpus_root, "bug-unsup", localstack_unsupported=True)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", runs_root)

    for bug_id, attempts in {
        "bug-fixed-fast": [_attempt(1, passed=True, decision=CommitDecision.COMMIT)],
        "bug-fixed-slow": [
            _attempt(1, passed=False, decision=CommitDecision.COMMIT),
            _attempt(2, passed=False, decision=CommitDecision.COMMIT),
            _attempt(3, passed=True, decision=CommitDecision.COMMIT),
        ],
        "bug-failed-open": [_attempt(1, passed=False, decision=CommitDecision.COMMIT)],
        "bug-withheld": [_attempt(1, passed=None, decision=CommitDecision.WITHHOLD)],
    }.items():
        Thread(bug_id=bug_id, run_id="test-run", attempts=attempts).save()

    summary = cli._recompute_summary("test-run")

    assert summary.bug_results == {
        "bug-fixed-fast": "fixed",
        "bug-fixed-slow": "fixed",
        "bug-failed-open": "not_fixed",
        "bug-withheld": "withheld",
        "bug-unsup": "unsupported",
    }
    # mean attempts on success: (1 + 3) / 2 = 2.0
    assert summary.mean_attempts_on_success == 2.0
    # commit pushed for 3 bugs (fast, slow, failed-open); 2 of those ended up fixed
    assert summary.confidence_precision == 2 / 3


def test_recompute_summary_precision_is_none_when_no_commits_pushed(tmp_path, monkeypatch):
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    _write_case(corpus_root, "bug-withheld")
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")

    Thread(bug_id="bug-withheld", run_id="test-run", attempts=[_attempt(1, passed=None, decision=CommitDecision.WITHHOLD)]).save()

    summary = cli._recompute_summary("test-run")
    assert summary.confidence_precision is None


class _FakePool:
    def __init__(self, processes=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starmap(self, func, iterable):
        return [func(*args) for args in iterable]


def test_cmd_run_all_processes_every_corpus_bug(tmp_path, monkeypatch, capsys):
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    _write_case(corpus_root, "bug-x", localstack_unsupported=True)
    _write_case(corpus_root, "bug-y", localstack_unsupported=True)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(cli.multiprocessing, "Pool", _FakePool)

    args = argparse_namespace(bug_id=None, all=True, jobs=2, run_id="test-run")
    rc = cli.cmd_run(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "bug=bug-x status=unsupported" in out
    assert "bug=bug-y status=unsupported" in out

    summary_data = json.loads((tmp_path / "runs" / "test-run" / "summary.json").read_text())
    assert summary_data["bug_results"] == {"bug-x": "unsupported", "bug-y": "unsupported"}


def argparse_namespace(**kwargs):
    import argparse

    return argparse.Namespace(**kwargs)


def test_one_bug_crashing_does_not_abort_the_rest_of_all(tmp_path, monkeypatch, capsys):
    """A bug that raises (missing API key, transient error, anything) must
    not take down the other bugs in a --all run, and must still show up in
    the summary as "error" rather than silently vanishing."""
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    _write_case(corpus_root, "bug-broken")
    _write_case(corpus_root, "bug-unsup", localstack_unsupported=True)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(cli.multiprocessing, "Pool", _FakePool)

    real_run_bug = cli.orchestrator.run_bug

    def fake_run_bug(run_id, bug_id):
        if bug_id == "bug-broken":
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        return real_run_bug(run_id, bug_id)  # bug-unsup takes the real UnsupportedBug path

    monkeypatch.setattr(cli.orchestrator, "run_bug", fake_run_bug)

    args = argparse_namespace(bug_id=None, all=True, jobs=2, run_id="test-run")
    rc = cli.cmd_run(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "bug=bug-broken status=error" in out
    assert "bug=bug-unsup status=unsupported" in out

    summary_data = json.loads((tmp_path / "runs" / "test-run" / "summary.json").read_text())
    assert summary_data["bug_results"] == {"bug-broken": "error", "bug-unsup": "unsupported"}
