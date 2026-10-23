from mini_agent.scanner import scan_repository
from mini_agent.state import (
    ALLOWED_ACTIONS,
    MAX_CODE_CONTEXT_CHARS,
    MAX_READ_ACTIONS,
    MAX_REPAIR_CYCLES,
    AgentState,
    Progress,
    TaskType,
    WorkingContext,
    choose_inspection_files,
    classify_task,
    validate_action,
    validate_transition,
)


class TestAgentState:
    def test_all_states_defined(self):
        expected = {
            "discover",
            "read",
            "plan",
            "edit",
            "verify",
            "repair",
            "done",
            "failed",
        }
        actual = {s.value for s in AgentState}
        assert actual == expected

    def test_state_is_str_enum(self):
        assert AgentState.DONE == "done"
        assert isinstance(AgentState.DONE.value, str)


class TestTaskType:
    def test_all_types_defined(self):
        expected = {"inspect", "locate", "modify", "debug"}
        actual = {t.value for t in TaskType}
        assert actual == expected


class TestClassifyTask:
    def test_modify_keywords_es(self):
        for task in [
            "corrige el bug",
            "modifica el archivo",
            "cambia el valor",
            "implementa la funcion",
        ]:
            assert classify_task(task) == TaskType.MODIFY

    def test_modify_keywords_en(self):
        for task in [
            "fix the test",
            "change the code",
            "implement the feature",
            "add a function",
            "remove the line",
        ]:
            assert classify_task(task) == TaskType.MODIFY

    def test_debug_keywords(self):
        for task in [
            "debug the issue",
            "there is a bug",
            "an error occurs",
            "exception thrown",
            "failing test",
        ]:
            assert classify_task(task) == TaskType.DEBUG

    def test_locate_keywords(self):
        for task in [
            "where is login",
            "find the function",
            "locate the class",
            "donde esta el archivo",
        ]:
            assert classify_task(task) == TaskType.LOCATE

    def test_inspect_default(self):
        for task in [
            "revisa que hay",
            "look at the files",
            "show me the repo",
            "Do nothing.",
        ]:
            assert classify_task(task) == TaskType.INSPECT

    def test_modify_takes_priority_over_debug(self):
        assert classify_task("fix the bug") == TaskType.MODIFY

    def test_case_insensitive(self):
        assert classify_task("FIX THE CODE") == TaskType.MODIFY
        assert classify_task("Debug The Error") == TaskType.DEBUG


class TestAllowedTransitions:
    def test_discover_to_read(self):
        assert validate_transition(AgentState.DISCOVER, AgentState.READ)

    def test_read_to_plan(self):
        assert validate_transition(AgentState.READ, AgentState.PLAN)

    def test_read_to_edit(self):
        assert validate_transition(AgentState.READ, AgentState.EDIT)

    def test_read_to_done(self):
        assert validate_transition(AgentState.READ, AgentState.DONE)

    def test_plan_to_edit(self):
        assert validate_transition(AgentState.PLAN, AgentState.EDIT)

    def test_edit_to_verify(self):
        assert validate_transition(AgentState.EDIT, AgentState.VERIFY)

    def test_edit_to_read(self):
        assert validate_transition(AgentState.EDIT, AgentState.READ)

    def test_verify_to_done(self):
        assert validate_transition(AgentState.VERIFY, AgentState.DONE)

    def test_verify_to_repair(self):
        assert validate_transition(AgentState.VERIFY, AgentState.REPAIR)

    def test_repair_to_verify(self):
        assert validate_transition(AgentState.REPAIR, AgentState.VERIFY)

    def test_repair_to_edit(self):
        assert validate_transition(AgentState.REPAIR, AgentState.EDIT)

    def test_repair_to_read(self):
        assert validate_transition(AgentState.REPAIR, AgentState.READ)

    def test_done_no_transitions(self):
        for target in AgentState:
            assert not validate_transition(AgentState.DONE, target)

    def test_failed_no_transitions(self):
        for target in AgentState:
            assert not validate_transition(AgentState.FAILED, target)

    def test_invalid_transition_rejected(self):
        assert not validate_transition(AgentState.EDIT, AgentState.DONE)
        assert not validate_transition(AgentState.VERIFY, AgentState.EDIT)
        assert not validate_transition(AgentState.PLAN, AgentState.DONE)

    def test_read_self_transition(self):
        assert validate_transition(AgentState.READ, AgentState.READ)

    def test_edit_self_transition(self):
        assert validate_transition(AgentState.EDIT, AgentState.EDIT)


