"""Shared authenticated GitHub REST API helpers for the live/ package.

Needs a token with write access starting at slice 9 (git push) — see
02-architecture.md's External section. Confirmed live (2026-08-27) that
even read-only log fetching needs one too (slice 8 finding).
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


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Some GitHub endpoints (job logs) 302 to signed blob storage that
    rejects requests carrying our GitHub Authorization header — a stray
    401 on the *redirected* request, confirmed live against a real repo.
    urllib forwards request headers across redirects by default; this
    strips Authorization on the hop so only the initial GitHub API request
    is authenticated, matching what a browser/curl -L would actually do.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req.remove_header("Authorization")
        return new_req


_opener = urllib.request.build_opener(_StripAuthOnRedirect)


def get_json(path_or_url: str) -> dict | list:
    req = urllib.request.Request(_url(path_or_url), headers=_headers())
    with _opener.open(req) as resp:
        return json.loads(resp.read())


def get_text(path_or_url: str, accept: str = "application/vnd.github+json") -> str:
    req = urllib.request.Request(_url(path_or_url), headers=_headers(accept))
    with _opener.open(req) as resp:
        return resp.read().decode("utf-8", errors="replace")
