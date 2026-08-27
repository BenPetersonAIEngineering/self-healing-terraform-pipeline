"""Shared helper for parsing JSON out of an LLM text response."""
import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_json_response(text: str) -> dict:
    match = _FENCE_RE.search(text)
    payload = match.group(1) if match else text
    return json.loads(payload)