class TestAllowedActions:
    def test_read_actions(self):
        actions = ALLOWED_ACTIONS[AgentState.READ]
        assert "read_file" in actions
        assert "search_code" in actions
        assert "finish" in actions
        assert "plan" in actions
        assert "apply_patch" not in actions

    def test_edit_apply_patch_and_finish(self):
        assert ALLOWED_ACTIONS[AgentState.EDIT] == {"apply_patch", "finish"}

    def test_plan_only_plan(self):
        assert ALLOWED_ACTIONS[AgentState.PLAN] == {"plan"}

    def test_verify_no_actions(self):
        assert ALLOWED_ACTIONS[AgentState.VERIFY] == set()

    def test_repair_actions(self):
        actions = ALLOWED_ACTIONS[AgentState.REPAIR]
        assert "apply_patch" in actions
        assert "read_file" in actions
        assert "finish" in actions

    def test_done_no_actions(self):
        assert ALLOWED_ACTIONS[AgentState.DONE] == set()

    def test_failed_no_actions(self):
        assert ALLOWED_ACTIONS[AgentState.FAILED] == set()


class TestValidateAction:
    def test_read_file_in_read(self):
        assert validate_action(AgentState.READ, "read_file")

    def test_apply_patch_not_in_read(self):
        assert not validate_action(AgentState.READ, "apply_patch")

    def test_apply_patch_in_edit(self):
        assert validate_action(AgentState.EDIT, "apply_patch")

    def test_read_file_not_in_edit(self):
        assert not validate_action(AgentState.EDIT, "read_file")

    def test_finish_in_read(self):
        assert validate_action(AgentState.READ, "finish")

    def test_finish_in_edit_escape_hatch(self):
        assert validate_action(AgentState.EDIT, "finish")

    def test_plan_in_read(self):
        assert validate_action(AgentState.READ, "plan")

    def test_plan_in_plan(self):
        assert validate_action(AgentState.PLAN, "plan")

    def test_no_actions_in_verify(self):
        assert not validate_action(AgentState.VERIFY, "read_file")
        assert not validate_action(AgentState.VERIFY, "apply_patch")
        assert not validate_action(AgentState.VERIFY, "finish")


class TestProgress:
    def test_record_read_new(self):
        p = Progress()
        assert p.record_read("src/app.py") is True
        assert "src/app.py" in p.files_read

    def test_record_read_duplicate(self):
        p = Progress()
        p.record_read("src/app.py")
        assert p.record_read("src/app.py") is False

    def test_record_search_new(self):
        p = Progress()
        assert p.record_search("login") is True

    def test_record_search_duplicate(self):
        p = Progress()
        p.record_search("login")
        assert p.record_search("login") is False

    def test_record_search_case_insensitive(self):
        p = Progress()
        p.record_search("Login")
        assert p.record_search("login") is False

    def test_record_patch(self):
        p = Progress()
        p.record_patch()
        assert p.patches_applied == 1

    def test_record_test(self):
        p = Progress()
        p.record_test()
        assert p.tests_run == 1

    def test_add_auto_read(self):
        p = Progress()
        p.add_auto_read("README.md")
        assert "README.md" in p.files_read
        assert p.record_read("README.md") is False

    def test_read_actions_counter(self):
        p = Progress()
        p.record_read("a.py")
        p.record_read("b.py")
        assert p.read_actions == 0


