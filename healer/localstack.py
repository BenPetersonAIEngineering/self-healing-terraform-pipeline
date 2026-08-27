"""Applies a patch against LocalStack and returns the resulting infra state.

Internal to reviewer.py — reviewer.py is the only caller, and the only
agent whose ScopedFileTool includes eval/. The raw diff this module
returns is unscrubbed (it can contain verified-fix values) and must never
be returned by any agent-facing function as-is; reviewer.py is responsible
for reducing it to the fixed ReviewFeedback schema before it re-enters the
shared thread.

NOT YET VERIFIED END-TO-END: this environment has no running Docker
daemon, no `terraform` binary, and no LocalStack install (see
docs/plans/terraform-self-healer/00-status.md). Written against the
documented terraform-provider-aws LocalStack override pattern and unit
tested with a mocked subprocess layer (tests/test_localstack.py) — needs a
real run against real LocalStack before slice 3 is considered proven.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from healer.models import Patch
from healer.patching import apply_unified_diff
from healer.tools.scoped_fs import ScopedFileTool

LOCALSTACK_ENDPOINT = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")

_PROVIDER_OVERRIDE_TF = """
provider "aws" {{
  access_key                  = "test"
  secret_key                  = "test"
  region                      = "us-east-1"
  s3_use_path_style            = true
  skip_credentials_validation  = true
  skip_metadata_api_check      = true
  skip_requesting_account_id   = true

  endpoints {{
    ec2 = "{endpoint}"
    s3  = "{endpoint}"
    iam = "{endpoint}"
    sts = "{endpoint}"
  }}
}}
"""


def diff_state(bug_id: str, patch: Patch) -> dict:
    from healer import corpus

    case = corpus.load_case(bug_id)
    eval_fs = ScopedFileTool(allowed_roots=[str(case.eval_path)])
    verified_diff_text = eval_fs.read(str(case.eval_path / "verified-fix.diff"))
    verified_patch = Patch(unified_diff=verified_diff_text, touched_paths=[])

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        candidate_state = _apply_and_extract_state(case.repo_path, patch, tmp_path / "candidate")
        verified_state = _apply_and_extract_state(case.repo_path, verified_patch, tmp_path / "verified")

    mismatches = {}
    for key in sorted(set(candidate_state) | set(verified_state)):
        candidate_value = candidate_state.get(key, "<missing>")
        verified_value = verified_state.get(key, "<missing>")
        if candidate_value != verified_value:
            mismatches[key] = {"candidate": candidate_value, "verified": verified_value}
    return mismatches


def _apply_and_extract_state(repo_snapshot_dir: Path, patch: Patch, clone_dir: Path) -> dict:
    shutil.copytree(repo_snapshot_dir, clone_dir)
    apply_unified_diff(clone_dir, patch.unified_diff)
    (clone_dir / "localstack_override.tf").write_text(_PROVIDER_OVERRIDE_TF.format(endpoint=LOCALSTACK_ENDPOINT))
    try:
        _run(["terraform", "init", "-input=false"], cwd=clone_dir)
        _run(["terraform", "apply", "-auto-approve", "-input=false"], cwd=clone_dir)
        show = _run(["terraform", "show", "-json"], cwd=clone_dir, capture=True)
        return _flatten_resource_attributes(json.loads(show.stdout))
    finally:
        _run(["terraform", "destroy", "-auto-approve", "-input=false"], cwd=clone_dir)



def _flatten_resource_attributes(tf_show_json: dict) -> dict:
    flat = {}
    resources = tf_show_json.get("values", {}).get("root_module", {}).get("resources", [])
    for resource in resources:
        address = resource["address"]
        for attr, value in resource.get("values", {}).items():
            flat[f"{address}.{attr}"] = value
    return flat


def _run(cmd: list[str], cwd: Path, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=capture, text=True)
