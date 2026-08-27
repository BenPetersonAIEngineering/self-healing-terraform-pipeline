import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from healer.models import (
    AttemptRecord,
    ConfidenceVerdict,
    FileList,
    Patch,
    CommitDecision,
    ReviewFeedback,
    StructuredError,
)

RUNS_ROOT = Path(__file__).resolve().parent.parent / "runs"


@dataclass
class Thread:
    bug_id: str
    run_id: str
    attempts: list[AttemptRecord] = field(default_factory=list)

    @property
    def _path(self) -> Path:
        return RUNS_ROOT / self.run_id / self.bug_id / "thread.json"

    @classmethod
    def load(cls, run_id: str, bug_id: str) -> "Thread":
        path = RUNS_ROOT / run_id / bug_id / "thread.json"
        if not path.exists():
            return cls(bug_id=bug_id, run_id=run_id, attempts=[])
        raw = json.loads(path.read_text())
        attempts = [_attempt_from_dict(a) for a in raw["attempts"]]
        return cls(bug_id=raw["bug_id"], run_id=raw["run_id"], attempts=attempts)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bug_id": self.bug_id,
            "run_id": self.run_id,
            "attempts": [_attempt_to_dict(a) for a in self.attempts],
        }
        self._path.write_text(json.dumps(payload, indent=2))

    def latest_feedback(self) -> ReviewFeedback | None:
        for attempt in reversed(self.attempts):
            if attempt.review is not None:
                return attempt.review
        return None


def _attempt_to_dict(attempt: AttemptRecord) -> dict:
    d = asdict(attempt)
    d["confidence"]["decision"] = attempt.confidence.decision.value
    return d


def _attempt_from_dict(d: dict) -> AttemptRecord:
    confidence_d = dict(d["confidence"])
    confidence_d["decision"] = CommitDecision(confidence_d["decision"])
    review = ReviewFeedback(**d["review"]) if d.get("review") is not None else None
    return AttemptRecord(
        attempt_number=d["attempt_number"],
        structured_error=StructuredError(**d["structured_error"]),
        file_list=FileList(**d["file_list"]),
        patch=Patch(**d["patch"]),
        confidence=ConfidenceVerdict(**confidence_d),
        review=review,
    )
