# Investigation: GitHub Actions doesn't reliably fire on push to a PR branch

> **RESOLVED 2026-08-27.** Not flaky, not rate limiting, not auth — fully
> deterministic. **A `pull_request` workflow filtered on `paths:` is skipped
> when the PR's *cumulative* diff against its base is empty**, and the
> healer's correct fix empties it. Root cause and evidence in
> [Resolution](#resolution) at the bottom; the original notes below are kept
> as-is for the record, including the hypotheses that turned out to be wrong.

## Context for a fresh agent

This is the Terraform Self-Healer project (`docs/plans/terraform-self-healer/00-status.md` has the full build history — read it for background, but this doc is self-contained for the specific bug). Slice 10 ("live-trigger integration") makes the pipeline push a real fix commit to a real PR branch, then wait for that PR's CI to re-run so it can tell whether the fix worked. That CI-wait step is `healer/live/ci_wait.py`'s `wait_for_conclusion()`, called from `healer/live/live_orchestrator.py`'s `run_live()`.

**The bug**: pushing a commit to the PR branch does not reliably cause GitHub to fire the `pull_request: synchronize` event that would re-run the Actions workflow. Sometimes it does, sometimes it doesn't, and we could not find what determines which.

This matters because `ci_wait.wait_for_conclusion()` polls `GET /repos/{owner}/{repo}/actions/runs?head_sha={sha}` waiting for a run to appear and complete. When no run ever appears, it correctly times out after 15 minutes (`DEFAULT_TIMEOUT_SECONDS = 900`) and reports an inconclusive result — so nothing crashes — but it means the live self-healing loop can silently never learn whether its own fix worked.

## Environment

- Repo: `BenPetersonAIEngineering/self-healing-terraform-pipeline` (public, real GitHub repo)
- The specific branch this was tested against: `demo/bug-versioning-status` (backs [PR #1](https://github.com/BenPetersonAIEngineering/self-healing-terraform-pipeline/pull/1))
- Workflow file: `.github/workflows/terraform-demo.yml`
  ```yaml
  on:
    pull_request:
      paths:
        - "demo/**"
    push:
      branches: [main]
      paths:
        - "demo/**"
    workflow_dispatch: {}
  ```
- Auth: a classic GitHub PAT with `repo` + `workflow` scopes, stored in `.env` (gitignored) as `GITHUB_TOKEN`.
- Relevant code: `healer/live/git_ops.py` (`_auth_args()`, `checkout_branch()`, `commit_and_push()`), `healer/live/ci_wait.py` (`wait_for_conclusion()`, `_find_run_for_sha()`).

## What we observed — every real push to this branch during the debugging session, in order

| # | Commit | How it was pushed | Auth method | Triggered a run? |
|---|--------|-------------------|-------------|-------------------|
| 1 | `b0589dd4` (original deliberate bug, opened as PR #1) | `git push` after `git clone` (no explicit token) | ambient (macOS Keychain) | **Yes** — run `33118269988`, conclusion `failure` |
| 2 | `d1af59ab` (manual "trigger test") | `git clone https://x-access-token:$TOKEN@github.com/...` (token embedded in clone URL), plain `git push` | token embedded in URL | **Yes** — run `33120672844`, `success` |
| 3 | `bf0ced10` (manual "verify explicit token auth") | plain `git clone` (no auth), `git -c http.extraHeader="Authorization: Basic $(base64 of x-access-token:$TOKEN)" push` | extraHeader on push only | **Yes** — run `33120815896`, `success` |
| 4 | `b0589dd4` again (user force-pushed branch back to the original bug, to reset for a clean test) | user's own `git push --force-with-lease` from their normal shell | user's own ambient credentials | **Yes** — run `33121151268`, `failure` |
| 5 | `c3eae8eb` (**healer's first real fix**, via `git_ops.py` *before* the auth fix below existed) | `healer/live/git_ops.py checkout_branch()` + `commit_and_push()`, which at this point built git URLs with **no credentials at all** | ambient (macOS Keychain) — code didn't pass `GITHUB_TOKEN` | **No** — confirmed via `GET /actions/runs?head_sha=...` returning 0 results, repeatedly, over several minutes |
| — | (fixed `git_ops.py` to add `_auth_args()`: `-c http.extraHeader="Authorization: Basic ..."` on both the `git clone` and `git push` invocations, using `GITHUB_TOKEN`) | | | |
| 6 | `d7a09553` (**healer's second real fix**, via `git_ops.py` *after* the auth fix, `--depth 1` shallow clone) | `healer/live/git_ops.py`, now with explicit `_auth_args()` on both clone and push | explicit token via extraHeader | **No** — confirmed via repeated checks over 15+ minutes; `ci_wait` timed out |
| 7 | `2a192146` (manual "isolate test" — hand-reproduced the *exact* `git_ops.py` recipe: `--depth 1` shallow clone with extraHeader auth, then push with extraHeader auth, same commit author `"Terraform Self-Healer" <self-healer@localhost>`, built on top of commit 6) | manual shell commands replicating `git_ops.py` exactly | explicit token via extraHeader, shallow clone | **Yes** — run `33121413926`, `success`, appeared within ~15 seconds |
| 8 | `d7a09553` again (user force-pushed branch back to just the healer's fix, to leave PR #1 clean) | user's own `git push --force-with-lease` | user's own ambient credentials | **No** — checked repeatedly, no run ever appeared |

## What we ruled out

- **Token/auth mechanism**: pushes #2, #3, #7 all used different explicit-auth approaches (URL-embedded token, extraHeader-on-push-only, extraHeader-on-both) and all triggered successfully. Push #6 used the *same* mechanism as #7 (byte-for-byte identical recipe) and did not trigger.
- **Shallow vs. full clone**: #7 used `--depth 1` (matching `git_ops.py`) and still triggered fine.
- **Commit author identity**: `"Terraform Self-Healer" <self-healer@localhost>` was used in both a triggering push (#7) and a non-triggering one (#6, the very commit #7 was built on top of).
- **Base64 encoding correctness**: verified byte-for-byte that Python's `base64.b64encode()` output matches the shell `base64` command's output for the same token (ruled out an encoding bug producing a malformed header — a malformed header would likely error outright rather than silently succeed-but-not-trigger, anyway, since the push itself always succeeded in every case).
- **Whether the push actually landed**: confirmed via the GitHub API in every failing case that the commit really was the PR's new head SHA and the file content was correct — this is not a case of the push silently failing.

## What we did NOT get to test

- Whether **push frequency/volume to one branch in a short window** is the actual cause (our leading hypothesis, untested directly). All 8 pushes above happened within roughly one hour, several within the same 2-3 minute window. GitHub is known to rate-limit or deduplicate in various contexts; we don't have documentation confirming this applies to `pull_request: synchronize` dispatch specifically.
- Whether waiting a longer cooldown (e.g., 30+ minutes with no activity on the branch) before pushing produces reliable triggering.
- Whether this is specific to this repo/workflow's configuration (e.g., the `paths: - "demo/**"` filter, or `workflow_dispatch: {}` being present) vs. a general GitHub behavior — untested against a control repo/workflow without those specifics.
- GitHub's Actions/webhook status page or any account-level notices weren't checked (possible platform-side incident during the test window, 2026-08-27 ~21:15–22:15 UTC).
- We did not inspect response headers (e.g., rate-limit headers) on the push requests themselves, only on `GET` API calls — the `git push` operations went through git's own HTTP client (via `subprocess`), whose headers we didn't capture.

## Resolution

### Root cause

`.github/workflows/terraform-demo.yml` triggers on:

```yaml
on:
  pull_request:
    paths:
      - "demo/**"
```

For `pull_request` events, GitHub evaluates `paths:` against the **PR's whole
diff versus its base branch**, not against the files in the commit that was
just pushed. The demo bug (`b0589dd4`) is a one-file regression of
`demo/main.tf` away from what `main` already has, so a *correct* fix restores
that file to byte-identical with `main` — leaving the PR with **zero changed
files**. Zero changed files match no path, so GitHub fires `synchronize` and
then dispatches no run at all.

The push always worked. The event always fired. The run was filtered out.

### Evidence

Queried live against the repo after the fact:

```
GET /repos/.../pulls/1                          -> changed_files: 0,  files: []
GET /repos/.../compare/main...demo/bug-versioning-status -> 0 files
```

Changed-files-vs-base for each head SHA in the table above, against whether it
triggered — 6/6, no exceptions:

| head SHA | Triggered? | changed files vs base |
|---|---|---|
| `b0589dd4` | Yes | 1 (`demo/main.tf`) |
| `d1af59ab` | Yes | 1 |
| `bf0ced10` | Yes | 1 |
| `2a192146` | Yes | 1 |
| `c3eae8eb` | **No** | **0** |
| `d7a09553` | **No** | **0** |

This also explains the two results the original investigation called
irreconcilable:

- **#6 vs #7 ("byte-for-byte identical recipe, different outcome")** — the
  recipe was identical; the *tree* wasn't. #7 was a hand edit on top of #6 that
  made `demo/main.tf` differ from `main` again, restoring a non-empty diff.
- **#8 (user force-push, no run)** — force-pushing back to `d7a09553` restored
  the empty diff. Expected, not a second instance of flakiness.
- **#5 (`git_ops.py` with no explicit auth)** — didn't trigger because its diff
  was empty, not because of the ambient credential. The `_auth_args()` docstring
  in `git_ops.py` asserted that causal link and has been corrected; explicit
  token auth is still worth keeping, just for the ordinary reason (not depending
  on a credential that happens to be in the host's Keychain).

Every hypothesis in "What we did NOT get to test" is moot: no push-frequency
effect, no platform incident, no need for `GIT_TRACE_CURL` header capture, and
no reason to add a `workflow_dispatch` fallback trigger.

### What changed in the code

- **`healer/live/ci_wait.py`** — new `CiOutcome.NO_RUN`, distinct from
  `TIMEOUT` (a run that started and didn't finish). A `no_run_grace_seconds`
  window (default 90s) gives up as soon as it's clear nothing was dispatched,
  instead of sitting out the full 900s. `_no_run_reason()` then diagnoses:
  it resolves the PR for the SHA and, if `changed_files == 0`, says so in
  plain words. Diagnosis is best-effort and never raises.
- **`healer/live/live_orchestrator.py`** — handles `NO_RUN` separately and
  deliberately **does not retry**. Re-pushing the same tree cannot produce a
  different outcome, so a retry would only burn an attempt.
- **`healer/live/git_ops.py`** — corrected the wrong causal claim in
  `_auth_args()`'s docstring.

### Still open: the demo scenario itself is unverifiable by construction

The code changes make the failure *fast and legible*, but they don't make the
demo case scoreable. As long as the bug on `demo/bug-versioning-status` is a
pure regression away from `main`, a correct fix empties the PR and its CI can
never go green — the healer can only ever get CI feedback when it's **wrong**.

Fixing that means changing the branch on the real repo (a force-push to PR #1),
so it's left for an explicit decision. Options:

1. Have the bug branch also add something `main` doesn't have (e.g. a new
   `demo/` resource or file), so a correct fix still leaves a non-empty diff.
   Closest to how a real contributor's PR behaves.
2. Branch the demo bug from a base commit that does *not* already contain the
   correct code, so the fix is a forward change rather than a revert.
3. Drop the `paths:` filter from the `pull_request` trigger. Cheapest, but it
   only papers over it — an empty-diff PR is a degenerate eval case regardless
   of whether CI runs on it.

Option 1 is the recommendation.
