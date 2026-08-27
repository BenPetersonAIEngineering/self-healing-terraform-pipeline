import argparse
import multiprocessing
import sys
from pathlib import Path

from healer import corpus, orchestrator, report
from healer import thread as thread_module
from healer.models import RunSummary
from healer.orchestrator import UnsupportedBug
from healer.thread import Thread


def _summary_path(run_id: str) -> Path:
    return thread_module.RUNS_ROOT / run_id / "summary.json"


def _run_one_bug(run_id: str, bug_id: str) -> tuple[str, str]:
    """Runs one bug and returns (bug_id, status). Never raises: one bug's
    failure (a missing API key, a transient LLM error, anything) must not
    take down the rest of a --all run, and a Pool worker exception would
    otherwise propagate and abort every other in-flight bug too.
    UnsupportedBug is a known, declared gap (see corpus/<bug-id>/case.yaml's
    localstack_unsupported); anything else is reported as "error" so it's
    visible in the summary rather than silently dropped.
    """
    try:
        thread = orchestrator.run_bug(run_id, bug_id)
        return bug_id, orchestrator.bug_status(thread)
    except UnsupportedBug:
        return bug_id, "unsupported"
    except Exception as exc:
        print(f"bug={bug_id} raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return bug_id, "error"


def _recompute_summary(run_id: str, fallback_results: dict[str, str] | None = None) -> RunSummary:
    """Rebuilds the run summary from every thread.json under this run —
    the thread files are the source of truth, not an incrementally
    maintained counter, so re-running one bug can never leave the summary
    stale relative to the others. `fallback_results` fills in bugs that
    have no thread.json at all (e.g. a bug that raised before its first
    attempt was ever saved) using this run's actual worker outcomes,
    rather than silently omitting them from the summary.
    """
    run_dir = thread_module.RUNS_ROOT / run_id
    summary = RunSummary(run_id=run_id)
    fallback_results = fallback_results or {}

    attempts_on_success = []
    opens_total = 0
    opens_correct = 0

    # Iterate the whole corpus, not just runs/<run_id>/*: an unsupported
    # bug never gets a run directory at all (see UnsupportedBug), so
    # scanning run_dir alone would silently drop it from the summary.
    for bug_id in corpus.list_bug_ids():
        case = corpus.load_case(bug_id)
        if case.localstack_unsupported:
            summary.bug_results[bug_id] = "unsupported"
            continue

        thread_file = run_dir / bug_id / "thread.json"
        if not thread_file.exists():
            if bug_id in fallback_results:
                summary.bug_results[bug_id] = fallback_results[bug_id]
            continue
        thread = Thread.load(run_id, bug_id)
        status = orchestrator.bug_status(thread)
        summary.bug_results[bug_id] = status

        if status == "fixed":
            attempts_on_success.append(len(thread.attempts))
        if orchestrator.fix_was_committed(thread):
            opens_total += 1
            if status == "fixed":
                opens_correct += 1

    summary.mean_attempts_on_success = (
        sum(attempts_on_success) / len(attempts_on_success) if attempts_on_success else 0.0
    )
    summary.confidence_precision = (opens_correct / opens_total) if opens_total else None
    return summary


def _save_summary(summary: RunSummary) -> None:
    import json

    path = _summary_path(summary.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary.__dict__, indent=2))


def cmd_run(args: argparse.Namespace) -> int:
    if args.all:
        bug_ids = corpus.list_bug_ids()
        if not bug_ids:
            print("no bugs found in corpus/", file=sys.stderr)
            return 1
        with multiprocessing.Pool(processes=min(len(bug_ids), args.jobs)) as pool:
            results = pool.starmap(_run_one_bug, [(args.run_id, bug_id) for bug_id in bug_ids])
        for bug_id, status in results:
            print(f"bug={bug_id} status={status}")
        fallback_results = dict(results)
    else:
        if not args.bug_id:
            print("run requires a bug_id, or pass --all", file=sys.stderr)
            return 1
        bug_id, status = _run_one_bug(args.run_id, args.bug_id)
        print(f"bug={bug_id} status={status}")
        fallback_results = {bug_id: status}

    summary = _recompute_summary(args.run_id, fallback_results)
    _save_summary(summary)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    import json

    summary_data = json.loads(_summary_path(args.run_id).read_text())
    summary = RunSummary(**summary_data)

    run_dir = thread_module.RUNS_ROOT / args.run_id
    report.render_dashboard(summary, str(run_dir / "eval-dashboard.html"))
    for bug_id in summary.bug_results:
        thread = Thread.load(args.run_id, bug_id)
        report.render_bug_trail(thread, str(run_dir / f"{bug_id}-trail.html"))

    print(f"wrote {run_dir / 'eval-dashboard.html'}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="healer")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the pipeline against one bug (or --all)")
    run_p.add_argument("bug_id", nargs="?", help="corpus bug id, e.g. bug-001")
    run_p.add_argument("--all", action="store_true", help="run the full corpus, one process per bug")
    run_p.add_argument("--jobs", type=int, default=multiprocessing.cpu_count(), help="max parallel workers for --all")
    run_p.add_argument("--run-id", dest="run_id", required=True, help="run identifier, e.g. 2026-08-27-nightly")
    run_p.set_defaults(func=cmd_run)

    report_p = sub.add_parser("report", help="regenerate the dashboard + per-bug trails for a run")
    report_p.add_argument("run_id", help="run identifier")
    report_p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


def main_entry() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    main_entry()
