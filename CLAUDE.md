# Terraform Self-Healer

Build a self-healing CI/CD system that automatically fixes bugs in Terraform modules for AWS infrastructure, evaluated against real-world verified fixes rather than synthetic bugs.

## Evaluation approach

- **Sourcing**: Real broken Terraform/AWS scenarios pulled from sources like Stack Overflow or GitHub issues with linked fix PRs. Avoid synthetically generated bugs — they're too easy relative to real-world issues.
- **Core evaluation loop**: Feed the pipeline a real problem (error output + repo), let it attempt a fix blind, then score against a human-verified solution that stays hidden from the healer agents until final review.
- **Leakage prevention**: Enforce isolation at the tool layer, not via prompt instructions. Path allowlists/denylists baked into each agent's file-access tools. The verified solution file can live in the repo, but must sit in a directory excluded from every tool's accessible root (e.g. a hidden eval folder no agent has a path into).

## Pipeline architecture

Parallel per issue, one job per agent:

1. **Watcher** — monitors GitHub job error output, structures it. No repo access needed.
2. **Analyzer** — diagnoses the problem using trained knowledge, scoped to repo + error output. Outputs an explicit list of relevant file paths (not free text).
3. **Coder** — implements the fix. Tool access is programmatically restricted to only the paths the analyzer flagged.
4. **Confidence-check analyzer** — assesses fix confidence, decides whether to push the fix as a new commit onto the same MR that triggered the run (re-triggering that MR's pipeline), rather than opening a separate PR. This is a single-owner repo with no forked contributions, so there's no cross-permission concern with pushing directly to the branch under review.
5. **Reviewer** — only agent with access to the verified solution. Scores the fix by functional equivalence: apply both the healer's fix and the verified solution against a local AWS representation (LocalStack or similar free-tier tool) and compare resulting infrastructure state, not the code diff.

**Retry logic**: Capped retry loop (~3 attempts) with feedback between attempts — core to the "self-healing" premise, not one-shot. Eval must capture number of attempts, not just pass/fail.

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

Implementation work happens under the Gate-based workflow in `docs/plans/terraform-self-healer/` (`00-status.md` tracks gate/slice status). If asked to implement or extend this, ground the plan in that architecture (thread schema, agent boundaries, tool schemas, control-flow table) rather than inventing a new structure, and flag explicitly if a requested change would deviate from one of the 12 factors it's built on.
