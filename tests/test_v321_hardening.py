"""Regression tests for v3.2.1 (CURRENT_ISSUES 8.3).

Insertion-only clusters (pure + lines, no removals) inside a hunk that
git apply rejects atomically (e.g. a hallucinated context line breaks
the whole hunk) are anchored on their preceding context line when it
matches exactly one whitespace-insensitive file position. Previously
one insertion cluster aborted the entire anchor fallback.

git apply -C1 already rescues most pure-insertion hunks via context
reduction; the anchor's residual value is mixed-cluster hunks and
clusters whose neighbors are broken.
"""

from pathlib import Path

from mini_agent.tools import RepoTools


def _repo(git_repo: Path) -> Path:
    (git_repo / "src").mkdir()
    (git_repo / "src" / "app.py").write_text(
        "def ApplyPolicy(ports):\n"
        "    total = 0\n"
        "    return total\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
    )
    return git_repo


# git rejects this hunk atomically (hallucinated "returnn total"
# context); the removal cluster and the insertion cluster each anchor.
MIXED_HUNK = (
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,7 +1,8 @@\n"
    " def ApplyPolicy(ports):\n"
    "-    total = 0\n"
    "+    total = len(ports)\n"
    "     returnn total\n"
    " \n"
    " def helper():\n"
    "+    pass\n"
    "     return 1\n"
)


class TestInsertionClusterAnchor:
    def test_mixed_hunk_git_rejects(self, git_repo: Path) -> None:
        # sanity: git apply alone refuses the whole hunk
        import subprocess

        repo = _repo(git_repo)
        proc = subprocess.run(
            [
                "git", "apply", "--whitespace=nowarn",
                "--ignore-whitespace", "-C1", "--recount", "-",
            ],
            cwd=repo,
            input=MIXED_HUNK,
            text=True,
            capture_output=True,
        )
        assert proc.returncode != 0

    def test_mixed_clusters_apply_via_anchor(self, git_repo: Path) -> None:
        repo = _repo(git_repo)
        out = RepoTools(repo, "true").apply_patch(MIXED_HUNK)
        assert out.startswith("PATCH APPLIED"), out
        content = (repo / "src" / "app.py").read_text()
        assert "total = len(ports)" in content
        lines = content.splitlines()
        helper_idx = lines.index("def helper():")
        assert lines[helper_idx + 1] == "    pass"
        assert lines[helper_idx + 2] == "    return 1"

    def test_no_context_insertion_fails_loudly(self, git_repo: Path) -> None:
        repo = _repo(git_repo)
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+import os\n"
            " hallucinated_one = 1\n"
            " hallucinated_two = 2\n"
        )
        out = RepoTools(repo, "true").apply_patch(patch)
        assert out.startswith("PATCH FAILED"), out

    def test_unique_leading_anchor_unit(self, git_repo: Path) -> None:
        repo = _repo(git_repo)
        tools = RepoTools(repo, "true")
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,4 +1,5 @@\n"
            " def ApplyPolicy(ports):\n"
            "     total = 0\n"
            "+    if not ports:\n"
            "+        return 'error'\n"
            "     return total\n"
        )
        assert tools._anchor_apply(patch) is True
        lines = (repo / "src" / "app.py").read_text().splitlines()
        assert lines[2] == "    if not ports:"
        assert lines[3] == "        return 'error'"
        assert lines[4] == "    return total"

    def test_whitespace_flexible_leading_unit(self, git_repo: Path) -> None:
        repo = _repo(git_repo)
        tools = RepoTools(repo, "true")
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,4 +1,5 @@\n"
            " def ApplyPolicy(ports):\n"
            "   total = 0\n"
            "+    if not ports:\n"
            "+        return 'error'\n"
            "     return total\n"
        )
        assert tools._anchor_apply(patch) is True
        assert "if not ports:" in (repo / "src" / "app.py").read_text()

    def test_ambiguous_leading_refused_unit(self, git_repo: Path) -> None:
        repo = _repo(git_repo)
        (repo / "src" / "app.py").write_text(
            "def ApplyPolicy(ports):\n"
            "    total = 0\n"
            "    total = 0\n"
            "    return total\n"
        )
        tools = RepoTools(repo, "true")
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,4 +1,5 @@\n"
            " def ApplyPolicy(ports):\n"
            "     total = 0\n"
            "+    x = 1\n"
            "     return total\n"
        )
        assert tools._anchor_apply(patch) is False
