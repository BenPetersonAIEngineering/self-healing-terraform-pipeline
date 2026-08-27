import html

from healer.models import RunSummary
from healer.thread import Thread

_STATUS_CLASS = {"fixed": "fixed", "not_fixed": "not-fixed", "withheld": "neutral", "unsupported": "neutral", "error": "not-fixed"}
_STATUS_LABEL = {
    "fixed": "fixed",
    "not_fixed": "not fixed",
    "withheld": "withheld (no commit pushed)",
    "unsupported": "unsupported (LocalStack fidelity)",
    "error": "error (pipeline crashed)",
}

_DASHBOARD_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Eval Dashboard: {run_id}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2rem; color: #222; }}
  h1 {{ font-size: 1.3rem; }}
  .meta {{ color: #666; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e0e0e0; font-size: 0.9rem; }}
  th {{ color: #555; font-weight: 600; }}
  .status {{ padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.8rem; font-weight: 600; }}
  .fixed {{ background: #d4f4dd; color: #1a7a3a; }}
  .not-fixed {{ background: #fbdcdc; color: #a3231e; }}
  .neutral {{ background: #eee; color: #555; }}
  a {{ color: #2a5db0; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
  <h1>Run: {run_id}</h1>
  <div class="meta">{n_bugs} bugs &middot; {n_fixed} fixed &middot; {n_not_fixed} not fixed &middot; {n_withheld} withheld &middot; {n_unsupported} unsupported &middot; {n_error} errored &middot; mean attempts (successes): {mean_attempts} &middot; confidence-check precision: {precision}</div>
  <table>
    <tr><th>Bug</th><th>Status</th><th>Trail</th></tr>
{rows}
  </table>
</body>
</html>
"""

_ROW_TEMPLATE = '    <tr><td>{bug_id}</td><td><span class="status {status_class}">{status_label}</span></td><td><a href="{trail_href}">view</a></td></tr>'


def render_dashboard(summary: RunSummary, out_path: str) -> None:
    rows = "\n".join(
        _ROW_TEMPLATE.format(
            bug_id=html.escape(bug_id),
            status_class=_STATUS_CLASS.get(status, "neutral"),
            status_label=_STATUS_LABEL.get(status, status),
            trail_href=f"{bug_id}-trail.html",
        )
        for bug_id, status in summary.bug_results.items()
    )
    counts = {s: 0 for s in ("fixed", "not_fixed", "withheld", "unsupported", "error")}
    for status in summary.bug_results.values():
        counts[status] = counts.get(status, 0) + 1

    precision = "n/a (no commits pushed)" if summary.confidence_precision is None else f"{summary.confidence_precision:.0%}"

    html_out = _DASHBOARD_TEMPLATE.format(
        run_id=html.escape(summary.run_id),
        n_bugs=len(summary.bug_results),
        n_fixed=counts["fixed"],
        n_not_fixed=counts["not_fixed"],
        n_withheld=counts["withheld"],
        n_unsupported=counts["unsupported"],
        n_error=counts["error"],
        mean_attempts=round(summary.mean_attempts_on_success, 2),
        precision=precision,
        rows=rows,
    )
    with open(out_path, "w") as f:
        f.write(html_out)


_TRAIL_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Trail: {bug_id}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2rem; color: #222; max-width: 900px; }}
  h1 {{ font-size: 1.3rem; }}
  h2 {{ font-size: 1rem; margin-top: 2rem; border-top: 1px solid #e0e0e0; padding-top: 1rem; }}
  pre {{ background: #f6f6f6; padding: 0.75rem; overflow-x: auto; font-size: 0.85rem; }}
  .field {{ margin: 0.25rem 0; }}
  .label {{ color: #666; }}
  a {{ color: #2a5db0; }}
</style>
</head>
<body>
  <p><a href="eval-dashboard.html">&larr; back to dashboard</a></p>
  <h1>{bug_id}</h1>
{attempts}
</body>
</html>
"""

_ATTEMPT_TEMPLATE = """  <h2>Attempt {n}</h2>
  <div class="field"><span class="label">error_class:</span> {error_class}</div>
  <div class="field"><span class="label">files flagged by Analyzer:</span> {paths}</div>
  <div class="field"><span class="label">Coder touched:</span> {touched}</div>
  <pre>{diff}</pre>
  <div class="field"><span class="label">Confidence:</span> {decision} (score {score}) — {reason}</div>
  <div class="field"><span class="label">Reviewer:</span> {review_summary}</div>
"""


def render_bug_trail(thread: Thread, out_path: str) -> None:
    parts = []
    for attempt in thread.attempts:
        if attempt.review is None:
            review_summary = "not reviewed (commit withheld)"
        elif attempt.review.passed:
            review_summary = "passed — functionally equivalent to the verified fix"
        else:
            review_summary = f"failed — {html.escape(attempt.review.symptom or '')} ({html.escape(attempt.review.attempt_delta or '')})"

        parts.append(
            _ATTEMPT_TEMPLATE.format(
                n=attempt.attempt_number,
                error_class=html.escape(attempt.structured_error.error_class),
                paths=html.escape(", ".join(attempt.file_list.paths)),
                touched=html.escape(", ".join(attempt.patch.touched_paths) or "(no change)"),
                diff=html.escape(attempt.patch.unified_diff or "(empty diff)"),
                decision=attempt.confidence.decision.value,
                score=attempt.confidence.score,
                reason=html.escape(attempt.confidence.reason),
                review_summary=review_summary,
            )
        )

    html_out = _TRAIL_TEMPLATE.format(bug_id=html.escape(thread.bug_id), attempts="\n".join(parts) or "  <p>No attempts recorded.</p>")
    with open(out_path, "w") as f:
        f.write(html_out)
