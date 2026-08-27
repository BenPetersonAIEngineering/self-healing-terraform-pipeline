from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class StructuredError:
    resource_address: str | None
    error_class: str
    raw_excerpt: str
    aws_service: str | None


@dataclass(frozen=True)
class FileList:
    paths: list[str]
    rationale: str


@dataclass(frozen=True)
class Patch:
    unified_diff: str
    touched_paths: list[str]


class CommitDecision(Enum):
    COMMIT = "commit"  # push the fix as a new commit on the MR under review
    WITHHOLD = "withhold"


@dataclass(frozen=True)
class ConfidenceVerdict:
    score: float
    decision: CommitDecision
    reason: str


@dataclass(frozen=True)
class ReviewFeedback:
    passed: bool
    resource: str | None
    symptom: str | None
    attempt_delta: str | None


@dataclass
class AttemptRecord:
    attempt_number: int
    structured_error: StructuredError
    file_list: FileList
    patch: Patch
    confidence: ConfidenceVerdict
    review: ReviewFeedback | None = None


@dataclass
class RunSummary:
    run_id: str
    bug_results: dict[str, str] = field(default_factory=dict)
    mean_attempts_on_success: float = 0.0
    confidence_precision: float | None = None  # None = no commits pushed yet, nothing to score
