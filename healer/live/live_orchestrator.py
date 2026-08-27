"""Live-mode control flow. Slice 9 scope only: one attempt, checkout real
repo state, run the real Analyzer/Coder/Confidence agents against it,
build a commit locally, and stop — always dry-run (`git_ops.commit_and_push`
is called with `allow_push=False` here, unconditionally).

There is deliberately no retry loop and no CI-wait wiring in this module
yet — that's slice 10, gated on an explicit human go-ahead before it ever
runs with allow_push=True against a real repo (see
02-architecture.md's Live-trigger integration section). Keeping this
function dry-run-only, rather than exposing an allow_push parameter here,
means there is no code path in this module that can push to a real
remote — that capability doesn't exist until slice 10 adds it.
"""
from pathlib import Path

from healer.agents import analyzer, coder, confidence, watcher
from healer.live import git_ops
from healer.live.git_ops import PushResult
from healer.live.live_watcher import LiveCase
from healer.models import AttemptRecord, CommitDecision
from healer.thread import Thread
from healer.tools.scoped_fs import ScopedFileTool


def run_live_dry(case: LiveCase, run_id: str, workdir_root: Path) -> tuple[Thread, PushResult | None]:
    thread = Thread.load(run_id, str(case.pr_number))

    workdir = workdir_root / str(case.pr_number) / "workdir"
    git_ops.checkout_branch(case.owner, case.repo, case.branch, workdir)

    structured_error = watcher.structure_error(case.error_output)

    analyzer_fs = ScopedFileTool(allowed_roots=[str(workdir)])
    file_list = analyzer.diagnose(analyzer_fs, structured_error, thread.latest_feedback())

    coder_roots = [str(workdir / p) for p in file_list.paths]
    coder_fs = ScopedFileTool(allowed_roots=coder_roots)
    patch = coder.implement_fix(coder_fs, file_list, structured_error)

    verdict = confidence.assess(patch, thread)

    attempt_number = len(thread.attempts) + 1
    push_result = None
    if verdict.decision == CommitDecision.COMMIT:
        push_result = git_ops.commit_and_push(workdir, patch, attempt_number, allow_push=False)

    thread.attempts.append(
        AttemptRecord(
            attempt_number=attempt_number,
            structured_error=structured_error,
            file_list=file_list,
            patch=patch,
            confidence=verdict,
            review=None,  # no CI wait in dry-run mode — nothing to review yet
        )
    )
    thread.save()
    return thread, push_result
