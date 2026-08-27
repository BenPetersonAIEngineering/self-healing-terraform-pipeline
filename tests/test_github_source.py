import pytest

from healer.sourcing import github_source


def test_parse_pr_url():
    owner, repo, number = github_source.parse_pr_url("https://github.com/hashicorp/terraform-provider-aws/pull/31022")
    assert (owner, repo, number) == ("hashicorp", "terraform-provider-aws", 31022)


def test_parse_pr_url_rejects_non_pr_url():
    with pytest.raises(ValueError):
        github_source.parse_pr_url("https://github.com/hashicorp/terraform-provider-aws/issues/31022")


def test_build_case_writes_corpus_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(github_source, "fetch_pr_meta", lambda owner, repo, n: {"base": {"sha": "abc123"}})
    monkeypatch.setattr(
        github_source,
        "fetch_pr_files",
        lambda owner, repo, n: [{"filename": "main.tf"}, {"filename": "README.md"}],
    )
    monkeypatch.setattr(github_source, "fetch_pr_diff", lambda owner, repo, n: "--- a/main.tf\n+++ b/main.tf\n")
    monkeypatch.setattr(
        github_source,
        "fetch_file_at_ref",
        lambda owner, repo, path, ref: 'resource "aws_instance" "web" {\n  instance_type = "t2.micrio"\n}\n',
    )

    bug_dir = github_source.build_case(
        bug_id="bug-002",
        pr_url="https://github.com/example/repo/pull/42",
        error_output="Error: InvalidParameterValue",
        out_root=tmp_path,
    )

    assert bug_dir == tmp_path / "bug-002"
    assert (bug_dir / "repo" / "main.tf").exists()
    assert not (bug_dir / "repo" / "README.md").exists(), "only .tf files should be pulled into repo/"
    assert (bug_dir / "eval" / "verified-fix.diff").read_text() == "--- a/main.tf\n+++ b/main.tf\n"

    import yaml

    case = yaml.safe_load((bug_dir / "case.yaml").read_text())
    assert case["error_output"] == "Error: InvalidParameterValue"
    assert "pull/42" in case["source"]


def test_build_case_rejects_pr_with_no_tf_files(tmp_path, monkeypatch):
    monkeypatch.setattr(github_source, "fetch_pr_meta", lambda owner, repo, n: {"base": {"sha": "abc123"}})
    monkeypatch.setattr(github_source, "fetch_pr_files", lambda owner, repo, n: [{"filename": "README.md"}])

    with pytest.raises(ValueError, match="no .tf files"):
        github_source.build_case(
            bug_id="bug-003",
            pr_url="https://github.com/example/repo/pull/7",
            error_output="whatever",
            out_root=tmp_path,
        )
