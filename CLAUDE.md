# Terraform Self-Healer

A self-healing CI/CD system that automatically fixes bugs in Terraform modules for AWS infrastructure, on real GitHub PRs.

## Pipeline architecture

Triggered automatically by a GitHub Actions workflow (`self-heal.yml`) whenever a PR's CI fails:

1. **Watcher** — monitors GitHub job error output, structures it. No repo access needed.
2. **Analyzer** — diagnoses the problem using trained knowledge, scoped to repo + error output. Outputs an explicit list of relevant file paths (not free text).
3. **Coder** — implements the fix. Tool access is programmatically restricted to only the paths the analyzer flagged.
4. **Confidence-check** — a real LLM judgment call on the patch (score + reason), deciding whether to push the fix as a new commit onto the same MR that triggered the run (re-triggering that MR's pipeline), rather than opening a separate PR. This is a single-owner repo with no forked contributions, so there's no cross-permission concern with pushing directly to the branch under review.
5. If the fix is pushed, the pipeline waits for the re-triggered CI run and posts a structured comment on the PR: confidence score, reasoning, the suggested diff, and the CI outcome. A withheld attempt (no push) posts nothing — see `docs/plans/terraform-self-healer/02-architecture.md`'s 2026-08-28 amendment for why.

**Retry logic**: Capped retry loop (~3 attempts) with the failing CI's own log excerpt fed back into the next attempt — core to the "self-healing" premise, not one-shot.

**Versioning**: Tag each pipeline run with the model/prompt version used, so approaches can be compared over time. (Implementation detail still open — logging scheme TBD.)

## Design principles

Follow [12-factor agents](https://github.com/humanlayer/12-factor-agents) (HumanLayer). Specifically:

- Own the control flow as explicit code — orchestration between agents should be conditional logic, not another LLM deciding handoffs.
- Keep retry state stateless/explicit — pass prior attempt context back in rather than holding it in a long-running process, so any attempt can pause/resume independently.
- Each agent has one narrow job (already satisfied by the pipeline split above).
- Curate context explicitly per agent — never give an agent more repo/file access than its stage needs.

## Tech constraints

- Backend code in Python.
- Include UI prototypes where appropriate.

Implementation work happens under the Gate-based workflow in `docs/plans/terraform-self-healer/` (`00-status.md` tracks gate/slice status — as of 2026-08-28 it also records an earlier eval-harness design that was built, then cut in favor of this simpler live-only pipeline; that history is kept for context, not as live architecture). If asked to implement or extend this, ground the plan in that architecture (thread schema, agent boundaries, tool schemas, control-flow table) rather than inventing a new structure, and flag explicitly if a requested change would deviate from one of the 12 factors it's built on.
