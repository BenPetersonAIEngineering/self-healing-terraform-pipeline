from dataclasses import dataclass
from pathlib import Path

import yaml

CORPUS_ROOT = Path(__file__).resolve().parent.parent / "corpus"


@dataclass(frozen=True)
class Case:
    bug_id: str
    error_output: str
    repo_path: Path
    eval_path: Path
    source: str
    localstack_unsupported: bool = False
    skip_reason: str | None = None


def load_case(bug_id: str) -> Case:
    bug_dir = CORPUS_ROOT / bug_id
    case_file = bug_dir / "case.yaml"
    if not case_file.exists():
        raise FileNotFoundError(f"no corpus case at {case_file}")
    data = yaml.safe_load(case_file.read_text())
    return Case(
        bug_id=bug_id,
        error_output=data["error_output"],
        repo_path=bug_dir / "repo",
        eval_path=bug_dir / "eval",
        source=data.get("source", "unknown"),
        localstack_unsupported=data.get("localstack_unsupported", False),
        skip_reason=data.get("skip_reason"),
    )


def list_bug_ids() -> list[str]:
    if not CORPUS_ROOT.exists():
        return []
    return sorted(
        p.name for p in CORPUS_ROOT.iterdir() if p.is_dir() and (p / "case.yaml").exists()
    )
