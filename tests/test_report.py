from healer.models import (
    AttemptRecord,
    ConfidenceVerdict,
    FileList,
    Patch,
    CommitDecision,
    ReviewFeedback,
    RunSummary,
    StructuredError,
)
from healer.report import render_bug_trail, render_dashboard
from healer.thread import Thread


def test_render_dashboard_shows_precision_and_unsupported_count(tmp_path):
    summary = RunSummary(
        run_id="demo",
        bug_results={"bug-a": "fixed", "bug-b": "not_fixed", "bug-c": "unsupported"},
        mean_attempts_on_success=1.5,
        confidence_precision=0.5,
    )
    out_path = tmp_path / "dashboard.html"
    render_dashboard(summary, str(out_path))

    html = out_path.read_text()
    assert "bug-a" in html and "bug-b" in html and "bug-c" in html
    assert "50%" in html
    assert "1 unsupported" in html
    assert 'href="bug-a-trail.html"' in html


def test_render_dashboard_precision_na_when_none(tmp_path):
    summary = RunSummary(run_id="demo", bug_results={"bug-a": "withheld"}, confidence_precision=None)
    out_path = tmp_path / "dashboard.html"
    render_dashboard(summary, str(out_path))
    assert "n/a" in out_path.read_text()


def test_render_bug_trail_includes_attempt_details(tmp_path):
    thread = Thread(
        bug_id="bug-a",
        run_id="demo",
        attempts=[
            AttemptRecord(
                attempt_number=1,
                structured_error=StructuredError(resource_address="aws_instance.web", error_class="InvalidParameterValue", raw_excerpt="x", aws_service="ec2"),
                file_list=FileList(paths=["main.tf"], rationale="x"),
                patch=Patch(unified_diff="--- a/main.tf\n+++ b/main.tf\n", touched_paths=["main.tf"]),
                confidence=ConfidenceVerdict(score=0.9, decision=CommitDecision.COMMIT, reason="looks right"),
                review=ReviewFeedback(passed=False, resource="aws_instance.web", symptom="still wrong", attempt_delta="1 attribute differs"),
            )
        ],
    )
    out_path = tmp_path / "bug-a-trail.html"
    render_bug_trail(thread, str(out_path))

    html = out_path.read_text()
    assert "InvalidParameterValue" in html
    assert "main.tf" in html
    assert "still wrong" in html
    assert "failed" in html
