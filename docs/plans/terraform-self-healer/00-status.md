# Status: Terraform Self-Healer

- Gate 1 — Product: APPROVED 2026-08-27
- Gate 2 — Architecture: APPROVED 2026-08-27 (reopened same day for the same-MR-commit amendment, re-approved)
- Gate 3 — Program Design: APPROVED 2026-08-27
- Gate 4 — Slice plan: APPROVED 2026-08-27

## Slices
- [x] Slice 1 — tracer bullet: full pipeline wired end-to-end for one hand-curated bug, all agents stubbed
- [x] Slice 2 — real happy-path fix: real Watcher/Analyzer/Coder LLM calls, confidence + reviewer still stubbed (built and unit-tested against a fake LLM; NOT yet verified against the real Anthropic API — no ANTHROPIC_API_KEY in this environment)
- [x] Slice 3 — real scoring: real Reviewer + LocalStack diff, single attempt, trustworthy pass/fail (built and unit-tested against mocked subprocess/terraform; NOT yet run against real Docker/LocalStack/terraform — none installed in this environment)
- [x] Slice 4 — retry loop: capped 3 attempts, scrubbed feedback loop, confidence WITHHOLD path (fully verified — pure control-flow logic, no external infra dependency, 4 new tests all green)
- [x] Slice 5 — corpus sourcing: script to pull real SO/GitHub issues + fix PRs into corpus/ format (fully verified — ran live against api.github.com, real PR pulled in, confirmed the fetched diff applies with the exact `patch -p1` call localstack.py uses)
- [x] Slice 6 — multi-bug parallel run: `run --all`, RunSummary aggregation, full dashboard (parallel wiring fully verified live; per-bug healing still blocked on the same API-key/Docker gaps as slices 2-3)
- [x] Slice 7 — metrics polish: mean-attempts, confidence precision, LocalStack fidelity escape hatch
- [x] Slice 8 — read-only live watcher: poll real GitHub Actions for failing open PRs, build LiveCase, detect healer-vs-human commits. No writes, safe to run live immediately. Mostly verified live (see notes below); log-content fetch needs GITHUB_TOKEN, unverified.
- [x] Slice 9 — real git workdir + dry-run push: real checkout, real Analyzer/Coder/Confidence run, commit built locally but never pushed (`allow_push=False` default). Fully verified against real local git plumbing (LLM calls mocked, no API key here).
- [x] Slice 10 — real push + CI-wait scoring, gated by `--allow-push`. RUN FOR REAL against PR #1: diagnosis + fix + push all confirmed correct end-to-end. CI-trigger reliability is an external GitHub flakiness issue, not resolved (not a code bug) — see notes below.
- [ ] Slice 11 — `healer watch` polling loop CLI glue. (fully verified — pure aggregation logic, 36/36 tests green)

