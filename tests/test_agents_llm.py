import json

from healer import llm
from healer.agents import analyzer, coder, watcher
from healer.models import StructuredError
from healer.tools.scoped_fs import ScopedFileTool


def test_watcher_parses_llm_response(monkeypatch):
    def fake_complete(system, user, max_tokens=2048):
        assert "Watcher agent" in system
        return json.dumps(
            {
                "resource_address": "aws_instance.web",
                "error_class": "InvalidParameterValue",
                "raw_excerpt": "Unknown instance type",
                "aws_service": "ec2",
            }
        )

    monkeypatch.setattr(llm, "complete", fake_complete)
    result = watcher.structure_error("some raw terraform error output")
    assert result.resource_address == "aws_instance.web"
    assert result.error_class == "InvalidParameterValue"


def test_watcher_handles_fenced_json_response(monkeypatch):
    def fake_complete(system, user, max_tokens=2048):
        return "```json\n" + json.dumps({"resource_address": None, "error_class": "Unknown", "raw_excerpt": "x", "aws_service": None}) + "\n```"

    monkeypatch.setattr(llm, "complete", fake_complete)
    result = watcher.structure_error("garbled output")
    assert result.error_class == "Unknown"


def test_analyzer_validates_returned_paths_against_repo(tmp_path, monkeypatch):
    (tmp_path / "main.tf").write_text("resource {}")

    def fake_complete(system, user, max_tokens=2048):
        assert "Analyzer agent" in system
        return json.dumps({"paths": ["main.tf"], "rationale": "the only file"})

    monkeypatch.setattr(llm, "complete", fake_complete)
    scoped_fs = ScopedFileTool(allowed_roots=[str(tmp_path)])
    error = StructuredError(resource_address="x", error_class="Unknown", raw_excerpt="x", aws_service=None)

    result = analyzer.diagnose(scoped_fs, error, prior_feedback=None)
    assert result.paths == ["main.tf"]


def test_analyzer_falls_back_when_model_hallucinates_a_path(tmp_path, monkeypatch):
    (tmp_path / "main.tf").write_text("resource {}")

    def fake_complete(system, user, max_tokens=2048):
        return json.dumps({"paths": ["does_not_exist.tf"], "rationale": "oops"})

    monkeypatch.setattr(llm, "complete", fake_complete)
    scoped_fs = ScopedFileTool(allowed_roots=[str(tmp_path)])
    error = StructuredError(resource_address="x", error_class="Unknown", raw_excerpt="x", aws_service=None)

    result = analyzer.diagnose(scoped_fs, error, prior_feedback=None)
    assert result.paths == ["main.tf"], "must fall back to all .tf files rather than hand the Coder an empty/bad allowlist"


def test_coder_writes_only_files_the_model_returned(tmp_path, monkeypatch):
    (tmp_path / "main.tf").write_text("instance_type = \"t2.micrio\"")

    def fake_complete(system, user, max_tokens=2048):
        assert "Coder agent" in system
        return json.dumps({"files": [{"path": "main.tf", "content": "instance_type = \"t2.micro\""}]})

    monkeypatch.setattr(llm, "complete", fake_complete)
    scoped_fs = ScopedFileTool(allowed_roots=[str(tmp_path / "main.tf")])
    from healer.models import FileList

    error = StructuredError(resource_address="x", error_class="InvalidParameterValue", raw_excerpt="x", aws_service="ec2")
    patch = coder.implement_fix(scoped_fs, FileList(paths=["main.tf"], rationale="x"), error)

    assert patch.touched_paths == ["main.tf"]
    assert "t2.micro" in patch.unified_diff
    assert (tmp_path / "main.tf").read_text() == 'instance_type = "t2.micro"'
