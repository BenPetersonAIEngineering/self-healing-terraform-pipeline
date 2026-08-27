"""One-off script for the slice-10 real run against PR #1. Not part of the
package — a throwaway runner, deleted after use."""
import sys

from healer.live import live_orchestrator, live_watcher
from healer.thread import RUNS_ROOT


def log(msg):
    print(msg, flush=True)


log("polling for failing PRs...")
cases = live_watcher.poll_failing_prs("BenPetersonAIEngineering", "self-healing-terraform-pipeline")
case = next((c for c in cases if c.pr_number == 1), None)
if case is None:
    log("PR #1 not found among failing PRs — nothing to do")
    sys.exit(1)

log(f"found PR #{case.pr_number} on branch {case.branch}, healer_authored={case.is_healer_authored_head}")
log("starting run_live (allow_push=True, max_attempts=3)...")

thread, push_result = live_orchestrator.run_live(
    case,
    run_id="slice10-real-run-2",
    workdir_root=RUNS_ROOT / "live",
    max_attempts=3,
    allow_push=True,
)

log(f"DONE. attempts={len(thread.attempts)}")
for a in thread.attempts:
    log(f"--- attempt {a.attempt_number} ---")
    log(f"file_list: {a.file_list.paths} | {a.file_list.rationale}")
    log(f"patch touched: {a.patch.touched_paths}")
    log(f"confidence: {a.confidence.decision.value} {a.confidence.score} ({a.confidence.reason})")
    log("diff:")
    log(a.patch.unified_diff)
    if a.review:
        log(f"review: passed={a.review.passed} {a.review.symptom} {a.review.attempt_delta}")

log(f"RESULT push_result: {push_result}")
