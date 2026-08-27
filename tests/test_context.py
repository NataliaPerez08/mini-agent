from mini_agent.context import (
    IGNORED,
    SYSTEM_PROMPT,
    build_repo_map,
    build_turn_prompt,
    git_status,
)


class TestBuildRepoMap:
    def test_lists_files(self, git_repo_with_files):
        repo_map = build_repo_map(git_repo_with_files)
        assert "src/app.py" in repo_map
        assert "src/utils.py" in repo_map
        assert "README.md" in repo_map

    def test_ignores_directories(self, git_repo_with_files):
        repo_map = build_repo_map(git_repo_with_files)
        assert ".venv" not in repo_map
        assert "ignored.py" not in repo_map

    def test_output_is_sorted(self, git_repo_with_files):
        repo_map = build_repo_map(git_repo_with_files)
        lines = repo_map.splitlines()
        assert lines == sorted(lines)

    def test_empty_repo(self, git_repo):
        repo_map = build_repo_map(git_repo)
        assert repo_map == ""

    def test_max_files_limit(self, git_repo):
        for i in range(10):
            (git_repo / f"f{i}.py").write_text("x\n")
        repo_map = build_repo_map(git_repo, max_files=5)
        assert len(repo_map.splitlines()) == 5

    def test_ignored_set_contains_common_dirs(self):
        for d in [".git", ".venv", "node_modules", "build", "dist"]:
            assert d in IGNORED


class TestGitStatus:
    def test_clean_repo(self, git_repo_with_files):
        status = git_status(git_repo_with_files)
        assert status == "<clean>"

    def test_dirty_repo(self, git_repo_with_files):
        (git_repo_with_files / "new.py").write_text("x\n")
        status = git_status(git_repo_with_files)
        assert "new.py" in status
        assert status != "<clean>"


class TestSystemPrompt:
    def test_is_non_empty(self):
        assert len(SYSTEM_PROMPT) > 50

    def test_mentions_all_actions(self):
        for action in ["read_file", "search_code", "apply_patch", "run_tests", "finish"]:
            assert action in SYSTEM_PROMPT

    def test_mentions_json(self):
        assert "JSON" in SYSTEM_PROMPT.upper()


class TestBuildTurnPrompt:
    def test_contains_all_sections(self):
        prompt = build_turn_prompt(
            task="Fix the bug",
            repo_map="src/app.py",
            memory=["iter 1: read_file"],
            tool_result="OK",
            status="<clean>",
        )
        assert "Fix the bug" in prompt
        assert "src/app.py" in prompt
        assert "iter 1: read_file" in prompt
        assert "OK" in prompt
        assert "<clean>" in prompt

    def test_empty_memory_shows_none(self):
        prompt = build_turn_prompt(
            task="T",
            repo_map="",
            memory=[],
            tool_result="",
            status="<clean>",
        )
        assert "<none>" in prompt

    def test_memory_truncated_to_last_8(self):
        mem = [f"iter {i}" for i in range(20)]
        prompt = build_turn_prompt(
            task="T",
            repo_map="",
            memory=mem,
            tool_result="",
            status="<clean>",
        )
        assert "iter 19" in prompt
        assert "iter 0" not in prompt
        assert "iter 12" in prompt
        assert "iter 11" not in prompt
        assert "iter 12" in prompt
