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


def test_wait_for_conclusion_times_out_with_no_run_id_when_no_run_found(monkeypatch):
    monkeypatch.setattr(ci_wait, "_find_run_for_sha", lambda owner, repo, sha: None)
    clock = _FakeClock()
    result = ci_wait.wait_for_conclusion(
        "me", "repo", "abc123", timeout_seconds=10, poll_interval_seconds=10, _sleep=clock.sleep, _now=clock.now
    )
    assert result.outcome == ci_wait.CiOutcome.TIMEOUT
    assert result.run_id is None
