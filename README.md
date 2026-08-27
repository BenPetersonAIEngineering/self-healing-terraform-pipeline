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
4. **Confidence-check** decides whether to commit the fix onto the MR under review, or hold back.
5. **Reviewer** (eval mode only) is the *only* agent with access to a bug's verified human fix. It scores functional equivalence by applying both the candidate patch and the verified fix to [LocalStack](https://www.localstack.cloud/) and diffing the resulting infrastructure state — not the code diff.

Capped at 3 retry attempts per bug, with scrubbed reviewer feedback fed back into the next attempt.

## Two modes

- **Eval harness** (`healer run`) — the core deliverable. Feeds the pipeline a real, previously-fixed Terraform/AWS bug (sourced via `healer-source` from a GitHub PR) and scores the healer's blind attempt against the hidden human fix.
- **Live mode** (`healer/live/`) — the same agents, pointed at a real open PR instead of a corpus fixture. Polls GitHub Actions for failing PRs, and (when explicitly enabled) pushes a fix as a new commit onto that same PR — re-triggering its pipeline — rather than opening a separate PR.

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
```
