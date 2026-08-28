# Terraform Self-Healer

A self-healing CI/CD pipeline that automatically fixes bugs in Terraform modules for AWS infrastructure — evaluated against real-world bugs sourced from GitHub issues and their linked human fixes, not synthetic ones.

Built around a small team of narrowly-scoped agents and the [12-factor-agents](https://github.com/humanlayer/12-factor-agents) principles: explicit control flow (an LLM never decides handoffs), stateless/resumable retry state, and per-agent file access enforced at the tool layer rather than by prompting.

## How it works

```
Watcher → Analyzer → Coder → Confidence-check → Reviewer
```

1. **Watcher** structures a raw `terraform apply` error. No repo access.
2. **Analyzer** diagnoses the problem and names the exact files that need to change.
3. **Coder** implements the fix — its tool access is programmatically restricted to only the paths the Analyzer named.
4. **Confidence-check** makes a real LLM judgment call — given the diagnosed error, the Analyzer's file list, the patch diff, and retry history — and decides whether to commit the fix onto the MR under review, or hold back. (Two deterministic pre-checks short-circuit this without an LLM call: an empty patch, or an exact repeat of a diff that already failed a prior attempt.)
5. **Reviewer** (eval mode only) is the *only* agent with access to a bug's verified human fix. It scores functional equivalence by applying both the candidate patch and the verified fix to [LocalStack](https://www.localstack.cloud/) and diffing the resulting infrastructure state — not the code diff.

Capped at 3 retry attempts per bug, with scrubbed reviewer feedback fed back into the next attempt.

## Two modes

- **Eval harness** (`healer run`) — the core deliverable. Feeds the pipeline a real, previously-fixed Terraform/AWS bug (sourced via `healer-source` from a GitHub PR) and scores the healer's blind attempt against the hidden human fix.
- **Live mode** (`healer/live/`) — the same agents, pointed at a real open PR instead of a corpus fixture. Runs automatically (see [GitHub Actions integration](#github-actions-integration) below) whenever a PR's CI fails, and — when the Confidence-check agent commits — pushes the fix as a new commit onto that same PR, re-triggering its pipeline, rather than opening a separate PR.

## System diagram

```mermaid
flowchart TD
    PR["Open GitHub PR<br>demo/*.tf change"]
    CI["terraform-demo.yml<br>GitHub Actions + LocalStack"]
    PR --> CI
    CI -->|passes| GREEN(["done"])
    CI -->|fails| HOOK["self-heal.yml<br>workflow_run trigger"]

    HOOK --> RUNPR["healer-run-pr CLI"]
    CORPUS["corpus/bug-NNN<br>real GitHub-sourced bug"] --> EVALCLI["healer run<br>eval CLI"]

    RUNPR --> ORCH["orchestrator<br>retry loop, max 3 attempts"]
    EVALCLI --> ORCH

    subgraph AGENTS["Agent pipeline — tool access scoped per agent"]
        direction LR
        WATCHER["Watcher<br>structures the error<br>no repo access"]
        ANALYZER["Analyzer<br>names files to change"]
        CODER["Coder<br>writes the fix<br>ScopedFileTool: Analyzer's paths only"]
        CONF["Confidence-check<br>real LLM score<br>COMMIT / WITHHOLD"]
        WATCHER --> ANALYZER --> CODER --> CONF
    end

    ORCH --> WATCHER

    CONF -->|WITHHOLD| RETRY{"attempts < 3?"}
    RETRY -->|yes| WATCHER
    RETRY -->|no| STOP(["stopped: not fixed"])

    CONF -->|"COMMIT, live mode"| GITPUSH["git_ops<br>push fix commit to the PR branch"]
    GITPUSH --> CI
    GITPUSH --> CIWAIT["ci_wait<br>poll for the re-triggered run"]
    CIWAIT -->|success| COMMENT["pr_comment<br>post confidence + diff + result"]
    CIWAIT -->|failure| RETRY
    CIWAIT -->|no run / timeout| STOP
    COMMENT --> PR

    CONF -->|"COMMIT, eval mode"| REVIEWER["Reviewer<br>only agent with eval/ access"]
    REVIEWER --> LOCALSTACK["LocalStack<br>apply candidate vs. verified fix,<br>diff resulting AWS state"]
    LOCALSTACK -->|match| SCORED(["scored: fixed"])
    LOCALSTACK -->|mismatch| RETRY
```

## GitHub Actions integration

Live mode runs automatically, not just on demand. `.github/workflows/self-heal.yml` triggers on `workflow_run` whenever `terraform-demo.yml` completes with `conclusion == 'failure'` on a PR — no polling loop, no always-on process. It runs `healer-run-pr <owner> <repo> <pr_number> --allow-push`, which resolves the failing PR into a `LiveCase` and calls the same `live_orchestrator.run_live()` used everywhere else.

Needs two repo secrets:
- `ANTHROPIC_API_KEY`
- `HEALER_GITHUB_TOKEN` — a personal access token, **not** the default Actions-injected `GITHUB_TOKEN`. GitHub deliberately blocks the default token from re-triggering downstream workflow runs on its own pushes, which would silently break the fix-and-recheck loop this whole feature depends on.

When Confidence-check commits a fix, the pipeline posts a structured comment on the PR: the confidence score and the LLM's reasoning, the suggested fix as a diff, and the CI outcome once known. A `WITHHOLD` outcome posts nothing — a comment implies a real fix was attempted, and there's no way to word one that distinguishes "genuinely nothing to fix" from "something went wrong internally" (see the 2026-08-28 amendment in [`02-architecture.md`](docs/plans/terraform-self-healer/02-architecture.md) for what prompted that).

## Design docs

The full design history — architecture, program design, and the slice-by-slice build log with what's genuinely verified vs. still mocked — lives in [`docs/plans/terraform-self-healer/`](docs/plans/terraform-self-healer/), following a gate-based workflow (`00-status.md` is the live index). [`CLAUDE.md`](CLAUDE.md) is the original project brief.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/pytest -q

# eval mode (needs ANTHROPIC_API_KEY, Docker+LocalStack, terraform CLI)
.venv/bin/python -m healer.cli run bug-001 --run-id demo
.venv/bin/python -m healer.cli report demo

# source a new eval bug from a real GitHub fix PR
.venv/bin/python -m healer.sourcing.github_source <bug-id> <pr-url> --error-output-file <path>

# live mode, read-only (no writes, safe to run anytime)
.venv/bin/python -m healer.live.live_watcher <owner> <repo>

# live mode against one specific PR — what self-heal.yml runs automatically
.venv/bin/healer-run-pr <owner> <repo> <pr_number> --run-id demo --allow-push
```