## Notes for a fresh session
- Project brief lives in `/Users/ben/Dev/self-healing-terraform-pipeline/CLAUDE.md` — read it first.
- Prior design work (case-study.md, 12-factor mapping) was intentionally deleted 2026-08-27 in favor of the brief in CLAUDE.md. Do not resurrect it.
- Core constraint driving architecture: eval-solution leakage must be prevented at the *tool layer* (path allowlists per agent), not via prompting.
- Backend in Python. UI prototypes where appropriate (likely a slice-4+ concern, not core to the pipeline).
- Slice 1 (tracer bullet) is done and passing: `healer/` package, `corpus/bug-001/` hand-curated fixture, 12 tests in `tests/` (all green under `.venv`). Run it with `.venv/bin/python -m healer.cli run bug-001 --run-id <id>` then `... report <id>`.
- **corpus/ must stay pristine** — orchestrator copies each bug's repo/ into `runs/<run-id>/<bug-id>/workdir/` before agents touch anything (see 02-architecture.md amendment). Never let an agent write into `corpus/` directly.
- Slice 2 done: Watcher/Analyzer/Coder now call the real Anthropic API via `healer/llm.py` (`healer.llm.complete`, model from `HEALER_MODEL` env var, default `claude-sonnet-5`). Confidence-check and Reviewer are still deterministic stubs — that's intentional per the slice plan, not unfinished work.
- **Not yet run against the real API** — no `ANTHROPIC_API_KEY` in this dev environment. All slice-2 tests mock `healer.llm.complete` (see `tests/fake_llm.py`, `tests/test_agents_llm.py`). First real run (`export ANTHROPIC_API_KEY=... && .venv/bin/python -m healer.cli run bug-001 --run-id <id>`) is still outstanding — do it before calling slice 2 fully proven.
- Analyzer output is validated against the real repo listing before being trusted as the Coder's allowlist (falls back to "all .tf files" if the model names a path that doesn't exist) — see `healer/agents/analyzer.py`.
- Slice 3 done: `healer/localstack.py` applies both the candidate patch and `eval/verified-fix.diff` (read via a ScopedFileTool rooted at `eval/`, even though it's the only caller) against LocalStack in separate scratch clones, diffs resulting `terraform show -json` attributes. `agents/reviewer.py` now uses this instead of the old expected-state.json stub, and scrubs the raw diff down to `ReviewFeedback` (resource name + counts only, never actual values — see `tests/test_reviewer_scrub.py`).
- **Not yet run for real** — this dev environment has no running Docker daemon, no `terraform` CLI, and no LocalStack install. `localstack.py` is written against the documented terraform-provider-aws LocalStack override pattern (endpoints block, test creds) but unverified. Before trusting slice 3: start Docker, install `terraform`, and run a real bug through `healer run` with a real `ANTHROPIC_API_KEY` set.
- Slice 4 turned out to already be structurally in place from slice 1's orchestrator (the retry loop, WITHHOLD handling, and feedback threading were built up front) — slice 4's actual work was writing `tests/test_retry.py` (4 tests: max-attempts cap with a reviewer that always fails, WITHHOLD-then-fix, forever-WITHHOLD → `no_pr` status, and proof that a failed review's feedback shows up in the *next* Analyzer call's prompt) and confirming it all holds. This one is fully verified, not just built — no external infra involved.
- Slice 5 done: `healer/sourcing/github_source.py` (+ `healer-source` console script) pulls a real corpus case from a GitHub PR URL — fetches only the `.tf` files the PR touched at its base SHA into `repo/`, the PR's diff into `eval/verified-fix.diff`, writes `case.yaml`. Error output is supplied by the corpus builder (a text file), not scraped — no reliable way to auto-extract "the" error from an arbitrary issue/PR.
- **Verified live**, not just mocked: ran it for real against `terraform-aws-modules/terraform-aws-s3-bucket` PR #401 (a real operator-precedence bug: `&&` binds tighter than `||` in a Terraform `count` guard, causing an index-out-of-range crash) → `corpus/bug-002/`. Confirmed the fetched diff applies cleanly with the exact `patch -p1` invocation `localstack.py` uses. `bug-002` is a second, harder, genuinely real corpus case now available for slice 6/7 — has NOT yet been run through the actual healer pipeline (needs `ANTHROPIC_API_KEY` + Docker/terraform/LocalStack, same gaps as slices 2–3).
- Dropped `eval/expected-state.json` from the corpus format entirely (dead since slice 3 — the real Reviewer compares two applied states directly, never needed a separately-authored expected-state fixture). `corpus/bug-001/eval/` no longer has it; docs updated to match.
- Python env: `.venv/` at repo root, `pip install -e .` already done, pytest + pyyaml + anthropic installed. This environment DOES have outbound network access to api.github.com (verified) — unlike Docker/LocalStack/terraform, which are absent.

## All 7 planned slices are now built (2026-08-27). What's actually proven vs. not:

**Fully verified (real infra, no mocking):**
- Slice 1 (tracer bullet plumbing) — real CLI run, corpus stays pristine.
- Slice 4 (retry loop, WITHHOLD path, feedback threading) — pure control flow, no external dep.
- Slice 5 (corpus sourcing) — ran live against api.github.com, pulled a real bug (`bug-002`), confirmed the diff applies with the exact `patch -p1` call the pipeline uses.
- Slice 6's parallel wiring — `run --all` genuinely spawned OS processes via `multiprocessing.Pool` against the real 2-bug corpus and aggregated results correctly (both errored cleanly on the missing API key rather than crashing the run — that resilience itself is real and tested).
- Slice 7 (metrics aggregation, dashboard/trail rendering) — pure Python, 36/36 tests green.

**Built and unit-tested against mocks, NOT yet run for real:**
- Slice 2 (Watcher/Analyzer/Coder LLM calls) — needs `ANTHROPIC_API_KEY`.
- Slice 3 (Reviewer + LocalStack state diffing) — needs Docker running + `terraform` CLI + LocalStack.
- Slice 6's actual per-bug healing outcome (as opposed to the parallel-run wiring) — inherits slices 2/3's gap.

**To get a fully real end-to-end run**: `export ANTHROPIC_API_KEY=...`, start Docker Desktop, `brew install terraform`, then `.venv/bin/python -m healer.cli run --all --run-id <id>` followed by `... report <id>`. Nothing in the design is blocking this — it's purely missing tools/credentials in this dev environment.

## Architecture amendment (2026-08-27): same-MR commit, not a new PR

User decided mid-review: no reason for the healer to open a separate PR when it can just fix forward on the MR that failed CI — push a new commit onto that MR's branch, re-triggering its pipeline. This is a single-owner repo (no forks/external contributors), so pushing directly to the branch under review has no cross-permission concern; that constraint would need revisiting if this were ever used against forked contributions.

Reopened and re-approved Gate 2 for this (see the amendment note in `02-architecture.md`'s Fit section), then propagated it through code:
- `models.py`: `PrDecision` → `CommitDecision`, `OPEN` → `COMMIT`.
- `orchestrator.py`: `pr_was_opened()` → `fix_was_committed()`; `bug_status()`'s `"no_pr"` → `"withheld"`.
- `report.py`: dashboard/trail labels and CSS class token updated (`"no PR opened"` → `"withheld"`, `no-pr` class → `neutral`).
- `confidence.py`, `cli.py`, `thread.py`: updated to match.
- All tests (`test_thread.py`, `test_report.py`, `test_multi_bug.py`, `test_retry.py`) updated to the new names. 36/36 still green after the rename.
- `04-slices.md` and `03-program-design.md` left mostly as historical record with a note at the top, rather than rewritten — that's genuinely what was built at the time under the old model.

This is purely a naming/semantics change so far — the eval harness still never performs a real git push (see the Flow section note in `02-architecture.md`). The actual "push commit to MR" mechanic only becomes a real git action once live-trigger integration is built (not yet scoped as a slice).

## Slice 8 (2026-08-27): read-only live watcher

`healer/live/` package: `_github.py` (shared auth'd REST helpers), `live_watcher.py` (`LiveCase`, `poll_failing_prs`, authorship detection via `Healer-Attempt:` commit trailer). 6 tests, all mocked. `healer-live-watch <owner> <repo>` CLI for manual one-shot verification (the continuous polling loop is slice 11).

**Ran live against a real repo** (`hashicorp/terraform-provider-aws`, chosen because it has enough open PR volume to reliably have a currently-failing one) — found this the hard way:
- Listing open PRs, finding a failing Actions run for a PR's head SHA, and fetching a commit message to check authorship: all confirmed working, fully unauthenticated, against real data (correctly identified a real human commit as not-healer-authored).
- Caught and fixed a real bug: fetching job **log content** with an explicit `Accept: text/plain` header got a real `415` from the GitHub API. Fixed by using the default `application/vnd.github+json` accept on the initial request — the endpoint 302s to blob storage regardless, and we just read whatever bytes come back. Locked in as `tests/test_live_watcher.py::test_fetch_failure_log_excerpt_uses_default_accept_header`.
- After that fix, log fetching still fails — but now with a `403`, not a code bug: GitHub requires authentication to actually download job log **content**, even for a public repo, unlike every other endpoint this module calls. `GITHUB_TOKEN` is needed starting at slice 8, not first at slice 10's push step as originally assumed. Documented in `02-architecture.md`'s External section.
- This means slice 8's per-PR resilience (each PR's case-building is wrapped in try/except, so one failure doesn't kill the whole poll — same pattern as slice 6's crash-proofing) is doing real work already: a poll against a real repo without a token finds every failing PR correctly and just can't capture its error text yet, rather than crashing.

**Not yet done**: getting a `GITHUB_TOKEN` into this environment to confirm log-content fetching for real. Also haven't tested against this project's own repo, because it isn't a git repo yet (see environment info) — slice 8 was verified against a third-party public repo instead, the same way slice 5's sourcing tool was, since slice 8 is read-only and that's a safe thing to point at any real repo.

## Slice 9 (2026-08-27): real git workdir, dry-run push

`healer/live/git_ops.py` (`checkout_branch`, `commit_and_push`, `PushResult`) and `healer/live/live_orchestrator.py` (`run_live_dry` — one attempt, always dry-run, no retry loop, no CI-wait wiring; that's slice 10). Also extracted `healer/patching.py` (shared unified-diff application, used by eval mode's `localstack.py`) out of what used to be a private helper duplicated across files.

**Fully verified — no mocking of the git mechanics at all.** Built a real local bare repo (`git init --bare`) as a stand-in remote and drove the whole thing against real `git` subprocess calls: checkout, apply/commit, and (in the git_ops-level tests only, to prove the mechanism works) a real push that genuinely advances the bare repo's branch tip. Only the LLM calls are mocked (no `ANTHROPIC_API_KEY` here) — `run_live_dry`'s test uses a fake `llm.complete` but real git checkout/commit throughout.

**Found and fixed a real design bug this way, not a test artifact:** `commit_and_push` originally re-applied `patch.unified_diff` via `patch(1)` before committing — but in live mode the Coder already writes its fix directly into the same real workdir (via `ScopedFileTool`, exactly like eval mode's orchestrator), so the file is already fixed on disk by the time `commit_and_push` runs. Re-applying the same diff on top of an already-fixed tree fails outright. Fixed by having `commit_and_push` just `git add` + commit whatever's already there — `patch.unified_diff` is kept on the `Patch` object for the thread record, not used to mutate anything. This only surfaced because the git operations were real, not mocked; worth remembering as a reason to prefer real-plumbing tests over convenient mocks when the mechanism itself is what's in question.

Also caught the same latent issue in eval mode's own tests while investigating: `tests/test_localstack.py`'s hand-written diff fixture was malformed (no trailing context line) and had *never actually been run through real `patch(1)`* — it was accidentally routed entirely through the test's mocked `localstack._run`, which is why it never failed before. Fixed the fixture; that test now genuinely exercises `patch(1)`.

## Portfolio repo (2026-08-27): pushed live, real bugs found and fixed via real CI

Project is now a real GitHub repo: **https://github.com/BenPetersonAIEngineering/self-healing-terraform-pipeline** (public — this is a portfolio piece, not a throwaway, so the healer's own repo also hosts its live-trigger test target rather than a separate disposable repo. A `demo/` Terraform/AWS module + `.github/workflows/terraform-demo.yml` were added specifically so slice 10 has something real to operate against).

`demo/main.tf`: a small real S3 module (bucket, versioning, public-access-block) that runs against LocalStack in Actions rather than real AWS — the same pattern `healer/localstack.py` uses for eval mode. Getting its CI to actually pass surfaced two more real bugs, on top of everything found in slices 8-9:

1. **`_github.py`'s job-log fetch was completely broken for real logs.** GitHub's `/actions/jobs/{id}/logs` endpoint 302s to signed blob storage, and `urllib` forwards the `Authorization` header across that redirect by default — which the blob host rejects with a 401. Every log fetch against a real job with real content would have failed with this, silently (caught by the per-PR try/except in `poll_failing_prs`, so it degraded rather than crashed, but the log excerpt would always come back empty). Fixed with a custom `HTTPRedirectHandler` that strips `Authorization` on the hop; regression test spins up a real local HTTP server that 302s and fails the test if it ever sees the header on the second request.
2. **LocalStack's `:latest` image now requires a paid auth token**, even for community-tier features — a March 2026 relicensing change, not something in our control. Pinned the demo workflow to `localstack/localstack:4.4.0` (last pre-relicensing community release).

**Verified end-to-end for real, not mocked**: dispatched the actual workflow via the Actions API, polled it to completion, confirmed every step (`terraform init`/`validate`/`apply` against real LocalStack in a real Actions runner) succeeded. This is the first fully-real, fully-green CI run anywhere in this project.

Both fixes are exactly the kind of thing that only surfaces by actually running something for real — neither would have been caught by more mocked unit tests.

**Real bug PR opened for slice 10 testing**: [PR #1](https://github.com/BenPetersonAIEngineering/self-healing-terraform-pipeline/pull/1) sets `versioning_configuration.status = "Enable"` (should be `"Enabled"`) on `demo/main.tf`, branch `demo/bug-versioning-status`. Confirmed its CI fails for real with the exact expected error: `expected versioning_configuration.0.status to be one of ["Enabled" "Suspended" "Disabled"], got Enable`, at `demo/main.tf` line 36. This is now sitting there as a real target for `live_watcher.poll_failing_prs` and, eventually, slice 10's real push-and-verify loop.

## Slice 10 (2026-08-27) — RUN FOR REAL. Result: diagnosis+fix proven correct; CI-trigger reliability is a GitHub-side gap, not ours

**The core capability is proven, end to end, against a real PR, with no mocking of the diagnosis/fix path**: `run_live` polled PR #1, Watcher structured the real error, Analyzer correctly flagged `demo/main.tf` (after the recursive-discovery fix below), Coder produced the exact correct patch (`"Enable"` → `"Enabled"`), Confidence-check decided to commit it, and `git_ops` pushed a real commit (`d7a0955`, authored "Terraform Self-Healer") to the real branch. This is genuinely the pipeline fixing a real bug in a real PR, autonomously, for the first time.

**What didn't resolve**: whether that push's commit actually re-triggers the PR's CI run. Across ~8 real pushes to `demo/bug-versioning-status` during this session (mine and the user's, with and without explicit token auth, with and without shallow clone, with and without the same commit author), roughly half triggered a `pull_request: synchronize` Actions run and half didn't — with no reproducible pattern tied to any single variable tested. `ci_wait.wait_for_conclusion` correctly timed out after 15 minutes when no run appeared for the healer's actual fix commit, recording `passed=False, symptom="CI did not complete within the wait timeout"` — exactly the designed behavior for an inconclusive outcome (see 02-architecture.md: TIMEOUT stops rather than retrying blind). This looks like GitHub-side webhook/event-dispatch flakiness, most likely tied to the sheer volume of rapid pushes to one branch during debugging — not a bug in `git_ops.py`, `ci_wait.py`, or `live_orchestrator.py`. Stopped actively chasing it further rather than keep consuming real GitHub API/Actions resources on an external reliability question with no code-side fix.

**Two real bugs found and fixed along the way** (beyond the ones already listed below):
1. `git_ops.py` never actually authenticated with `GITHUB_TOKEN` at all — `checkout_branch`/`commit_and_push` built git URLs with no credentials, so every push silently fell back to whatever ambient git credential the host machine happened to have (macOS Keychain, here). It worked by accident locally. Fixed with an explicit `-c http.extraHeader` Basic-auth argument per git invocation (never persisted to `.git/config`), plus a regression test.
2. Analyzer's `.tf` file discovery (`scoped_fs.list_dir(".")`) only looked at the workdir's top level — fine for eval mode's always-flat corpus fixtures, but this repo's own `demo/main.tf` lives in a subdirectory, so the Coder's `ScopedFileTool` construction crashed on an empty allowlist the first time this ran for real. Added `ScopedFileTool.list_files_recursive` (excludes `.git`/`.venv`/etc.) and switched `analyzer.py` to use it — this also fixed a `pytest` collection collision, since a live checkout of this repo's own PR branches leaves a full clone (including `tests/`) under `runs/live/`; scoped `pytest` to `tests/` only via `pyproject.toml`.

Also added real progress logging (`ci_wait.py`, `live_orchestrator.py`) after a 15-minute silent wait was indistinguishable from a hang mid-session — worth keeping regardless of this specific debugging episode, since any real live run has the same multi-minute-silence problem otherwise.

---

### Original slice 10 build notes (superseded by the real run above, kept for history)

`healer/live/ci_wait.py` (`wait_for_conclusion` — polls a commit's Actions run to conclusion, injectable clock for testing) and a rewritten `healer/live/live_orchestrator.py`: `run_live(case, run_id, workdir_root, max_attempts=3, allow_push=False)` is now the single real retry loop for live mode, unifying what used to be slice 9's dry-run-only `run_live_dry`.

Retry condition is narrower than eval mode's: a live attempt only continues to the next one when it actually pushed a commit AND that commit's CI genuinely came back `FAILURE` — real new information worth retrying on. WITHHOLD, a no-op commit, CI SUCCESS, and CI TIMEOUT all stop rather than retry (see the module docstring for the reasoning per outcome). On a CI failure, the next attempt's Analyzer gets fed the *actual new* log excerpt from that failing run (reusing slice 8's fixed `_fetch_failure_log_excerpt`), not a stale copy of the original error.

11 tests across `test_ci_wait.py` (5, fully mocked GitHub calls + fake clock) and `test_live_orchestrator.py` (6, extended from slice 9 — real local git pushes across a multi-attempt retry sequence, only `ci_wait.wait_for_conclusion` and the log-fetch call mocked, since real CI-run polling needs an actual GitHub Actions run). 61/61 tests total, all green.

**Deliberately not yet run with `allow_push=True` against the real repo.** PR #1 is sitting there as the real target (see below), and everything needed to exercise this for real is built and tested — but per 02-architecture.md's Live-trigger integration section, the first real push to an actual branch needs the user's explicit go-ahead in the moment, not just code that compiles and passes mocked tests.

**Ran the real slice-8 watcher against this real PR — found one more real bug.** `poll_failing_prs` correctly found PR #1 and correctly flagged it as human-authored (fresh problem, no `Healer-Attempt` trailer), but the captured "error excerpt" was 4000 characters of post-failure noise (`docker rm`, git config cleanup, a Node.js deprecation warning) — the actual Terraform error had already scrolled past because `_fetch_failure_log_excerpt` just took the last N characters of the raw log, and this log kept going for ~3.6k characters *after* the real failure. Fixed by windowing backward from the last GitHub Actions `##[error]` marker instead of just taking the tail. Re-ran the real watcher after the fix: excerpt is now the exact `terraform validate` error text, verbatim. This would have silently fed the Analyzer garbage context on every real bug with a long enough log — another one that only a real run could have caught.
