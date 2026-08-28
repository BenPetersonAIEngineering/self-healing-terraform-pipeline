"""Explicit control flow. The only place agent handoffs happen — never an
LLM deciding what runs next.
"""
import shutil

from healer import corpus, thread as thread_module
from healer.agents import analyzer, coder, confidence, reviewer, watcher
from healer.models import AttemptRecord, CommitDecision
from healer.thread import Thread
from healer.tools.scoped_fs import ScopedFileTool


class UnsupportedBug(Exception):
    """Raised when a corpus case is flagged localstack_unsupported: true —
    the LocalStack-fidelity escape hatch. The caller (cli.py) treats this
    as its own "unsupported" status rather than running the pipeline at
    all, since there's no way to score a fix we can't emulate."""

    def __init__(self, bug_id: str, reason: str | None):
        self.bug_id = bug_id
        self.reason = reason
        super().__init__(f"{bug_id} is flagged localstack_unsupported: {reason or 'no reason given'}")


def run_bug(run_id: str, bug_id: str, max_attempts: int = 3) -> Thread:
    case = corpus.load_case(bug_id)
    if case.localstack_unsupported:
        raise UnsupportedBug(bug_id, case.skip_reason)

    thread = Thread.load(run_id, bug_id)

    # Agents work against a scratch copy of the repo, never the corpus
    # original — corpus/ must stay a pristine, re-runnable fixture.
    workdir = thread_module.RUNS_ROOT / run_id / bug_id / "workdir"
    if workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(case.repo_path, workdir)

    structured_error = watcher.structure_error(case.error_output)

    for attempt_number in range(len(thread.attempts) + 1, max_attempts + 1):
        analyzer_fs = ScopedFileTool(allowed_roots=[str(workdir)])
        file_list = analyzer.diagnose(analyzer_fs, structured_error, thread.latest_feedback())

        coder_roots = [str(workdir / p) for p in file_list.paths]
        coder_fs = ScopedFileTool(allowed_roots=coder_roots)
        patch = coder.implement_fix(coder_fs, file_list, structured_error)

        verdict = confidence.assess(patch, file_list, structured_error, thread)

        review_feedback = None
        if verdict.decision == CommitDecision.COMMIT:
            review_feedback = reviewer.review(bug_id, patch)

        thread.attempts.append(
            AttemptRecord(
                attempt_number=attempt_number,
                structured_error=structured_error,
                file_list=file_list,
                patch=patch,
                confidence=verdict,
                review=review_feedback,
            )
        )
        thread.save()

        if review_feedback is not None and review_feedback.passed:
            break

    return thread


def bug_status(thread: Thread) -> str:
    for attempt in thread.attempts:
        if attempt.review is not None and attempt.review.passed:
            return "fixed"
    if any(a.review is not None for a in thread.attempts):
        return "not_fixed"
    return "withheld"


def fix_was_committed(thread: Thread) -> bool:
    return any(a.confidence.decision == CommitDecision.COMMIT for a in thread.attempts)
