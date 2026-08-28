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


def test_tracer_bullet_fixes_bug_001(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(llm, "complete", fixing_fake_complete)
    # No Docker/terraform/LocalStack in this environment — fake the state
    # diff to "no mismatch" since the fake Coder above genuinely fixes the
    # typo. Real state-diffing logic is covered separately in test_localstack.py.
    monkeypatch.setattr(localstack, "diff_state", lambda bug_id, patch: {})

    thread = orchestrator.run_bug(run_id="test-run", bug_id="bug-001")

    assert len(thread.attempts) == 1
    attempt = thread.attempts[0]
    assert attempt.file_list.paths == ["main.tf"]
    assert "t2.micro" in attempt.patch.unified_diff
    assert attempt.review is not None
    assert attempt.review.passed is True
    assert orchestrator.bug_status(thread) == "fixed"

    workdir_content = (tmp_path / "runs" / "test-run" / "bug-001" / "workdir" / "main.tf").read_text()
    assert "t2.micrio" not in workdir_content
    assert "t2.micro" in workdir_content

    corpus_content = (corpus_root / "bug-001" / "repo" / "main.tf").read_text()
    assert corpus_content == '''resource "aws_instance" "web" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t2.micrio"

  tags = {
    Name = "web"
  }
}
''', "corpus fixture must stay pristine across runs"


def test_orchestrator_denies_analyzer_access_to_eval_dir(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")

    from healer.tools.scoped_fs import PathNotAllowed, ScopedFileTool

    case = corpus.load_case("bug-001")
    analyzer_fs = ScopedFileTool(allowed_roots=[str(case.repo_path)])
    try:
        analyzer_fs.read(str(case.eval_path / "verified-fix.diff"))
        assert False, "analyzer should not be able to read eval/"
    except PathNotAllowed:
        pass


def test_analyzer_flagging_no_files_withholds_instead_of_crashing(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(thread_module, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(localstack, "diff_state", lambda bug_id, patch: {})

    from healer.agents import analyzer as analyzer_module
    from healer.models import FileList

    monkeypatch.setattr(analyzer_module, "diagnose", lambda *a, **kw: FileList(paths=[], rationale="nothing relevant found"))
    monkeypatch.setattr(llm, "complete", fixing_fake_complete)

    thread = orchestrator.run_bug(run_id="test-run", bug_id="bug-001", max_attempts=1)

    assert len(thread.attempts) == 1
    assert thread.attempts[0].patch.touched_paths == []
    assert thread.attempts[0].confidence.decision.value == "withhold"
