"""CLI entrypoint for the automatic self-heal trigger: runs the live
pipeline against one specific PR, non-interactively. Invoked by
.github/workflows/self-heal.yml on a workflow_run failure — see
02-architecture.md's 2026-08-27 amendment. Not for manual use; the
manual/interactive path is healer-live-watch (read-only) plus a real
run_live() call, same as slices 8-12 were verified with.
"""
import argparse
import sys
from pathlib import Path

from healer.live import _github, live_orchestrator, live_watcher


def _build_case_for_pr(owner: str, repo: str, pr_number: int) -> live_watcher.LiveCase | None:
    pr = _github.get_json(f"/repos/{owner}/{repo}/pulls/{pr_number}")
    return live_watcher.build_live_case(owner, repo, pr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="healer-run-pr", description="Run the live self-healer against one PR")
    parser.add_argument("owner")
    parser.add_argument("repo")
    parser.add_argument("pr_number", type=int)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allow-push", action="store_true")
    args = parser.parse_args(argv)

    case = _build_case_for_pr(args.owner, args.repo, args.pr_number)
    if case is None:
        print(f"PR #{args.pr_number}: no failing run found for its current head — nothing to do")
        return 0

    print(f"PR #{args.pr_number} ({case.branch} @ {case.head_sha[:8]}): running live pipeline (allow_push={args.allow_push})")
    workdir_root = Path("runs") / "live" / "workdir-root"
    thread, push_result = live_orchestrator.run_live(
        case, run_id=args.run_id, workdir_root=workdir_root, allow_push=args.allow_push
    )
    print(f"PR #{args.pr_number}: {len(thread.attempts)} attempt(s) recorded")
    return 0


def main_entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
