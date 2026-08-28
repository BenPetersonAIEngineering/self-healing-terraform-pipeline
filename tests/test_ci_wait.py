from healer.live import ci_wait


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_wait_for_conclusion_returns_success(monkeypatch):
    monkeypatch.setattr(
        ci_wait,
        "_find_run_for_sha",
        lambda owner, repo, sha: {"id": 111, "status": "completed", "conclusion": "success"},
    )
    clock = _FakeClock()
    result = ci_wait.wait_for_conclusion("me", "repo", "abc123", _sleep=clock.sleep, _now=clock.now)
    assert result.outcome == ci_wait.CiOutcome.SUCCESS
    assert result.run_id == 111


def test_wait_for_conclusion_returns_failure_for_non_success_conclusion(monkeypatch):
    monkeypatch.setattr(
        ci_wait,
        "_find_run_for_sha",
        lambda owner, repo, sha: {"id": 222, "status": "completed", "conclusion": "failure"},
    )
    clock = _FakeClock()
    result = ci_wait.wait_for_conclusion("me", "repo", "abc123", _sleep=clock.sleep, _now=clock.now)
    assert result.outcome == ci_wait.CiOutcome.FAILURE
    assert result.run_id == 222


def test_wait_for_conclusion_polls_until_completed(monkeypatch):
    calls = {"n": 0}

    def fake_find(owner, repo, sha):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"id": 333, "status": "in_progress", "conclusion": None}
        return {"id": 333, "status": "completed", "conclusion": "success"}

    monkeypatch.setattr(ci_wait, "_find_run_for_sha", fake_find)
    clock = _FakeClock()
    result = ci_wait.wait_for_conclusion("me", "repo", "abc123", _sleep=clock.sleep, _now=clock.now)
    assert result.outcome == ci_wait.CiOutcome.SUCCESS
    assert calls["n"] == 3


def test_wait_for_conclusion_times_out(monkeypatch):
    monkeypatch.setattr(
        ci_wait,
        "_find_run_for_sha",
        lambda owner, repo, sha: {"id": 444, "status": "in_progress", "conclusion": None},
    )
    clock = _FakeClock()
    result = ci_wait.wait_for_conclusion(
        "me", "repo", "abc123", timeout_seconds=30, poll_interval_seconds=10, _sleep=clock.sleep, _now=clock.now
    )
    assert result.outcome == ci_wait.CiOutcome.TIMEOUT
    assert result.run_id == 444


def test_no_run_within_grace_returns_no_run_not_timeout(monkeypatch):
    """A run that never appears is NO_RUN, and it gives up on the short grace
    window rather than sitting out the full timeout."""
    monkeypatch.setattr(ci_wait, "_find_run_for_sha", lambda owner, repo, sha: None)
    monkeypatch.setattr(ci_wait, "_no_run_reason", lambda owner, repo, sha: "because reasons")
    clock = _FakeClock()
    result = ci_wait.wait_for_conclusion(
        "me",
        "repo",
        "abc123",
        timeout_seconds=900,
        poll_interval_seconds=10,
        no_run_grace_seconds=20,
        _sleep=clock.sleep,
        _now=clock.now,
    )
    assert result.outcome == ci_wait.CiOutcome.NO_RUN
    assert result.run_id is None
    assert result.detail == "because reasons"
    assert clock.t == 20  # gave up on the grace window, not the 900s timeout


def test_run_appearing_late_is_not_treated_as_no_run(monkeypatch):
    """The grace window only fires while nothing has been dispatched — a run
    that shows up slowly still gets waited on to conclusion."""
    responses = [None, {"id": 555, "status": "queued", "conclusion": None}, {"id": 555, "status": "completed", "conclusion": "success"}]
    monkeypatch.setattr(ci_wait, "_find_run_for_sha", lambda owner, repo, sha: responses.pop(0))
    clock = _FakeClock()
    result = ci_wait.wait_for_conclusion(
        "me", "repo", "abc123", poll_interval_seconds=10, no_run_grace_seconds=20, _sleep=clock.sleep, _now=clock.now
    )
    assert result.outcome == ci_wait.CiOutcome.SUCCESS
    assert result.run_id == 555


def test_no_run_reason_names_the_empty_pr_diff(monkeypatch):
    """The real 2026-08-27 cause: the fix restored the branch to match base,
    emptying the PR diff, so the workflow's `paths:` filter matched nothing."""
    def fake_get_json(path):
        if path.endswith("/pulls"):
            return [{"number": 1}]
        return {"number": 1, "changed_files": 0, "base": {"ref": "main"}}

    monkeypatch.setattr(ci_wait._github, "get_json", fake_get_json)
    reason = ci_wait._no_run_reason("me", "repo", "abc123")
    assert "0 changed files against main" in reason
    assert "paths" in reason


def test_no_run_reason_survives_a_github_api_error(monkeypatch):
    """Diagnosis is best-effort; it must never mask the NO_RUN result."""
    def boom(path):
        raise RuntimeError("502")

    monkeypatch.setattr(ci_wait._github, "get_json", boom)
    assert "could not diagnose" in ci_wait._no_run_reason("me", "repo", "abc123")
