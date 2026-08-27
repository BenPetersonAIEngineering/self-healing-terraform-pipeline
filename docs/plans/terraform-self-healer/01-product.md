# Product: Terraform Self-Healer

## Problem
Teams building "self-healing" IaC agents have no credible way to know if their pipeline actually works. Existing evals lean on synthetic, hand-crafted bugs that are easier than what shows up in real Terraform/AWS incidents, so a pipeline can look great on the eval and still fail on a real broken `apply`. There's no benchmark built from real, previously-fixed Terraform/AWS bugs (sourced from Stack Overflow / GitHub issues with linked fix PRs) that scores a candidate pipeline by whether its fix is functionally equivalent to the verified human fix — not whether the diff matches.

## Success metric
% of sourced real-world bugs the pipeline resolves with a functionally-equivalent fix (verified by applying both the pipeline's fix and the human fix against a local AWS representation and diffing resulting infra state), within the capped retry budget (~3 attempts). Secondary: mean attempts-to-fix among successes, and PR-open precision (does the confidence-check agent correctly withhold a PR when the fix is wrong).

## Announcement
Terraform Self-Healer is a benchmark and reference pipeline for self-healing Terraform CI/CD. Feed it a real broken `terraform apply` — pulled from an actual GitHub issue with a linked human fix — and watch a small team of agents (Watcher, Analyzer, Coder, Confidence-check, Reviewer) diagnose it, patch it, and decide whether it's confident enough to open a PR. Every fix is graded not on whether the code matches the original PR, but on whether it produces the same infrastructure when applied — using a local AWS stand-in, so no real cloud spend is required. Built on the 12-factor-agents principles: explicit control flow, stateless retries, and agents that only ever see the files their stage needs.

## Screens
- `eval-dashboard.html` — a run's results: one row per sourced bug, showing status (fixed / not fixed / no PR opened), attempts used, and a link to view the diagnosis trail (Watcher → Analyzer → Coder → Reviewer) for that bug.
