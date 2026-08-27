# Program Design: Terraform Self-Healer

## Files

```
healer/
  __init__.py
  cli.py                  # CLI entrypoints: run, run --all, report
  models.py                # shared dataclasses: StructuredError, FileList, Patch, ConfidenceVerdict, ReviewFeedback, AttemptRecord, RunSummary
  corpus.py                 # load/validate corpus/<bug-id>/case.yaml
  thread.py                  # Thread object: load/save runs/<run-id>/<bug-id>/thread.json
  orchestrator.py             # explicit control flow + retry loop (owns all handoffs)
  llm.py                        # thin wrapper around Anthropic API calls, one function per agent prompt
  tools/
    scoped_fs.py                # ScopedFileTool — allowlist-enforced file access
  agents/
    watcher.py                    # structures raw error output
    analyzer.py                    # diagnoses, returns file path list
    coder.py                        # implements patch within Coder's scoped tool
    confidence.py                    # confidence score + commit/withhold decision (push a fix commit to the MR under review, or hold back)
    reviewer.py                       # applies both patches to LocalStack, diffs state, scrubs feedback
  localstack.py                       # apply patch to a scratch clone, run against LocalStack, diff resulting state
  report.py                            # renders eval-dashboard.html from RunSummary
corpus/
  <bug-id>/case.yaml
  <bug-id>/repo/                      # snapshot at broken commit
  <bug-id>/eval/verified-fix.diff
runs/
  <run-id>/<bug-id>/thread.json
  <run-id>/summary.json
  <run-id>/eval-dashboard.html
tests/
  test_scoped_fs.py
  test_thread.py
  test_orchestrator.py
  test_reviewer_scrub.py
  test_report.py
```

Each file exists because it maps to exactly one 12-factor boundary from the architecture doc: `orchestrator.py` is the only place control flow lives (factor: own your control flow); `thread.py` is the only place state is read/written (factor: own your context window, stateless resumable attempts); `tools/scoped_fs.py` is the only place file I/O is gated (factor: tool-layer isolation, not prompts); each `agents/*.py` is a single narrow responsibility with no cross-agent imports.

## Types & signatures

```python
# models.py
from dataclasses import dataclass
from enum import Enum

@dataclass(frozen=True)
class StructuredError:
    resource_address: str | None
    error_class: str          # e.g. "AccessDenied", "CyclicDependency", "InvalidParameter"
    raw_excerpt: str
    aws_service: str | None

@dataclass(frozen=True)
class FileList:
    paths: list[str]          # relative to corpus/<bug-id>/repo/; may include not-yet-existing paths for new files
    rationale: str

@dataclass(frozen=True)
class Patch:
    unified_diff: str
    touched_paths: list[str]

class CommitDecision(Enum):
    COMMIT = "commit"      # push the fix as a new commit on the MR under review
    WITHHOLD = "withhold"

@dataclass(frozen=True)
class ConfidenceVerdict:
    score: float               # 0.0-1.0
    decision: CommitDecision
    reason: str

@dataclass(frozen=True)
class ReviewFeedback:
    passed: bool
    resource: str | None
    symptom: str | None        # derived from observed state diff only, never from verified-fix content
    attempt_delta: str | None  # e.g. "no change from prior state" | "new resource now conflicts"

@dataclass
class AttemptRecord:
    attempt_number: int
    structured_error: StructuredError
    file_list: FileList
    patch: Patch
    confidence: ConfidenceVerdict
    review: ReviewFeedback | None   # None if commit withheld

@dataclass
class RunSummary:
    run_id: str
    bug_results: dict[str, str]     # bug_id -> "fixed" | "not_fixed" | "withheld"
    mean_attempts_on_success: float


# tools/scoped_fs.py
class PathNotAllowed(Exception): ...

class ScopedFileTool:
    def __init__(self, allowed_roots: list[str]): ...
    def read(self, path: str) -> str: ...              # raises PathNotAllowed if resolved path escapes allowed_roots
    def write(self, path: str, content: str) -> None: ...
    def list_dir(self, path: str) -> list[str]: ...


# thread.py
@dataclass
class Thread:
    bug_id: str
    run_id: str
    attempts: list[AttemptRecord]

    @classmethod
    def load(cls, run_id: str, bug_id: str) -> "Thread": ...
    def save(self) -> None: ...
    def latest_feedback(self) -> ReviewFeedback | None: ...


# agents/watcher.py
def structure_error(raw_output: str) -> StructuredError: ...

# agents/analyzer.py
def diagnose(scoped_fs: ScopedFileTool, error: StructuredError, prior_feedback: ReviewFeedback | None) -> FileList: ...

# agents/coder.py
def implement_fix(scoped_fs: ScopedFileTool, file_list: FileList, error: StructuredError) -> Patch: ...

# agents/confidence.py
def assess(patch: Patch, thread: Thread) -> ConfidenceVerdict: ...

# agents/reviewer.py
def review(bug_id: str, patch: Patch) -> ReviewFeedback: ...   # only function in the codebase constructed with eval/ in its allowed_roots

# localstack.py
def diff_state(bug_id: str, patch: Patch) -> dict: ...          # applies patch in scratch clone, returns raw state diff (internal to reviewer.py only)

# orchestrator.py
def run_bug(run_id: str, bug_id: str, max_attempts: int = 3) -> AttemptRecord: ...   # the only function that calls every agent in sequence

# report.py
def render_dashboard(summary: RunSummary, out_path: str) -> None: ...

# cli.py
def main(argv: list[str]) -> int: ...
```

