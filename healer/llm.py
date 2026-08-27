"""Thin wrapper around the Anthropic API, one call shape shared by every
agent role. Model tiering is a deliberately deferred decision (see
03-program-design.md, least-confident-decision #1) — v1 uses one model for
every role, overridable via HEALER_MODEL for experimentation.
"""
import os

import anthropic

DEFAULT_MODEL = os.environ.get("HEALER_MODEL", "claude-sonnet-5")

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Real agent calls (Watcher/Analyzer/"
                "Coder) need it — see docs/plans/terraform-self-healer/00-status.md."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def complete(system: str, user: str, max_tokens: int = 2048) -> str:
    """Single request/response call. Returns the response's text content.

    Not a chat loop and not a tool-use loop — each agent role is one
    request in, one answer out, per the pipeline's stateless-attempt design.
    """
    response = _get_client().messages.create(
        model=DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
