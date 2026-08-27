"""Shared authenticated GitHub REST API helpers for the live/ package.

Read-only today (slice 8). git_ops.py (slice 9/10) will need a token with
write access to push commits — that's a materially bigger permission than
anything here, and is called out explicitly in 02-architecture.md's
External section rather than silently assumed.
"""
import json
import os
import urllib.request

GITHUB_API = "https://api.github.com"


def _headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {"Accept": accept, "User-Agent": "terraform-self-healer-live"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _url(path_or_url: str) -> str:
    return path_or_url if path_or_url.startswith("http") else f"{GITHUB_API}{path_or_url}"


def get_json(path_or_url: str) -> dict | list:
    req = urllib.request.Request(_url(path_or_url), headers=_headers())
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_text(path_or_url: str, accept: str = "application/vnd.github+json") -> str:
    req = urllib.request.Request(_url(path_or_url), headers=_headers(accept))
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8", errors="replace")
