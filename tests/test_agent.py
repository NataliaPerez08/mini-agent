import json

from mini_agent.agent import AgentResult, CodingAgent
from tests.conftest import StubModel


class TestCodingAgentRun:
    def test_finish_immediately(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps({"action": "finish", "summary": "Nothing to do."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Do nothing.")
        assert isinstance(result, AgentResult)
        assert result.iterations == 1
        assert result.summary == "Nothing to do."
        assert result.final_state == "done"

    def test_read_then_finish(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps(
                    {"action": "read_file", "path": "src/app.py", "start": 1, "end": 10}
                ),
                json.dumps(
                    {"action": "finish", "summary": "Read the file, looks good."}
                ),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Read src/app.py and finish.")
        assert result.iterations == 2
        assert "good" in result.summary

    def test_search_then_finish(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps(
                    {"action": "search_code", "query": "def hello", "path": "."}
                ),
                json.dumps({"action": "finish", "summary": "Found it."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Search for hello function.")
        assert result.iterations == 2
        assert len(agent.memory) == 1

    def test_max_iterations_without_finish(self, git_repo_with_files):
        responses = [
            json.dumps({"action": "run_tests"}),
        ] * 15
        model = StubModel(responses)
        agent = CodingAgent(git_repo_with_files, model, "", max_iterations=5)
        result = agent.run("Loop forever.")
        assert result.iterations == 4
        assert result.final_state == "failed"

    def test_unsupported_action(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps({"action": "delete_file", "path": "x"}),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Try unsupported action.")
        assert result.iterations == 2
        assert "ERROR" in agent.memory[0]

    def test_loop_detection_repeats_error(self, git_repo_with_files):
        repeated = json.dumps({"action": "run_tests"})
        model = StubModel([repeated] * 10)
        agent = CodingAgent(git_repo_with_files, model, "", max_iterations=6)
        result = agent.run("Loop detection test.")
        assert result.iterations <= 4
        error_found = any("identical action repeated" in m for m in agent.memory)
        assert error_found

    def test_automatic_verification_after_patch(self, git_repo_with_files):
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'world'\n"
        )
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Change return value",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": patch}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "true")
        result = agent.run("Fix the return value in src/app.py.")
        assert result.iterations == 2
        assert result.final_state == "done"
        assert result.patches == 1
        assert result.tests_run == 1
        assert any("PATCH APPLIED" in m for m in agent.memory)
        assert any("EXIT_CODE=0" in m for m in agent.memory)

    def test_apply_patch_failed_no_verification(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Change return value",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": "invalid diff"}),
                json.dumps({"action": "finish", "summary": "Gave up."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "true")
        result = agent.run("Fix the return value in src/app.py.")
        assert result.iterations == 3
        assert any("PATCH FAILED" in m for m in agent.memory)
        assert not any("EXIT_CODE" in m for m in agent.memory)

    def test_memory_grows_per_iteration(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps({"action": "read_file", "path": "src/app.py"}),
                json.dumps({"action": "read_file", "path": "src/utils.py"}),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Read two files.")
        assert len(agent.memory) == 2
        assert "read_file" in agent.memory[0]
        assert "read_file" in agent.memory[1]

    def test_model_receives_system_prompt(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Task.")
        assert len(model.calls) == 1
        system, _ = model.calls[0]
        assert "JSON" in system.upper()

    def test_inspect_auto_reads_readme(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps({"action": "finish", "summary": "Saw the README."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Revisa que hay en la carpeta.")
        assert result.iterations == 1
        assert result.task_type == "inspect"
        assert "README.md" in agent.progress.files_read

    def test_inspect_finish_in_one_call(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps(
                    {"action": "finish", "summary": "It has a README and src files."}
                ),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Revisa que hay en la carpeta.")
        assert result.llm_calls == 1
        assert result.final_state == "done"

    def test_modify_goes_through_plan(self, git_repo_with_files):
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'world'\n"
        )
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Change return value",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": patch}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "true")
        result = agent.run("Fix the return value in src/app.py.")
        assert result.task_type == "modify"
        assert result.llm_calls == 2
        assert result.final_state == "done"

    def test_duplicate_read_rejected(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps({"action": "read_file", "path": "src/app.py"}),
                json.dumps({"action": "read_file", "path": "src/app.py"}),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Read the file twice.")
        assert result.iterations == 3
        assert any("already read" in m for m in agent.memory)

    def test_duplicate_search_rejected(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps(
                    {"action": "search_code", "query": "def hello", "path": "."}
                ),
                json.dumps(
                    {"action": "search_code", "query": "def hello", "path": "."}
                ),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Search twice.")
        assert result.iterations == 3
        assert any("already performed" in m for m in agent.memory)

    def test_action_not_allowed_in_state(self, git_repo_with_files):
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'world'\n"
        )
        model = StubModel(
            [
                json.dumps(
                    {"action": "plan", "files": ["src/app.py"], "summary": "Change it"}
                ),
                json.dumps({"action": "search_code", "query": "hello", "path": "."}),
                json.dumps({"action": "apply_patch", "patch": patch}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "true")
        result = agent.run("Fix the return value in src/app.py.")
        assert result.iterations == 3
        assert any("not allowed in state" in m for m in agent.memory)

    def test_inspect_hard_limit_less_than_modify(self, git_repo_with_files):
        inspect_repeated = json.dumps({"action": "run_tests"})
        model = StubModel([inspect_repeated] * 20)
        agent = CodingAgent(git_repo_with_files, model, "", max_iterations=12)
        inspect_result = agent.run("Look around.")
        assert inspect_result.iterations <= 4

        model2 = StubModel([inspect_repeated] * 20)
        agent2 = CodingAgent(git_repo_with_files, model2, "", max_iterations=12)
        modify_result = agent2.run("Fix the return value in src/app.py.")
        assert modify_result.iterations <= 8
        assert modify_result.iterations > inspect_result.iterations

    def test_repair_cycles_limited(self, git_repo_with_files):
        patch1 = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'world'\n"
        )
        patch2 = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def hello():\n"
            "     return 'world'\n"
            "+    pass\n"
        )
        patch3 = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def hello():\n"
            "     return 'world'\n"
            "     pass\n"
            "+    return None\n"
        )
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Change return value",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": patch1}),
                json.dumps({"action": "apply_patch", "patch": patch2}),
                json.dumps({"action": "apply_patch", "patch": patch3}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "false")
        result = agent.run("Fix the return value in src/app.py.")
        assert result.final_state == "failed"
        assert result.tests_run == 3


GO_SOURCE = """package firewall

type Service struct {
\tName string
}

func NewService() *Service {
\treturn &Service{}
}

func ApplyPolicy() error {
\treturn nil
}
"""


class TestReadSymbolAction:
    def test_read_symbol_then_finish(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "read_symbol",
                        "path": "fw/service.go",
                        "symbol": "ApplyPolicy",
                    }
                ),
                json.dumps({"action": "finish", "summary": "Read ApplyPolicy."}),
            ]
        )
        agent = CodingAgent(git_repo, model, "")
        result = agent.run("Revisa ApplyPolicy en el firewall.")
        assert result.iterations == 2
        assert "ApplyPolicy" in agent.last_tool_result

    def test_read_symbol_duplicate_rejected(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "read_symbol",
                        "path": "fw/service.go",
                        "symbol": "ApplyPolicy",
                    }
                ),
                json.dumps(
                    {
                        "action": "read_symbol",
                        "path": "fw/service.go",
                        "symbol": "ApplyPolicy",
                    }
                ),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo, model, "")
        result = agent.run("Revisa ApplyPolicy dos veces.")
        assert result.iterations == 3
        assert any("already read" in m for m in agent.memory)

    def test_read_symbol_not_found_continues(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "read_symbol",
                        "path": "fw/service.go",
                        "symbol": "NonExistent",
                    }
                ),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo, model, "")
        result = agent.run("Revisa algo que no existe.")
        assert result.iterations == 2
        assert "ERROR" in agent.last_tool_result

    def test_symbol_candidates_in_prompt(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        model = StubModel([json.dumps({"action": "finish", "summary": "Done."})])
        agent = CodingAgent(git_repo, model, "")
        agent.run("Corrige ApplyPolicy del firewall.")
        assert len(model.calls) == 1
        _, user = model.calls[0]
        assert "RELEVANT SYMBOL CANDIDATES" in user
        assert "ApplyPolicy" in user

    def test_no_candidates_when_no_match(self, git_repo_with_files):
        model = StubModel([json.dumps({"action": "finish", "summary": "Done."})])
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Do nothing special.")
        _, user = model.calls[0]
        assert "RELEVANT SYMBOL CANDIDATES" not in user


class TestContextMetrics:
    def test_repo_map_chars_in_result(self, git_repo_with_files):
        model = StubModel([json.dumps({"action": "finish", "summary": "Done."})])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Task.")
        assert result.repo_map_chars > 0

    def test_system_prompt_chars_in_result(self, git_repo_with_files):
        model = StubModel([json.dumps({"action": "finish", "summary": "Done."})])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Task.")
        assert result.system_prompt_chars > 0

    def test_user_prompt_chars_in_result(self, git_repo_with_files):
        model = StubModel([json.dumps({"action": "finish", "summary": "Done."})])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Task.")
        assert result.user_prompt_chars > 0

    def test_active_code_chars_zero_initially(self, git_repo_with_files):
        model = StubModel([json.dumps({"action": "finish", "summary": "Done."})])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Task.")
        assert result.active_code_chars == 0

    def test_active_code_chars_after_read(self, git_repo_with_files):
        model = StubModel(
            [
                json.dumps({"action": "read_file", "path": "src/app.py"}),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Read the file.")
        assert result.active_code_chars > 0

    def test_working_memory_chars_in_result(self, git_repo_with_files):
        model = StubModel([json.dumps({"action": "finish", "summary": "Done."})])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Task.")
        assert result.working_memory_chars >= 0


class TestContextPruning:
    def test_pruning_reduces_active_code(self, git_repo):
        big_content = "x = 1  # " + "a" * 200 + "\n"
        (git_repo / "big.py").write_text(big_content * 500)
        (git_repo / "small.py").write_text("y = 2\n")
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=git_repo, check=True)
        model = StubModel(
            [
                json.dumps(
                    {"action": "read_file", "path": "big.py", "start": 1, "end": 220}
                ),
                json.dumps({"action": "read_file", "path": "small.py"}),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo, model, "")
        result = agent.run("Read two files.")
        assert result.iterations == 3
        assert result.active_code_chars <= 8000

    def test_pruning_keeps_recent_snippet(self, git_repo):
        big_content = "x = 1  # " + "a" * 200 + "\n"
        (git_repo / "big.py").write_text(big_content * 500)
        (git_repo / "small.py").write_text("y = 2\n")
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=git_repo, check=True)
        model = StubModel(
            [
                json.dumps(
                    {"action": "read_file", "path": "big.py", "start": 1, "end": 220}
                ),
                json.dumps({"action": "read_file", "path": "small.py"}),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo, model, "")
        agent.run("Read two files.")
        assert "read_file small.py" in agent.working_context.active_snippets
        assert "read_file big.py" not in agent.working_context.active_snippets
