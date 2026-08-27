# Slices: Terraform Self-Healer

*Historical record — slices 1-7 below were built and described using the original "open a PR" model (`PrDecision.OPEN`/`WITHHOLD`, `"no_pr"` status). The 2026-08-27 architecture amendment renamed this to a commit-to-same-MR model (`CommitDecision.COMMIT`/`WITHHOLD`, `"withheld"` status) — see 02-architecture.md. Left as-written here rather than rewritten, since this is what was actually built at the time.*

1. **Tracer bullet** — full pipeline wired end-to-end for one hand-curated bug case, all five agents stubbed with hardcoded outputs. Proves the plumbing: `corpus.load_case`, `ScopedFileTool` enforcement (including a denial test against `eval/`), `Thread.load/save`, `orchestrator.run_bug` calling every stage in order, `report.render_dashboard`. No LLM calls yet.
2. **Real happy-path fix** — swap the Watcher/Analyzer/Coder stubs for real LLM calls (`llm.py` + real prompts) on that same bug. Confidence-check hardcoded to always `OPEN`; Reviewer stubbed to always `passed=True`. Produces a real diagnosis and a real patch for the first time; nothing graded yet.
3. **Real scoring** — replace the Reviewer stub with the real thing: `localstack.diff_state` against LocalStack, scrubbed `ReviewFeedback` schema, functional-equivalence pass/fail. Single attempt only, no retry loop yet. This is the first slice where "fixed" vs "not fixed" is a real, trustworthy signal.
4. **Retry loop** — capped 3-attempt loop with scrubbed Reviewer feedback fed back into the thread for the next Analyzer call, plus the confidence-check `WITHHOLD` path (no PR opened, still consumes an attempt, still eligible for retry). This is the "self-healing" behavior, not one-shot.
5. **Corpus sourcing** — script/tool to pull real Stack Overflow / GitHub issues with linked fix PRs into the `corpus/<bug-id>/` format (case.yaml, repo snapshot, eval/verified-fix.diff). Expands past the one hand-curated bug from slice 1.
6. **Multi-bug parallel run** — `healer run --all`, one job per bug (multiprocessing per the program-design decision), `RunSummary` aggregation, full dashboard with real per-bug rows and trail links (`healer report`).
7. **Metrics polish** — mean-attempts-to-success and confidence-check precision (Gate 1 secondary metrics) computed and shown on the dashboard; any LocalStack-fidelity escape hatch discovered in slice 3/5 gets formalized here rather than patched ad hoc.

Slices 5 and 6 could run in either order relative to each other since they don't depend on one another (corpus size vs. parallel execution), but both need slices 1–4 done first since they operate on the real pipeline, not stubs.

---

## Live-trigger integration (scoped 2026-08-27, current model: `CommitDecision`/same-MR-commit)

8. **Read-only live watcher** — `live_watcher.poll_failing_prs()` against the real GitHub Actions API: finds failing open-PR runs, fetches job logs, builds `LiveCase`, correctly tells the healer's own prior commits apart from a human's (the `Healer-Attempt` trailer check). Prints what it found. No repo checkout, no git writes, no LLM calls — pure read-only reconnaissance, safe to run against a real repo immediately.
9. **Real git workdir, dry-run push** — `git_ops.checkout_branch` does a real `git clone`/checkout of the PR branch into `runs/live/<pr>/workdir/`; Analyzer/Coder/Confidence run for real against it (reusing existing agent code unchanged). `commit_and_push` builds the real commit locally (trailer included) but `allow_push` defaults to `False` — it never touches the remote. Proves the whole loop short of the one irreversible step.
10. **Real push + CI-wait scoring, gated by `--allow-push`** — `commit_and_push` actually pushes when explicitly enabled; `ci_wait.wait_for_conclusion` polls the resulting Actions run to conclusion and that becomes the "review" result, wired into the retry loop. **This slice is not run for real against an actual repo without asking first** — building and unit-testing it (mocked git/GitHub calls) is fine any time; the first live execution against a real branch needs explicit go-ahead in the moment, not just "the code is done."
11. **`healer watch` polling loop** — CLI glue: repeated polling on an interval, one `run_live` per discovered failing PR, persisted attempt state across runs via `Thread`. Lower-risk than slice 10 since it's just orchestration of already-gated pieces, but depends on 8-10 being done and (for real use) already approved to run live.

Slice 8 can be built and run for real immediately (read-only). Slices 9-11 build on each other in order; slice 10 is the one that needs an explicit human go-ahead before its "real push" path is ever exercised against an actual repository, not just before the code is written.
