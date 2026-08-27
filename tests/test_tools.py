import subprocess

import pytest

from mini_agent.tools import RepoTools


class TestSafePath:
    def test_normal_path(self, git_repo):
        tools = RepoTools(git_repo, "")
        p = tools._safe_path("src/app.py")
        assert p == (git_repo / "src" / "app.py").resolve()

    def test_path_traversal_rejected(self, git_repo):
        tools = RepoTools(git_repo, "")
        with pytest.raises(ValueError, match="escapes repository"):
            tools._safe_path("../../etc/passwd")

    def test_dot_returns_repo(self, git_repo):
        tools = RepoTools(git_repo, "")
        p = tools._safe_path(".")
        assert p == git_repo.resolve()

    def test_absolute_path_inside_repo(self, git_repo):
        tools = RepoTools(git_repo, "")
        p = tools._safe_path(str(git_repo / "src" / "app.py"))
        assert p == (git_repo / "src" / "app.py").resolve()


class TestReadFile:
    def test_read_full_file(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/app.py")
        assert "def hello" in result
        assert "return 'hello'" in result

    def test_line_numbers_prefixed(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/app.py")
        lines = result.splitlines()
        assert lines[0].lstrip().startswith("1:")

    def test_read_partial_range(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/utils.py", start=2, end=3)
        lines = result.splitlines()
        assert len(lines) == 2
        assert "PI" not in lines[0]

    def test_nonexistent_file(self, git_repo):
        tools = RepoTools(git_repo, "")
        result = tools.read_file("nope.py")
        assert result.startswith("ERROR:")

    def test_empty_file(self, git_repo):
        (git_repo / "empty.py").write_text("")
        tools = RepoTools(git_repo, "")
        result = tools.read_file("empty.py")
        assert result == "<empty>"

    def test_start_clamped_to_one(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/app.py", start=-5, end=1)
        lines = result.splitlines()
        assert len(lines) == 1

    def test_end_clamped_to_file_length(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/app.py", start=1, end=9999)
        lines = result.splitlines()
        assert len(lines) == 2


class TestSearchCode:
    def test_finds_match(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.search_code("def hello")
        assert "src/app.py" in result
        assert "def hello" in result

    def test_no_matches(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.search_code("xyzzy_nonexistent")
        assert result == "<no matches>"

    def test_search_with_path_filter(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.search_code("def", path="src")
        assert "src/app.py" in result
        assert "src/utils.py" in result

    def test_result_truncated(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.search_code("def")
        assert len(result) <= 12000


class TestApplyPatch:
    def test_successful_patch(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'world'\n"
        )
        result = tools.apply_patch(patch)
        assert result.startswith("PATCH APPLIED")
        assert "return 'world'" in result

    def test_failed_patch(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        patch = "garbage that is not a valid diff\n"
        result = tools.apply_patch(patch)
        assert result.startswith("PATCH FAILED")

    def test_patch_modifies_file(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'patched'\n"
        )
        tools.apply_patch(patch)
        content = (git_repo_with_files / "src" / "app.py").read_text()
        assert "patched" in content


class TestRunTests:
    def test_no_command_configured(self, git_repo):
        tools = RepoTools(git_repo, "")
        result = tools.run_tests()
        assert result == "NO TEST COMMAND CONFIGURED"

    def test_whitespace_only_command(self, git_repo):
        tools = RepoTools(git_repo, "   ")
        result = tools.run_tests()
        assert result == "NO TEST COMMAND CONFIGURED"

    def test_passing_test(self, git_repo):
        tools = RepoTools(git_repo, "true")
        result = tools.run_tests()
        assert "EXIT_CODE=0" in result

    def test_failing_test(self, git_repo):
        tools = RepoTools(git_repo, "false")
        result = tools.run_tests()
        assert "EXIT_CODE=1" in result

    def test_test_output_captured(self, git_repo):
        tools = RepoTools(git_repo, "echo hello_test")
        result = tools.run_tests()
        assert "hello_test" in result
        assert "EXIT_CODE=0" in result