## Call stack

**`healer run <bug-id>`**
```
cli.main
  -> corpus.load_case(bug_id)
  -> orchestrator.run_bug(run_id, bug_id)
       -> thread.Thread.load (or new)
       -> agents.watcher.structure_error(case.raw_error)
       loop attempt in 1..max_attempts:
         -> ScopedFileTool(roots=[repo/])                      # Analyzer's tool
         -> agents.analyzer.diagnose(scoped_fs, error, thread.latest_feedback())
         -> ScopedFileTool(roots=file_list.paths)               # Coder's tool, rebuilt every attempt
         -> agents.coder.implement_fix(scoped_fs, file_list, error)
         -> agents.confidence.assess(patch, thread)
         if decision == WITHHOLD: record AttemptRecord(review=None); continue or stop
         if decision == COMMIT:
           -> agents.reviewer.review(bug_id, patch)
                -> localstack.diff_state(bug_id, patch)          # internal, has eval/ access
                -> (scrub to ReviewFeedback schema)
           record AttemptRecord(review=feedback)
           if feedback.passed: break
       -> thread.save()
  -> update runs/<run-id>/summary.json
```

**`healer report <run-id>`**
```
cli.main -> report.render_dashboard(RunSummary.load(run_id), out_path)
```

## Test plan

- `test_scoped_fs_denies_path_outside_allowed_roots` — read/write outside allowlist raises `PathNotAllowed`.
- `test_scoped_fs_denies_symlink_escape` — a symlink inside an allowed root pointing to `eval/` is denied (resolves before checking).
- `test_scoped_fs_coder_allowlist_matches_analyzer_output_exactly` — Coder's tool root set equals the current attempt's `FileList.paths`, not the full repo and not a prior attempt's list.
- `test_reviewer_feedback_excludes_verified_fix_content` — given a `verified-fix.diff` fixture, asserts no substring of it appears anywhere in the `ReviewFeedback` written to `thread.json`.
- `test_thread_resumable_from_disk` — save a `Thread` mid-run, reload via `Thread.load`, assert identical attempts list (validates the stateless/resumable retry claim).
- `test_orchestrator_stops_at_max_attempts` — reviewer always returns `passed=False`; assert exactly 3 `AttemptRecord`s and final status `not_fixed`.
- `test_orchestrator_confidence_withhold_still_counts_as_attempt` — `WITHHOLD` decision consumes an attempt and can still retry, per architecture (no commit pushed, still eligible for retry).
- `test_report_render_matches_summary_counts` — dashboard HTML row count and status labels match `RunSummary.bug_results`.

