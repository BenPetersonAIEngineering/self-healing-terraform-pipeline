# Architecture: Terraform Self-Healer

## Fit
Greenfield repo — no existing code (design docs only, replaced by CLAUDE.md). This is a Python CLI-driven eval harness, not a hosted web service. There's no live-CI integration in MVP scope: the "self-healing CI/CD pipeline" is exercised by feeding it pre-captured real bugs (error output + pinned repo snapshot), not by watching a live GitHub Actions run. Live-trigger integration (Watcher polling real GH Actions failures) is a later concern, structurally isolated so it can be added without touching the agent pipeline.

**Amendment (2026-08-27, pre-slice-8):** when live-trigger integration lands, the healer fixes forward on the MR that failed CI — it pushes a new commit onto that MR's existing branch (re-triggering the same MR's pipeline), rather than opening a separate PR with the fix. This is a single-owner repo with no forked/external contributions, so pushing directly to the branch under review carries no cross-permission concern; a forked-contribution setup would need to fall back to something else, but that's out of scope here. This changes what "the Confidence-check agent's decision" means throughout this doc: read every "open a PR" below as "push a fix commit to the MR under review."

## Endpoints
None. This is a CLI:
- `healer run <bug-id>` — run the pipeline against one sourced bug.
- `healer run --all` — run the full corpus (parallel per issue, per CLAUDE.md).
- `healer report <run-id>` — regenerate the static HTML dashboard from a run's stored results.

No server. The eval dashboard (Gate 1 mockup) is a static HTML file generated from the run store — avoids standing up a backend for something that's fundamentally a report.

## Data
All file-based (JSON/YAML), no database — corpus and runs are small, versioned, and benefit from being diffable in git.

- **Bug corpus** — `corpus/<bug-id>/`:
  - `case.yaml` — source URL, repo, pinned commit/tag, captured error output, links to the human fix PR.
  - `repo/` — snapshot of the Terraform repo at the broken commit (agents' accessible root).
  - `eval/verified-fix.diff` — **excluded from every agent's accessible path allowlist except the Reviewer.** (`eval/expected-state.json` was in the original design but dropped in slice 3: the Reviewer applies both the candidate patch and `verified-fix.diff` to LocalStack and diffs the two resulting states directly, so a separately-authored expected-state fixture was redundant and risked drifting out of sync with the diff.)
- **Thread/run store** — `runs/<run-id>/<bug-id>/thread.json`: the explicit, stateless context object passed between agents and across retry attempts (12-factor "own your context window" / "unify state"). Contains: structured error (Watcher output), each attempt's Analyzer file list, Coder patch, confidence-check verdict, Reviewer score, and attempt count. A retry resumes by loading this file, not by resuming a live process.
- **Run summary** — `runs/<run-id>/summary.json`: aggregated pass/fail/withheld counts + mean attempts, feeds the dashboard.

## Access control & context isolation
Leakage prevention is a first-class component, not an incidental note on storage layout. Two distinct mechanisms:

**1. Per-agent scoped file tool (enforced at the tool layer).** Each agent role gets its own instance of a shared `ScopedFileTool`, constructed with a resolved allowlist of roots — never a prompt instruction telling the agent what not to touch. The tool resolves every path (following symlinks) and raises before any read/write if the resolved path falls outside the allowlist, so a path-traversal or symlink trick can't escape it. Per-role scope:

| Agent | Read access | Write access |
|---|---|---|
| Watcher | none (structured error text only) | none |
| Analyzer | `corpus/<bug-id>/repo/` | none (outputs a path list, not files) |
| Coder | `corpus/<bug-id>/repo/`, restricted further to *only* the paths the Analyzer flagged this attempt | same restricted set |
| Confidence-check | the Coder's patch + thread context (no raw repo tool needed) | none |
| Reviewer | `corpus/<bug-id>/repo/` **and** `corpus/<bug-id>/eval/` | none (applies patches in a scratch clone, not the corpus) |

**Amendment (slice 1, 2026-08-27):** this applies to Analyzer/Coder too, not just Reviewer — the orchestrator copies `corpus/<bug-id>/repo/` to `runs/<run-id>/<bug-id>/workdir/` once per bug run, before the retry loop starts, and every agent's `ScopedFileTool` is rooted at that workdir, never at `corpus/` directly. Discovered by actually running slice 1: without this, the Coder's writes permanently mutated the corpus fixture, making a bug "pre-fixed" on the next run. `corpus/` must stay a pristine, re-runnable snapshot.

The Coder's allowlist is rebuilt every attempt from that attempt's Analyzer output — it's not a static per-bug grant, so a broadened diagnosis on retry 2 doesn't silently carry over stale access from retry 1, and a narrowed one can't be widened by the Coder itself.

**2. Feedback scrubbing on the retry path.** The Reviewer is the only agent with `eval/` access, which means its output is the one place solution content can enter the shared `thread.json` that later gets read back by the Analyzer/Coder on retry. The Reviewer never writes its raw diagnostic (which is computed against the verified fix) into the thread; it writes through a fixed reporting schema — e.g. `{resource: "aws_s3_bucket_policy.this", symptom: "resulting policy still denies GetObject", attempt_delta: "no change from prior state"}` — derived from the *observed infra state diff*, not from the verified fix's diff or file contents. This schema is validated (not just prompted) before the orchestrator appends it to `thread.json`, so there's a code-level check, not just agent discipline, between the Reviewer and the next Analyzer call.

## Flow
Per bug, orchestrated by explicit Python control-flow code (never an LLM deciding handoffs, per 12-factor):

1. Load `case.yaml` for the bug; construct the initial thread.
2. **Watcher** — structures the captured error output into a structured record. No repo access. Appends to thread.
3. **Analyzer** — given repo (path-allowlisted, `eval/` denied) + structured error + any prior-attempt feedback in the thread → outputs explicit file path list. Appends to thread.
4. **Coder** — tool access programmatically restricted to exactly the Analyzer's flagged paths → produces a patch. Appends to thread.
5. **Confidence-check analyzer** — given the patch + thread context → confidence score + commit/withhold decision (push the fix as a new commit on the MR under review, or hold back). Appends to thread.
6. If confidence too low: skip to step 8 with "withheld, no commit pushed" (still eligible for retry).
7. If committed: **Reviewer** (only agent with `eval/` access) applies both the candidate patch and `verified-fix.diff` against LocalStack, diffs resulting infra state → pass/fail + diagnostic feedback. Appends to thread.
8. Orchestrator checks: pass → done. Fail and attempts < 3 → increment attempt count, feed the Reviewer's *scrubbed* schema output (or confidence-check's own feedback, which never touched `eval/`) back into the thread, go to step 3. Fail and attempts == 3 → done, recorded as not-fixed.
9. Write final thread to `runs/<run-id>/<bug-id>/thread.json`, update `runs/<run-id>/summary.json`.