class TestChooseInspectionFiles:
    def test_returns_readme_first(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        candidates = choose_inspection_files(scan)
        assert candidates[0] == "README.md"

    def test_max_two_files(self, git_repo):
        for name in ["README.md", "pyproject.toml", "package.json"]:
            (git_repo / name).write_text("x\n")
        scan = scan_repository(git_repo)
        candidates = choose_inspection_files(scan)
        assert len(candidates) <= 2

    def test_empty_repo(self, git_repo):
        scan = scan_repository(git_repo)
        candidates = choose_inspection_files(scan)
        assert candidates == []


class TestTaskBudgets:
    def test_inspect_hard_limit_less_than_modify(self):
        from mini_agent.state import TASK_BUDGETS

        _, inspect_hard = TASK_BUDGETS[TaskType.INSPECT]
        _, modify_hard = TASK_BUDGETS[TaskType.MODIFY]
        assert inspect_hard < modify_hard

    def test_inspect_limits(self):
        from mini_agent.state import TASK_BUDGETS

        soft, hard = TASK_BUDGETS[TaskType.INSPECT]
        assert soft == 2
        assert hard == 4

    def test_modify_limits(self):
        from mini_agent.state import TASK_BUDGETS

        soft, hard = TASK_BUDGETS[TaskType.MODIFY]
        assert soft == 5
        assert hard == 8

    def test_debug_limits(self):
        from mini_agent.state import TASK_BUDGETS

        soft, hard = TASK_BUDGETS[TaskType.DEBUG]
        assert soft == 6
        assert hard == 10

    def test_locate_limits(self):
        from mini_agent.state import TASK_BUDGETS

        soft, hard = TASK_BUDGETS[TaskType.LOCATE]
        assert soft == 3
        assert hard == 5


class TestMaxReadActions:
    def test_inspect_max_2(self):
        assert MAX_READ_ACTIONS[TaskType.INSPECT] == 2

    def test_modify_max_3(self):
        assert MAX_READ_ACTIONS[TaskType.MODIFY] == 3

    def test_inspect_less_than_debug(self):
        assert MAX_READ_ACTIONS[TaskType.INSPECT] < MAX_READ_ACTIONS[TaskType.DEBUG]


class TestMaxRepairCycles:
    def test_max_repair_cycles_is_2(self):
        assert MAX_REPAIR_CYCLES == 2


class TestMaxCodeContextChars:
    def test_inspect_budget(self):
        assert MAX_CODE_CONTEXT_CHARS[TaskType.INSPECT] == 4000

    def test_modify_budget(self):
        assert MAX_CODE_CONTEXT_CHARS[TaskType.MODIFY] == 8000

    def test_debug_budget(self):
        assert MAX_CODE_CONTEXT_CHARS[TaskType.DEBUG] == 10000

    def test_inspect_less_than_modify(self):
        assert (
            MAX_CODE_CONTEXT_CHARS[TaskType.INSPECT]
            < MAX_CODE_CONTEXT_CHARS[TaskType.MODIFY]
        )


class TestReadSymbolAllowed:
    def test_read_symbol_in_discover(self):
        assert "read_symbol" in ALLOWED_ACTIONS[AgentState.DISCOVER]

    def test_read_symbol_in_read(self):
        assert "read_symbol" in ALLOWED_ACTIONS[AgentState.READ]

    def test_read_symbol_in_repair(self):
        assert "read_symbol" in ALLOWED_ACTIONS[AgentState.REPAIR]

    def test_read_symbol_not_in_edit(self):
        assert "read_symbol" not in ALLOWED_ACTIONS[AgentState.EDIT]

    def test_read_symbol_not_in_plan(self):
        assert "read_symbol" not in ALLOWED_ACTIONS[AgentState.PLAN]


class TestProgressSymbolRead:
    def test_record_symbol_read_new(self):
        p = Progress()
        assert p.record_symbol_read("a.go", "Foo") is True
        assert "a.go::Foo" in p.symbols_read

    def test_record_symbol_read_duplicate(self):
        p = Progress()
        p.record_symbol_read("a.go", "Foo")
        assert p.record_symbol_read("a.go", "Foo") is False

    def test_record_symbol_read_different_symbol(self):
        p = Progress()
        p.record_symbol_read("a.go", "Foo")
        assert p.record_symbol_read("a.go", "Bar") is True

    def test_record_symbol_read_adds_to_files_read(self):
        p = Progress()
        p.record_symbol_read("a.go", "Foo")
        assert "a.go" in p.files_read


class TestWorkingContext:
    def test_add_snippet(self):
        ctx = WorkingContext()
        ctx.add_snippet("read_file a.go", "content here", budget=8000)
        assert "read_file a.go" in ctx.active_snippets
        assert ctx.active_code_chars == len("content here")

    def test_add_fact(self):
        ctx = WorkingContext()
        ctx.add_fact("some fact")
        assert "some fact" in ctx.facts

    def test_facts_limited(self):
        ctx = WorkingContext()
        for i in range(20):
            ctx.add_fact(f"fact {i}")
        assert len(ctx.facts) == 12
        assert "fact 19" in ctx.facts
        assert "fact 0" not in ctx.facts

    def test_pruning(self):
        ctx = WorkingContext()
        ctx.add_snippet("s1", "x" * 500, budget=800)
        assert "s1" in ctx.active_snippets
        ctx.add_snippet("s2", "y" * 500, budget=800)
        assert "s1" not in ctx.active_snippets
        assert "s2" in ctx.active_snippets
        assert any("pruned s1" in f for f in ctx.facts)

    def test_no_prune_under_budget(self):
        ctx = WorkingContext()
        ctx.add_snippet("s1", "x" * 100, budget=8000)
        ctx.add_snippet("s2", "y" * 100, budget=8000)
        assert "s1" in ctx.active_snippets
        assert "s2" in ctx.active_snippets

    def test_memory_chars(self):
        ctx = WorkingContext()
        ctx.add_snippet("s1", "abc", budget=8000)
        ctx.add_fact("fact123")
        assert ctx.memory_chars == 7

    def test_to_memory_lines(self):
        ctx = WorkingContext()
        ctx.add_snippet("read_file a.go", "content", budget=8000)
        ctx.add_fact("some fact")
        lines = ctx.to_memory_lines()
        assert len(lines) == 1
        assert lines[0] == "some fact"

    def test_add_modified_file(self):
        ctx = WorkingContext()
        ctx.add_modified_file("a.go")
        ctx.add_modified_file("a.go")
        ctx.add_modified_file("b.go")
        assert ctx.modified_files == ["a.go", "b.go"]

    def test_add_test_failure(self):
        ctx = WorkingContext()
        ctx.add_test_failure("FAIL: something broke")
        assert len(ctx.test_failures) == 1
        assert "FAIL" in ctx.test_failures[0]

    def test_test_failures_limited(self):
        ctx = WorkingContext()
        for i in range(10):
            ctx.add_test_failure(f"failure {i}")
        assert len(ctx.test_failures) == 3

    def test_empty_context(self):
        ctx = WorkingContext()
        assert ctx.active_code_chars == 0
        assert ctx.memory_chars == 0
        assert ctx.to_memory_lines() == []