## Least confident decisions

1. **Model tiering per agent role is undecided.** Right now `llm.py` is one wrapper called by all five roles with different prompts but no stated model choice. Cheapest correct default: same model everywhere for v1, revisit once we have real cost/accuracy numbers from a run. Worth challenging now since it's a one-line change in `llm.py` today and a bigger one once agents have role-specific prompt tuning built around a specific model's quirks.
2. **New-file creation is allowed but not yet bounded.** `FileList.paths` can include paths that don't exist yet (net-new resource file), and the Coder's `ScopedFileTool` is built from that exact list — so the Coder *can* create a new file, but only at a path the Analyzer explicitly named. Confirm this is the intended boundary (vs. requiring Analyzer to name a directory and letting Coder pick a filename within it, which is looser).
3. **LocalStack fidelity is unverified against the actual corpus.** The architecture assumes LocalStack's free tier can represent whatever AWS services the sourced bugs touch. If early corpus bugs hit services LocalStack emulates poorly (e.g. some IAM/cross-account behaviors), `localstack.diff_state` may need a per-bug "unsupported, skip" escape hatch — better to decide that now than discover it mid-slice-3.
4. **Parallelism model for "one job per bug" isn't chosen.** `orchestrator.run_bug` is written as a plain function; running many bugs in parallel (per CLAUDE.md) means either multiprocessing or async fan-out in `cli.py`, and each parallel job needs its own LocalStack instance/port to avoid state collision. Proposing: `multiprocessing.Pool` over bug ids for v1, each worker starting its own LocalStack container — simplest, revisit if LocalStack startup cost makes it too slow.

---

## Addendum (2026-08-27): live-trigger integration

Program design for the "push a commit to the real MR" capability scoped in `02-architecture.md`'s Live-trigger integration section. Builds on the existing modules — no changes to `agents/watcher.py`, `agents/analyzer.py`, `agents/coder.py`, or `tools/scoped_fs.py`.

### Files

```
healer/
  live/
    __init__.py
    live_watcher.py      # polls GitHub Actions for failing open-PR runs; builds a LiveCase
    git_ops.py            # checkout PR branch into a real workdir, commit, push (behind --allow-push)
    ci_wait.py              # polls a pushed commit's Actions run to conclusion; this IS live mode's "reviewer"
    live_orchestrator.py     # run_live(pr_number, max_attempts=3) — same retry shape as orchestrator.run_bug, LocalStack/Reviewer swapped for git_ops+ci_wait
tests/
  test_live_watcher.py    # mocked GitHub API — attempt-tracking/human-vs-healer-commit detection
  test_git_ops.py          # mocked git/subprocess — commit trailer format, dry-run vs real push gating
  test_ci_wait.py            # mocked GitHub API — polling/timeout/conclusion mapping
```

`live_orchestrator.py` is a separate module from `orchestrator.py`, not a branch inside it — the control flow is similar but the scoring step is a fundamentally different kind of thing (a live CI wait vs. a local LocalStack diff), and keeping them separate means eval-mode code paths are never one flag away from accidentally trying to push to a real repo.

### Types & signatures

```python
# live/live_watcher.py
@dataclass(frozen=True)
class LiveCase:
    pr_number: int
    owner: str
    repo: str
    branch: str
    head_sha: str
    error_output: str
    is_healer_authored_head: bool   # True if head_sha's commit carries our Healer-Attempt trailer

def poll_failing_prs(owner: str, repo: str) -> list[LiveCase]: ...

# live/git_ops.py
@dataclass(frozen=True)
class PushResult:
    pushed: bool          # False when allow_push=False (dry run)
    commit_sha: str | None
    would_push_message: str | None   # populated on dry run, so callers/tests can assert on intent without a real push

def checkout_branch(owner: str, repo: str, branch: str, workdir: Path) -> None: ...
def commit_and_push(workdir: Path, patch: Patch, attempt_number: int, allow_push: bool) -> PushResult: ...
   # commit message trailer: "Healer-Attempt: {attempt_number}" — this is what is_healer_authored_head checks for

# live/ci_wait.py
class CiOutcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"

def wait_for_conclusion(owner: str, repo: str, commit_sha: str, timeout_seconds: int = 900) -> CiOutcome: ...

# live/live_orchestrator.py
def run_live(pr_number: int, owner: str, repo: str, max_attempts: int = 3, allow_push: bool = False) -> Thread: ...
```

