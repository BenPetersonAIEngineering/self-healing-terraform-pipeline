"""A fake healer.llm.complete for tests that exercise agents without a real
ANTHROPIC_API_KEY. Branches on which agent's SYSTEM_PROMPT is asking, using
the agent name embedded in each prompt.
"""
import json


def fixing_fake_complete(system: str, user: str, max_tokens: int = 2048) -> str:
    if "Watcher agent" in system:
        return json.dumps(
            {
                "resource_address": "aws_instance.web",
                "error_class": "InvalidParameterValue",
                "raw_excerpt": user.strip()[:500],
                "aws_service": "ec2",
            }
        )
    if "Analyzer agent" in system:
        first_file = user.split("--- ", 1)[1].split(" ---")[0]
        return json.dumps({"paths": [first_file], "rationale": "fake: flagged the only file mentioned"})
    if "Coder agent" in system:
        path = user.split("--- ", 1)[1].split(" ---")[0]
        content = user.split(f"--- {path} ---\n", 1)[1]
        fixed = content.replace("t2.micrio", "t2.micro")
        return json.dumps({"files": [{"path": path, "content": fixed}]})
    if "Confidence-check agent" in system:
        return json.dumps({"score": 0.9, "reason": "fake: patch matches the diagnosed error"})
    raise AssertionError(f"fake_llm got an unrecognized system prompt: {system[:80]!r}")
