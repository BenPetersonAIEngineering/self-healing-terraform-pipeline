"""Analyzer: diagnoses the problem, scoped to repo + error output.

Real LLM call as of slice 2. Outputs an explicit list of relevant file
paths (not free text) — this list becomes the Coder's entire filesystem
allowlist, so it's validated against what actually exists in the repo
before being trusted, not just parsed and passed through.
"""
from healer import llm
from healer.json_util import parse_json_response
from healer.models import FileList, ReviewFeedback, StructuredError
from healer.tools.scoped_fs import ScopedFileTool

SYSTEM_PROMPT = """You are the Analyzer agent in a self-healing Terraform CI/CD pipeline.
Given a structured error and the contents of the Terraform repo's .tf files, diagnose
the problem and decide which files need to change to fix it. You do not write the fix
yourself — only point to the files.

Respond with ONLY a JSON object of this exact shape:
{
  "paths": ["<file names from the provided listing that need to change>"],
  "rationale": "<one or two sentences on why>"
}
Only list paths that appear in the provided file listing."""


def diagnose(
    scoped_fs: ScopedFileTool,
    error: StructuredError,
    prior_feedback: ReviewFeedback | None,
) -> FileList:
    tf_files = scoped_fs.list_files_recursive(".", suffix=".tf")
    file_contents = {f: scoped_fs.read(f) for f in tf_files}

    user_prompt = _build_user_prompt(error, prior_feedback, file_contents)
    response = llm.complete(SYSTEM_PROMPT, user_prompt)
    data = parse_json_response(response)

    candidate_paths = data.get("paths", [])
    valid_paths = [p for p in candidate_paths if p in tf_files]
    rationale = data.get("rationale", "")

    if not valid_paths:
        # Defensive fallback: never hand the Coder an empty or hallucinated
        # allowlist. If the model didn't point at a real file, flag every
        # .tf file rather than silently doing nothing.
        valid_paths = tf_files
        rationale = f"{rationale} (fallback: no valid path in model response, flagged all .tf files)".strip()

    return FileList(paths=valid_paths, rationale=rationale)


def _build_user_prompt(
    error: StructuredError,
    prior_feedback: ReviewFeedback | None,
    file_contents: dict[str, str],
) -> str:
    parts = [
        f"error_class: {error.error_class}",
        f"resource_address: {error.resource_address}",
        f"aws_service: {error.aws_service}",
        f"raw_excerpt:\n{error.raw_excerpt}",
    ]
    if prior_feedback is not None:
        parts.append(
            "This is a retry. The previous attempt failed review:\n"
            f"resource: {prior_feedback.resource}\n"
            f"symptom: {prior_feedback.symptom}\n"
            f"attempt_delta: {prior_feedback.attempt_delta}"
        )
    parts.append("Repo files:")
    for path, content in file_contents.items():
        parts.append(f"--- {path} ---\n{content}")
    return "\n\n".join(parts)
