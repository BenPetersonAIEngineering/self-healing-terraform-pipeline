import json
import shutil
import subprocess
from pathlib import Path

from healer import corpus, localstack
from healer.models import Patch

REAL_CORPUS = Path(__file__).resolve().parent.parent / "corpus"


def _copy_bug_001_corpus(tmp_path: Path) -> Path:
    dest_root = tmp_path / "corpus"
    shutil.copytree(REAL_CORPUS / "bug-001", dest_root / "bug-001")
    return dest_root


def _fake_terraform_show(instance_type: str) -> str:
    return json.dumps(
        {
            "values": {
                "root_module": {
                    "resources": [
                        {
                            "address": "aws_instance.web",
                            "values": {"instance_type": instance_type, "ami": "ami-0c55b159cbfafe1f0"},
                        }
                    ]
                }
            }
        }
    )


def test_diff_state_reports_no_mismatch_when_states_match(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)

    def fake_run(cmd, cwd, capture=False):
        if cmd[:2] == ["terraform", "show"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=_fake_terraform_show("t2.micro"), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(localstack, "_run", fake_run)

    fixed_patch = Patch(
        unified_diff='--- a/main.tf\n+++ b/main.tf\n@@ -1,4 +1,4 @@\n resource "aws_instance" "web" {\n   ami           = "ami-0c55b159cbfafe1f0"\n-  instance_type = "t2.micrio"\n+  instance_type = "t2.micro"\n \n',
        touched_paths=["main.tf"],
    )
    mismatches = localstack.diff_state("bug-001", fixed_patch)
    assert mismatches == {}


def test_diff_state_reports_mismatch_when_states_differ(tmp_path, monkeypatch):
    corpus_root = _copy_bug_001_corpus(tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_ROOT", corpus_root)

    call_count = {"n": 0}

    def fake_run(cmd, cwd, capture=False):
        if cmd[:2] == ["terraform", "show"]:
            call_count["n"] += 1
            # first clone processed is "candidate" (still broken), second is "verified"
            value = "t2.micrio" if call_count["n"] == 1 else "t2.micro"
            return subprocess.CompletedProcess(cmd, 0, stdout=_fake_terraform_show(value), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(localstack, "_run", fake_run)

    unfixed_patch = Patch(unified_diff="", touched_paths=[])
    mismatches = localstack.diff_state("bug-001", unfixed_patch)

    assert "aws_instance.web.instance_type" in mismatches
    assert mismatches["aws_instance.web.instance_type"] == {"candidate": "t2.micrio", "verified": "t2.micro"}
