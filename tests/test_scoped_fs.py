import os

import pytest

from healer.tools.scoped_fs import PathNotAllowed, ScopedFileTool


def test_read_within_allowed_root(tmp_path):
    (tmp_path / "main.tf").write_text("hello")
    tool = ScopedFileTool(allowed_roots=[str(tmp_path)])
    assert tool.read(str(tmp_path / "main.tf")) == "hello"


def test_denies_path_outside_allowed_roots(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("verified fix")

    tool = ScopedFileTool(allowed_roots=[str(allowed)])
    with pytest.raises(PathNotAllowed):
        tool.read(str(outside / "secret.txt"))


def test_denies_symlink_escape(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("verified fix")
    os.symlink(outside / "secret.txt", allowed / "sneaky_link.txt")

    tool = ScopedFileTool(allowed_roots=[str(allowed)])
    with pytest.raises(PathNotAllowed):
        tool.read(str(allowed / "sneaky_link.txt"))


def test_write_creates_new_file_within_root(tmp_path):
    tool = ScopedFileTool(allowed_roots=[str(tmp_path)])
    tool.write(str(tmp_path / "new_resource.tf"), "resource {}")
    assert (tmp_path / "new_resource.tf").read_text() == "resource {}"


def test_coder_allowlist_is_exact_files_not_whole_repo(tmp_path):
    (tmp_path / "main.tf").write_text("a")
    (tmp_path / "other.tf").write_text("b")

    tool = ScopedFileTool(allowed_roots=[str(tmp_path / "main.tf")])
    assert tool.read(str(tmp_path / "main.tf")) == "a"
    with pytest.raises(PathNotAllowed):
        tool.read(str(tmp_path / "other.tf"))
