"""Confidence-check: assesses fix confidence, decides whether to push the
fix as a new commit onto the MR under review (or hold back).

Stub for slice 1 — commits whenever the Coder actually touched a file.
Real scoring logic lands in slice 2.
"""
from healer.models import CommitDecision, ConfidenceVerdict, Patch
from healer.thread import Thread


def assess(patch: Patch, thread: Thread) -> ConfidenceVerdict:
    if patch.touched_paths:
        return ConfidenceVerdict(score=0.9, decision=CommitDecision.COMMIT, reason="stub: patch touched files")
    return ConfidenceVerdict(score=0.1, decision=CommitDecision.WITHHOLD, reason="stub: patch touched nothing")
