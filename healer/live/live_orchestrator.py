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
import sys
from pathlib import Path

from healer.agents import analyzer, coder, confidence, watcher
from healer.live import ci_wait, git_ops, live_watcher, pr_comment
from healer.live.git_ops import PushResult
from healer.live.live_watcher import LiveCase
from healer.models import AttemptRecord, CommitDecision, ReviewFeedback
from healer.thread import Thread
from healer.tools.scoped_fs import ScopedFileTool


def _log(msg: str) -> None:
    print(f"[live_orchestrator] {msg}", flush=True, file=sys.stderr)


def run_live(
    case: LiveCase, run_id: str, workdir_root: Path, max_attempts: int = 3, allow_push: bool = False
) -> tuple[Thread, PushResult | None]:
    thread = Thread.load(run_id, str(case.pr_number))

    _log(f"PR #{case.pr_number}: checking out {case.branch} (allow_push={allow_push})")
    workdir = workdir_root / str(case.pr_number) / "workdir"
    git_ops.checkout_branch(case.owner, case.repo, case.branch, workdir)

    current_error_output = case.error_output
    last_push_result: PushResult | None = None

    for attempt_number in range(len(thread.attempts) + 1, max_attempts + 1):
        _log(f"attempt {attempt_number}/{max_attempts}: structuring error and diagnosing")
        structured_error = watcher.structure_error(current_error_output)

        analyzer_fs = ScopedFileTool(allowed_roots=[str(workdir)])
        file_list = analyzer.diagnose(analyzer_fs, structured_error, thread.latest_feedback())
        _log(f"attempt {attempt_number}: Analyzer flagged {file_list.paths}")

        coder_roots = [str(workdir / p) for p in file_list.paths]
        coder_fs = ScopedFileTool(allowed_roots=coder_roots)
        patch = coder.implement_fix(coder_fs, file_list, structured_error)
        _log(f"attempt {attempt_number}: Coder touched {patch.touched_paths}")

        verdict = confidence.assess(patch, file_list, structured_error, thread)
        _log(f"attempt {attempt_number}: confidence={verdict.decision.value} score={verdict.score} ({verdict.reason})")

        review_feedback: ReviewFeedback | None = None
        should_retry = False

        if verdict.decision == CommitDecision.COMMIT:
            last_push_result = git_ops.commit_and_push(workdir, patch, attempt_number, allow_push=allow_push)
            _log(f"attempt {attempt_number}: commit_and_push -> pushed={last_push_result.pushed} sha={last_push_result.commit_sha}")

            if last_push_result.pushed:
                _log(f"attempt {attempt_number}: waiting for CI on {last_push_result.commit_sha[:8]}...")
                ci_result = ci_wait.wait_for_conclusion(case.owner, case.repo, last_push_result.commit_sha)
                _log(f"attempt {attempt_number}: CI outcome = {ci_result.outcome.value}")

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
                elif ci_result.outcome == ci_wait.CiOutcome.NO_RUN:
                    # Deliberately no retry: GitHub dispatching no run at all is
                    # never transient, so re-pushing the same tree just burns an
                    # attempt. Confirmed live 2026-08-27 — a fix that restores the
                    # branch to match base empties the PR diff, and the workflow's
                    # `paths:` filter then matches nothing. See
                    # investigation-ci-trigger-flakiness.md.
                    _log(f"attempt {attempt_number}: no CI run dispatched - {ci_result.detail}")
                    review_feedback = ReviewFeedback(
                        passed=False,
                        resource=None,
                        symptom=f"GitHub dispatched no CI run for this commit, so the fix is unvalidated: {ci_result.detail}",
                        attempt_delta="no-run",
                    )
                else:  # TIMEOUT
                    review_feedback = ReviewFeedback(
                        passed=False,
                        resource=None,
                        symptom="CI did not complete within the wait timeout",
                        attempt_delta="timeout",
                    )

                if allow_push:
                    pr_comment.post_comment(
                        case.owner,
                        case.repo,
                        case.pr_number,
                        pr_comment.format_comment(attempt_number, verdict, patch, ci_result),
                    )
            # not pushed (dry run, or the Coder made no change): review stays None, nothing to wait on
        elif allow_push:
            # WITHHOLD: no push happened, but still worth telling the PR why
            # the healer looked at it and did nothing — that's the whole
            # point of this feature (visibility), not just successful fixes.
            pr_comment.post_comment(
                case.owner, case.repo, case.pr_number, pr_comment.format_comment(attempt_number, verdict, patch, None)
            )

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
