"""Coder: implements the fix. Tool access is restricted to exactly the
paths the Analyzer flagged for this attempt.

Real LLM call as of slice 2. Asks for the full corrected content of each
flagged file (simpler and more reliable for a small Terraform module than
asking the model to produce a valid unified diff itself); the unified
diff in the returned Patch is computed locally from before/after content.
"""
import difflib

from healer import llm
from healer.json_util import parse_json_response
from healer.models import FileList, Patch, StructuredError
from healer.tools.scoped_fs import ScopedFileTool

SYSTEM_PROMPT = """You are the Coder agent in a self-healing Terraform CI/CD pipeline.
You are given the full current content of a small set of Terraform files and the error
they're causing. Produce the full corrected content for every file that needs to change.
Do not explain your reasoning. Make the smallest change that fixes the error.

Respond with ONLY a JSON object of this exact shape:
{
  "files": [
    {"path": "<one of the given file names>", "content": "<the full corrected file content>"}
  ]
}
Omit a file from "files" entirely if it doesn't need to change."""


def implement_fix(scoped_fs: ScopedFileTool, file_list: FileList, error: StructuredError) -> Patch:
    before_by_path = {path: scoped_fs.read(root) for path, root in zip(file_list.paths, scoped_fs.roots)}

    response = llm.complete(SYSTEM_PROMPT, _build_user_prompt(error, before_by_path))
    data = parse_json_response(response)
    after_by_path = {f["path"]: f["content"] for f in data.get("files", []) if f.get("path") in before_by_path}

    diff_parts = []
    touched = []
    for path, root in zip(file_list.paths, scoped_fs.roots):
        before = before_by_path[path]
        after = after_by_path.get(path, before)
        if after == before:
            continue
        scoped_fs.write(root, after)
        touched.append(path)
        diff_parts.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    return Patch(unified_diff="".join(diff_parts), touched_paths=touched)


def _build_user_prompt(error: StructuredError, file_contents: dict[str, str]) -> str:
    parts = [
        f"error_class: {error.error_class}",
        f"resource_address: {error.resource_address}",
        f"raw_excerpt:\n{error.raw_excerpt}",
        "Files:",
    ]
    for path, content in file_contents.items():
        parts.append(f"--- {path} ---\n{content}")
    return "\n\n".join(parts)
