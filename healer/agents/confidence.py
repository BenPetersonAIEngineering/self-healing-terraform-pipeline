"""Confidence-check: assesses fix confidence, decides whether to push the
fix as a new commit onto the MR under review (or hold back).

Deterministic pre-checks handle the two cases that don't need an LLM
opinion; everything else gets a real LLM judgment call. Gets no raw
repo-tool access — only the patch, the Analyzer's file list, the
structured error, and thread history, per 02-architecture.md's tool-access
table. See 02-architecture.md's 2026-08-27 amendment for the design.
"""
from healer import llm
from healer.json_util import parse_json_response
from healer.models import CommitDecision, ConfidenceVerdict, FileList, Patch, StructuredError
from healer.thread import Thread

COMMIT_THRESHOLD = 0.6

SYSTEM_PROMPT = """You are the Confidence-check agent in a self-healing Terraform CI/CD pipeline.
You are given the error the pipeline is trying to fix, the Analyzer's file list and
rationale, the Coder's patch (as a unified diff), and this bug's retry history so far.
Rate how likely this patch actually resolves the stated error — not just whether it's
syntactically plausible, but whether it addresses the specific error_class/resource
described.

Respond with ONLY a JSON object of this exact shape:
{
  "score": <float between 0.0 and 1.0>,
  "reason": "<one or two sentences>"
}"""


def assess(patch: Patch, file_list: FileList, error: StructuredError, thread: Thread) -> ConfidenceVerdict:
    if not patch.touched_paths:
        return ConfidenceVerdict(score=0.0, decision=CommitDecision.WITHHOLD, reason="patch touched no files")

    if _is_repeat_of_failed_attempt(patch, thread):
        return ConfidenceVerdict(
            score=0.0,
            decision=CommitDecision.WITHHOLD,
            reason="identical to a prior attempt's patch that already failed review/CI",
        )

    response = llm.complete(SYSTEM_PROMPT, _build_user_prompt(patch, file_list, error, thread))
    try:
        data = parse_json_response(response)
        score = float(data["score"])
        reason = str(data.get("reason", ""))
    except (ValueError, KeyError, TypeError):
        # Defensive: never let a malformed LLM response crash the pipeline
        # or, worse, silently commit an unassessed patch. Same posture as
        # Analyzer's fallback-to-all-.tf-files.
        return ConfidenceVerdict(
            score=0.0, decision=CommitDecision.WITHHOLD, reason=f"could not parse confidence response: {response[:200]!r}"
        )

    decision = CommitDecision.COMMIT if score >= COMMIT_THRESHOLD else CommitDecision.WITHHOLD
    return ConfidenceVerdict(score=score, decision=decision, reason=reason)


def _normalize_diff(diff: str) -> str:
    lines = [line.rstrip() for line in diff.splitlines()]
    return "\n".join(line for line in lines if line)


def _is_repeat_of_failed_attempt(patch: Patch, thread: Thread) -> bool:
    normalized = _normalize_diff(patch.unified_diff)
    for attempt in thread.attempts:
        if attempt.review is not None and attempt.review.passed is False:
            if _normalize_diff(attempt.patch.unified_diff) == normalized:
                return True
    return False


def _build_user_prompt(patch: Patch, file_list: FileList, error: StructuredError, thread: Thread) -> str:
    parts = [
        f"error_class: {error.error_class}",
        f"resource_address: {error.resource_address}",
        f"analyzer_rationale: {file_list.rationale}",
        f"analyzer_paths: {file_list.paths}",
        "patch:",
        patch.unified_diff,
    ]
    if thread.attempts:
        parts.append("retry_history:")
        for attempt in thread.attempts:
            review = attempt.review
            parts.append(
                f"  attempt {attempt.attempt_number}: confidence_score={attempt.confidence.score} "
                f"decision={attempt.confidence.decision.value} "
                f"review_passed={review.passed if review is not None else None}"
            )
    return "\n\n".join(parts)
