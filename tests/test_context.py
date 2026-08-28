from mini_agent.context import (
    SYSTEM_PROMPT,
    build_initial_context,
    build_turn_prompt,
    git_status,
)


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
        for action in [
            "read_file",
            "read_symbol",
            "search_code",
            "apply_patch",
            "run_tests",
            "finish",
        ]:
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


class TestBuildInitialContext:
    def test_contains_all_sections(self):
        ctx = build_initial_context(
            task="Fix the bug",
            repo_map="src/app.py\n  def main()",
        )
        assert "TASK" in ctx
        assert "Fix the bug" in ctx
        assert "REPOSITORY MAP" in ctx
        assert "src/app.py" in ctx
        assert "INSTRUCTIONS" in ctx

    def test_instructions_present(self):
        ctx = build_initial_context(task="T", repo_map="")
        assert "INSTRUCTIONS" in ctx
        assert "lecturas" in ctx

    def test_truncation_keeps_task(self):
        big_map = "x" * 50000
        ctx = build_initial_context(
            task="MY IMPORTANT TASK", repo_map=big_map, max_chars=500
        )
        assert "MY IMPORTANT TASK" in ctx
        assert "INSTRUCTIONS" in ctx
        assert len(ctx) <= 500 + 200

    def test_truncation_never_removes_task_or_instructions(self):
        big_map = "y" * 100000
        ctx = build_initial_context(task="KEEPME", repo_map=big_map, max_chars=1000)
        assert "KEEPME" in ctx
        assert "INSTRUCTIONS" in ctx
        assert "REPOSITORY MAP" in ctx

    def test_small_repo_map_not_truncated(self):
        ctx = build_initial_context(task="T", repo_map="short map", max_chars=12000)
        assert "short map" in ctx
