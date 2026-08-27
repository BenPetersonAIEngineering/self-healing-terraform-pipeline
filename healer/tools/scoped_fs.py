"""Tool-layer file access enforcement.

Every agent that touches files gets its own ScopedFileTool, constructed with a
resolved allowlist. This is the only mechanism that can grant file access —
agents are never told what they can or can't touch via prompt instructions.
"""
import os
from pathlib import Path


class PathNotAllowed(Exception):
    pass


class ScopedFileTool:
    def __init__(self, allowed_roots: list[str]):
        if not allowed_roots:
            raise ValueError("ScopedFileTool requires at least one allowed root")
        self._roots = [os.path.realpath(root) for root in allowed_roots]

    @property
    def roots(self) -> list[str]:
        return list(self._roots)

    def _resolve(self, path: str) -> str:
        if not os.path.isabs(path):
            path = os.path.join(self._roots[0], path)
        resolved = os.path.realpath(path)
        for root in self._roots:
            if resolved == root or resolved.startswith(root + os.sep):
                return resolved
        raise PathNotAllowed(f"{path!r} (resolved: {resolved!r}) is outside this tool's allowed roots: {self._roots}")

    def read(self, path: str) -> str:
        resolved = self._resolve(path)
        return Path(resolved).read_text()

    def write(self, path: str, content: str) -> None:
        resolved = self._resolve(path)
        Path(resolved).parent.mkdir(parents=True, exist_ok=True)
        Path(resolved).write_text(content)

    def list_dir(self, path: str) -> list[str]:
        resolved = self._resolve(path)
        return sorted(os.listdir(resolved))
