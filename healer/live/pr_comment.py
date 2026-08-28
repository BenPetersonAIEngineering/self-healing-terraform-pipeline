"""Posts a structured comment on the PR after each live attempt — the
confidence score/reason, the suggested fix diff, and (once known) the CI
outcome — so the healer's work is visible on the MR itself, not just in
CI logs or thread.json. Not a new agent: this is an orchestration-level
side effect on already-computed data (ConfidenceVerdict/Patch/CiResult),
the same category as git_ops's push. See 02-architecture.md's 2026-08-27
amendment.
"""
from healer.live import _github
from healer.live.ci_wait import CiOutcome, CiResult
from healer.models import ConfidenceVerdict, Patch

_CI_ICON = {
    CiOutcome.SUCCESS: "✅",
    CiOutcome.FAILURE: "❌",
    CiOutcome.TIMEOUT: "⏱️",
    CiOutcome.NO_RUN: "⚠️",
}


def format_comment(attempt_number: int, verdict: ConfidenceVerdict, patch: Patch, ci_result: CiResult | None) -> str:
    lines = [
        f"### 🤖 Self-Healer — attempt {attempt_number}",
        "",
        f"**Confidence:** {verdict.score:.2f} → **{verdict.decision.value.upper()}**",
        f"> {verdict.reason}",
        "",
    ]

    if patch.touched_paths:
        lines.append(f"**Files touched:** {', '.join(patch.touched_paths)}")
        lines.append("")
        lines.append("<details><summary>Suggested fix diff</summary>")
        lines.append("")
        lines.append("```diff")
        lines.append(patch.unified_diff.rstrip("\n"))
        lines.append("```")
        lines.append("</details>")
    else:
        lines.append("_No changes made this attempt._")

    if ci_result is not None:
        icon = _CI_ICON.get(ci_result.outcome, "❓")
        detail = f" — {ci_result.detail}" if ci_result.detail else ""
        lines.append("")
        lines.append(f"**CI result:** {icon} {ci_result.outcome.value}{detail}")

    lines.append("")
    lines.append("<sub>Posted automatically by the terraform-self-healer pipeline.</sub>")
    return "\n".join(lines)


def post_comment(owner: str, repo: str, pr_number: int, body: str) -> None:
    _github.post_json(f"/repos/{owner}/{repo}/issues/{pr_number}/comments", {"body": body})
