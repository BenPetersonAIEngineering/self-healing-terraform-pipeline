from healer.live import live_watcher
from healer.live.live_watcher import LiveCase, is_healer_authored_commit


def test_is_healer_authored_commit_detects_trailer():
    assert is_healer_authored_commit("fix: correct instance type\n\nHealer-Attempt: 2\n") is True


def test_is_healer_authored_commit_false_for_ordinary_commit():
    assert is_healer_authored_commit("fix: correct instance type by hand") is False


def test_build_live_case_returns_none_when_latest_run_passed(monkeypatch):
    monkeypatch.setattr(
        live_watcher,
        "_find_latest_failing_run",
        lambda owner, repo, sha: None,
    )
    pr = {"number": 5, "head": {"sha": "abc123", "ref": "feature-x"}}
    assert live_watcher.build_live_case("me", "repo", pr) is None


def test_build_live_case_builds_case_with_log_excerpt_and_authorship(monkeypatch):
    monkeypatch.setattr(
        live_watcher,
        "_find_latest_failing_run",
        lambda owner, repo, sha: {"id": 999},
    )
    monkeypatch.setattr(
        live_watcher,
        "_fetch_failure_log_excerpt",
        lambda owner, repo, run_id, max_chars=4000: "Error: InvalidParameterValue: t2.micrio",
    )
    monkeypatch.setattr(
        live_watcher,
        "_fetch_commit_message",
        lambda owner, repo, sha: "fix: attempt\n\nHealer-Attempt: 1\n",
    )

    pr = {"number": 7, "head": {"sha": "deadbeef", "ref": "fix-branch"}}
    case = live_watcher.build_live_case("me", "repo", pr)

    assert case == LiveCase(
        pr_number=7,
        owner="me",
        repo="repo",
        branch="fix-branch",
        head_sha="deadbeef",
        error_output="Error: InvalidParameterValue: t2.micrio",
        is_healer_authored_head=True,
    )


def test_poll_failing_prs_skips_a_pr_that_raises_and_keeps_going(monkeypatch):
    monkeypatch.setattr(
        live_watcher._github,
        "get_json",
        lambda path: [
            {"number": 1, "head": {"sha": "aaa", "ref": "a"}},
            {"number": 2, "head": {"sha": "bbb", "ref": "b"}},
        ],
    )

    def fake_build_live_case(owner, repo, pr):
        if pr["number"] == 1:
            raise RuntimeError("GitHub API hiccup")
        return LiveCase(
            pr_number=2,
            owner=owner,
            repo=repo,
            branch="b",
            head_sha="bbb",
            error_output="some error",
            is_healer_authored_head=False,
        )

    monkeypatch.setattr(live_watcher, "build_live_case", fake_build_live_case)

    cases = live_watcher.poll_failing_prs("me", "repo")
    assert len(cases) == 1
    assert cases[0].pr_number == 2


def test_extract_error_excerpt_windows_around_last_error_marker_not_the_tail():
    log = (
        "...lots of build output...\n"
        "##[error]Terraform exited with code 1.\n"
        "##[error]Process completed with exit code 1.\n"
        "Post job cleanup.\n"
        "Stop and remove container: abc123\n"
        "Remove container network: def456\n"
        "Node.js 20 is deprecated, unrelated noise that pushes the real error out of a tail-only window.\n"
    )
    excerpt = live_watcher._extract_error_excerpt(log, max_chars=1000)
    assert "Terraform exited with code 1" in excerpt
    assert "Node.js 20 is deprecated" not in excerpt


def test_extract_error_excerpt_falls_back_to_tail_when_no_error_marker():
    log = "some log with no explicit error marker at all\n" * 5
    excerpt = live_watcher._extract_error_excerpt(log, max_chars=20)
    assert excerpt == log[-20:]


def test_fetch_failure_log_excerpt_uses_default_accept_header(monkeypatch):
    """Regression test: an explicit Accept: text/plain on the job-logs
    endpoint gets a real 415 from the GitHub API (confirmed live against
    hashicorp/terraform-provider-aws) — the request must use the default
    github+json accept and just read whatever body comes back after the
    redirect to blob storage."""
    monkeypatch.setattr(
        live_watcher._github,
        "get_json",
        lambda path: {"jobs": [{"id": 42, "conclusion": "failure"}]},
    )

    calls = []

    def fake_get_text(path, accept="application/vnd.github+json"):
        calls.append(accept)
        return "...log tail..."

    monkeypatch.setattr(live_watcher._github, "get_text", fake_get_text)

    result = live_watcher._fetch_failure_log_excerpt("me", "repo", 999)
    assert result == "...log tail..."
    assert calls == ["application/vnd.github+json"]


def test_poll_failing_prs_omits_healthy_prs(monkeypatch):
    monkeypatch.setattr(
        live_watcher._github,
        "get_json",
        lambda path: [{"number": 3, "head": {"sha": "ccc", "ref": "c"}}],
    )
    monkeypatch.setattr(live_watcher, "build_live_case", lambda owner, repo, pr: None)

    assert live_watcher.poll_failing_prs("me", "repo") == []
