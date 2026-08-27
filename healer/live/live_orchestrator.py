"""Live-mode control flow — the live-mode counterpart to orchestrator.py.

Unlike eval mode, "review" here is a real CI wait (ci_wait.py), not a
LocalStack diff, and the retry condition is narrower: a live attempt only
continues to the next one when it actually pushed a commit AND that
commit's CI genuinely came back FAILURE (real new information worth
retrying on). Every other outcome stops:
  - WITHHOLD: no info gained; wait for the next poll cycle instead of
    burning an attempt on a low-confidence guess (see 02-architecture.md).
  - COMMIT but nothing was pushed (dry run, or the Coder made no change):
    nothing to wait on.
  - CI SUCCESS: done.
  - CI TIMEOUT: inconclusive — don't keep retrying blind.

`allow_push=False` (the default) makes this behave exactly like slice 9's
dry-run-only orchestrator: one attempt, nothing ever reaches a real
remote. `allow_push=True` is the real thing — see 02-architecture.md's
Live-trigger integration section for why that needs an explicit
human go-ahead before ever running against a real repo, not just before
the code exists.
"""
from pathlib import Path

from healer.agents import analyzer, coder, confidence, watcher
from healer.live import ci_wait, git_ops, live_watcher
from healer.live.git_ops import PushResult
from healer.live.live_watcher import LiveCase
from healer.models import AttemptRecord, CommitDecision, ReviewFeedback
from healer.thread import Thread
from healer.tools.scoped_fs import ScopedFileTool


def run_live(
    case: LiveCase, run_id: str, workdir_root: Path, max_attempts: int = 3, allow_push: bool = False
) -> tuple[Thread, PushResult | None]:
    thread = Thread.load(run_id, str(case.pr_number))

    workdir = workdir_root / str(case.pr_number) / "workdir"
    git_ops.checkout_branch(case.owner, case.repo, case.branch, workdir)

    current_error_output = case.error_output
    last_push_result: PushResult | None = None

    for attempt_number in range(len(thread.attempts) + 1, max_attempts + 1):
        structured_error = watcher.structure_error(current_error_output)

        analyzer_fs = ScopedFileTool(allowed_roots=[str(workdir)])
        file_list = analyzer.diagnose(analyzer_fs, structured_error, thread.latest_feedback())

        coder_roots = [str(workdir / p) for p in file_list.paths]
        coder_fs = ScopedFileTool(allowed_roots=coder_roots)
        patch = coder.implement_fix(coder_fs, file_list, structured_error)

        verdict = confidence.assess(patch, thread)

        review_feedback: ReviewFeedback | None = None
        should_retry = False

        if verdict.decision == CommitDecision.COMMIT:
            last_push_result = git_ops.commit_and_push(workdir, patch, attempt_number, allow_push=allow_push)

            if last_push_result.pushed:
                ci_result = ci_wait.wait_for_conclusion(case.owner, case.repo, last_push_result.commit_sha)

                if ci_result.outcome == ci_wait.CiOutcome.SUCCESS:
                    review_feedback = ReviewFeedback(passed=True, resource=None, symptom=None, attempt_delta=None)
                elif ci_result.outcome == ci_wait.CiOutcome.FAILURE:
                    if ci_result.run_id is not None:
                        current_error_output = live_watcher._fetch_failure_log_excerpt(
                            case.owner, case.repo, ci_result.run_id
                        )
                    review_feedback = ReviewFeedback(
                        passed=False,
                        resource=None,
                        symptom="CI failed again after this fix",
                        attempt_delta=f"attempt {attempt_number} pushed, CI still failing",
                    )
                    should_retry = True
                else:  # TIMEOUT
                    review_feedback = ReviewFeedback(
                        passed=False,
                        resource=None,
                        symptom="CI did not complete within the wait timeout",
                        attempt_delta="timeout",
                    )
            # not pushed (dry run, or the Coder made no change): review stays None, nothing to wait on

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

        if not should_retry:
            break

    return thread, last_push_result
