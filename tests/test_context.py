from mini_agent.context import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPTS,
    build_initial_context,
    build_state_prompt,
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

    def test_is_the_shared_base_for_all_state_prompts(self):
        for prompt in SYSTEM_PROMPTS.values():
            assert prompt.startswith(SYSTEM_PROMPT)

    def test_all_actions_advertised_across_state_prompts(self):
        all_prompts = "\n".join(SYSTEM_PROMPTS.values())
        for action in [
            "read_file",
            "read_symbol",
            "search_code",
            "apply_patch",
            "plan",
            "finish",
        ]:
            assert action in all_prompts

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


class TestBuildStatePrompt:
    def test_tool_result_deduped_against_active_code(self):
        content = "def ApplyPolicy() { return 42 }"
        snippet = f"read_symbol svc.go::ApplyPolicy\n{content}"
        prompt = build_state_prompt(
            task="T",
            repo_map="svc.go",
            memory=[],
            tool_result=content,
            status="<clean>",
            state="read",
            active_code=[snippet],
        )
        assert prompt.count(content) == 1
        assert "ACTIVE CODE" in prompt
        assert "LAST TOOL RESULT" not in prompt

    def test_tool_result_shown_when_not_in_active_code(self):
        prompt = build_state_prompt(
            task="T",
            repo_map="",
            memory=[],
            tool_result="ERROR: file not found",
            status="<clean>",
            state="read",
            active_code=[],
        )
        assert "LAST TOOL RESULT" in prompt
        assert "ERROR: file not found" in prompt

    def test_tool_result_shown_in_repair_state_even_if_in_active_code(self):
        content = "patch applied"
        snippet = f"apply_patch\n{content}"
        prompt = build_state_prompt(
            task="T",
            repo_map="",
            memory=[],
            tool_result=content,
            status="<clean>",
            state="repair",
            plan={"goal": "fix", "files": []},
            test_failure="test failed",
            active_code=[snippet],
        )
        assert "LAST TOOL RESULT" in prompt


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