### Call stack

**`healer watch --repo <owner>/<repo> [--allow-push]`**
```
cli.main
  -> live_watcher.poll_failing_prs(owner, repo)
  for each LiveCase:
    -> Thread.load("live", str(pr_number))       # reuses existing Thread/thread.json machinery
    -> if case.is_healer_authored_head is False: reset thread (fresh problem, not our retry)
    -> live_orchestrator.run_live(pr_number, owner, repo, allow_push=args.allow_push)
         -> git_ops.checkout_branch(...)  into runs/live/<pr>/workdir/     # real git checkout, not a corpus copy
         loop attempt in 1..max_attempts:
           -> ScopedFileTool(roots=[workdir])         # same Analyzer/Coder code as eval mode, unchanged
           -> agents.analyzer.diagnose(...)
           -> agents.coder.implement_fix(...)
           -> agents.confidence.assess(...)
           if WITHHOLD: record AttemptRecord(review=None); stop (don't push an unreviewed guess)
           if COMMIT:
             -> git_ops.commit_and_push(workdir, patch, attempt_number, allow_push)
             if not allow_push: record dry-run AttemptRecord, stop (nothing to wait on)
             -> ci_wait.wait_for_conclusion(owner, repo, push_result.commit_sha)
             -> record AttemptRecord(review=ReviewFeedback(passed=(outcome==SUCCESS), ...))
             if passed: break
         -> thread.save()
```

Note the WITHHOLD branch **stops** rather than retrying immediately in live mode — unlike eval mode, there's no cheap local re-attempt; each live attempt means real time waiting on a real CI run, so a withheld low-confidence attempt here means "wait for the next poll cycle" (which may pick up new information) rather than "immediately try again with the same input."

### Test plan (new)

- `test_is_healer_authored_head_detects_own_trailer` — a commit with `Healer-Attempt: N` in its message → `is_healer_authored_head=True`; any other commit → `False`.
- `test_poll_resets_attempt_count_on_human_commit` — thread has 2 prior attempts; new head commit has no trailer → thread resets to a fresh attempt sequence, not attempt 3.
- `test_dry_run_never_calls_real_push` — `allow_push=False` → `commit_and_push` never invokes the actual `git push` subprocess call (asserted via a mock that fails the test if invoked), returns a populated `would_push_message` instead.
- `test_ci_wait_maps_conclusions_correctly` — mocked Actions API responses for success/failure/in-progress-then-timeout all map to the correct `CiOutcome`.
- `test_run_live_stops_on_low_confidence_without_pushing` — confidence WITHHOLD → no `git_ops.commit_and_push` call at all.

### Least confident decisions (new)

5. **Attempt-reset-on-human-commit relies entirely on a commit trailer convention.** If someone manually edits a healer commit (rebase, amend) the trailer could survive onto a human change, or a human's commit message could accidentally contain the same trailer text. Low risk in a single-owner repo, but worth a code comment flagging the assumption rather than silently trusting it forever.
6. ~~No explicit "give up and notify a human" step.~~ **Resolved 2026-08-27**: intentionally no separate notification. After `max_attempts` failed live attempts, `run_live` just stops and the MR is left with its CI still failing — that failure *is* the notification (it's what a human would see regardless of whether the healer ever existed). No PR comment, no external alert. Revisit only if this stops being a single-owner repo someone is actively watching.
7. ~~`timeout_seconds=900` (15 min) is a guess.~~ **Resolved 2026-08-27**: confirmed as the right starting value. Still worth checking against real pipeline runtime once slice 10 actually runs live, but not a blocker.
