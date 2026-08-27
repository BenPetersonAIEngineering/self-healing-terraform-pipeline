"""Pulls a real Terraform/AWS bug into corpus/<bug-id>/ format from a
GitHub pull request that fixed it.

Offline / corpus-build-time only — never called during a pipeline run (see
02-architecture.md's External section). The captured `terraform apply`
error output can't be reliably scraped from a PR/issue automatically, so
it's supplied by the person building the corpus, not fetched.

NOT YET VERIFIED against the live GitHub API in this environment — no
network egress or GITHUB_TOKEN exercised this session. Written against the
documented REST API (PR diff via the `.v3.diff` media type, file contents
via the Contents API at the PR's base SHA) and unit tested with a mocked
HTTP layer (tests/test_github_source.py).
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import yaml

from healer.corpus import CORPUS_ROOT

GITHUB_API = "https://api.github.com"

_PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    match = _PR_URL_RE.search(pr_url)
    if not match:
        raise ValueError(f"not a GitHub PR URL: {pr_url!r}")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def _headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {"Accept": accept, "User-Agent": "terraform-self-healer-sourcing"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _get_text(url: str, accept: str) -> str:
    req = urllib.request.Request(url, headers=_headers(accept))
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8")


def fetch_pr_meta(owner: str, repo: str, number: int) -> dict:
    return _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}")


def fetch_pr_diff(owner: str, repo: str, number: int) -> str:
    return _get_text(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}", "application/vnd.github.v3.diff")


def fetch_pr_files(owner: str, repo: str, number: int) -> list[dict]:
    return _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/files")


def fetch_file_at_ref(owner: str, repo: str, path: str, ref: str) -> str:
    data = _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={ref}")
    return base64.b64decode(data["content"]).decode("utf-8")


def build_case(bug_id: str, pr_url: str, error_output: str, out_root: Path | None = None) -> Path:
    owner, repo, number = parse_pr_url(pr_url)
    meta = fetch_pr_meta(owner, repo, number)
    base_ref = meta["base"]["sha"]

    files = fetch_pr_files(owner, repo, number)
    tf_files = [f["filename"] for f in files if f["filename"].endswith(".tf")]
    if not tf_files:
        raise ValueError(f"PR {pr_url} touches no .tf files — not usable as a Terraform bug case")

    diff_text = fetch_pr_diff(owner, repo, number)

    bug_dir = (out_root or CORPUS_ROOT) / bug_id
    repo_dir = bug_dir / "repo"
    eval_dir = bug_dir / "eval"
    repo_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)

    for path in tf_files:
        content = fetch_file_at_ref(owner, repo, path, base_ref)
        dest = repo_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    (eval_dir / "verified-fix.diff").write_text(diff_text)

    case_yaml = {"source": f"GitHub PR {pr_url}", "error_output": error_output}
    (bug_dir / "case.yaml").write_text(yaml.safe_dump(case_yaml, sort_keys=False))

    return bug_dir


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="healer-source", description="Build a corpus case from a GitHub fix PR")
    parser.add_argument("bug_id")
    parser.add_argument("pr_url", help="GitHub PR URL containing the verified fix")
    parser.add_argument("--error-output-file", required=True, help="path to a text file with the captured terraform apply error output")
    args = parser.parse_args(argv)

    error_output = Path(args.error_output_file).read_text()
    bug_dir = build_case(args.bug_id, args.pr_url, error_output)
    print(f"wrote {bug_dir}")
    return 0


def main_entry() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    main_entry()