Bugs run in parallel (one job per bug); within a bug, agent steps are sequential per the retry loop above.

Note: the eval harness itself never performs a real git push — it operates on local corpus snapshots and scratch clones (see `runs/<run-id>/<bug-id>/workdir/`), so "committed" above is a scoring-time label (`PrDecision`/confidence decision), not a live git operation. The commit-to-same-MR mechanism only becomes a real git action once live-trigger integration (noted in Fit) is built.

## Live-trigger integration (post-MVP, scoped 2026-08-27)

This is the piece that makes the same-MR-commit amendment above a real git action instead of just a status label. It reuses the existing agents (Watcher/Analyzer/Coder/Confidence-check) almost unchanged; what's new is where the "repo" and "review" come from.

**Polling, not webhooks.** This is a single-owner repo with no public server to receive webhooks, so `LiveWatcher` polls the GitHub Actions API for failed workflow runs on open PRs, rather than a push-based trigger. Simpler to build and debug, and the cost of polling latency is irrelevant here.

**Attempt tracking survives across polls.** Unlike eval mode (one `healer run` invocation owns the whole retry loop), a live PR is discovered on one poll and re-checked on later ones. `runs/live/<pr-number>/thread.json` persists the attempt count between polls — same `Thread` schema already in use. The healer must be able to tell its own retries apart from a human pushing new code in between: every commit it pushes carries a `Healer-Attempt: N` trailer. If the newest failing commit doesn't have that trailer, it's a human's change — treat it as a fresh problem (new thread, attempt count resets to 1), not attempt N+1 of an old one.

**The workdir generalizes cleanly.** Eval mode's `ScopedFileTool` roots are a plain directory copy of `corpus/<bug-id>/repo/`; live mode's roots are a real `git checkout` of the PR branch into the same kind of scratch `workdir/`. Analyzer and Coder don't need to change at all — they only ever see a directory tree through `ScopedFileTool`, never "corpus" or "git" as concepts.

**Reviewer doesn't exist in live mode — there's no verified-fix to compare against.** That's an eval-only concept (Gate 2's leakage-prevention design is specifically about corpus bugs with a hidden solution). In live mode, "review" means: push the commit, then poll the newly-triggered GitHub Actions run to conclusion. `passed = (conclusion == "success")`. This is actually simpler than LocalStack diffing — no local AWS emulation needed at all for the live path, since the real MR's own CI is the ground truth.

**This introduces the first genuinely irreversible, externally-visible action in the whole project** — a real `git push` to a real branch, plus whatever the MR's CI pipeline does as a result (deploys, notifications, etc. depending on what that pipeline runs). Every slice below defaults to not pushing; the actual push is gated behind an explicit flag, off by default, and the first live test against a real repo needs the user's explicit go-ahead before it runs for real — this is not something to flip on as a side effect of "it compiles."

## External
- `ANTHROPIC_API_KEY` — LLM calls for all four agent roles (Watcher/Analyzer/Coder/Confidence-check/Reviewer are prompts, not necessarily different models — model tiering is open, per original case study).
- LocalStack (or equivalent free-tier local AWS emulator) — run as a local container during Reviewer's state-diff step. No real AWS account/spend. **Eval mode only** — live mode doesn't use LocalStack (see Live-trigger integration above).
- GitHub API — offline, corpus-build-time only (sourcing issues + linked fix PRs into `corpus/`) in eval mode. In live mode, GitHub API access is on the pipeline's critical path: polling Actions runs, fetching job logs, and — the new piece — pushing commits, which needs a token with **write** access to the repo (existing sourcing only ever needed read access).
  - **Confirmed live (2026-08-27) against `hashicorp/terraform-provider-aws`**: listing open PRs, finding a failing run for a PR's head SHA, and fetching/reading a commit message all work fully unauthenticated for a public repo. Fetching the actual job **log content** (`/actions/jobs/{id}/logs`) does not — it 403s without a token even for a public repo, unlike every other endpoint used here. So `GITHUB_TOKEN` (read scope is enough for slice 8) is required earlier than expected — not just at the slice 10 push step.
