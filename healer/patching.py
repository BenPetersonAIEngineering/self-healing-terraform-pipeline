"""Shared unified-diff application, used by both localstack.py (eval mode's
scratch-clone apply) and live/git_ops.py (live mode's real git checkout).
"""
import subprocess
from pathlib import Path


def apply_unified_diff(target_dir: Path, unified_diff: str) -> None:
    if not unified_diff.strip():
        return
    diff_file = target_dir / ".healer-fix.diff"
    diff_file.write_text(unified_diff)
    subprocess.run(["patch", "-p1", "-i", str(diff_file)], cwd=target_dir, check=True, capture_output=True, text=True)
    diff_file.unlink()
