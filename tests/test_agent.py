import json
from pathlib import Path

from mini_agent.agent import AgentResult, CodingAgent
from tests.conftest import StubModel


class TestCodingAgentRun:
    def test_finish_immediately(self, git_repo_with_files):
        model = StubModel([
            json.dumps({"action": "finish", "summary": "Nothing to do."}),
        ])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Do nothing.")
        assert isinstance(result, AgentResult)
        assert result.iterations == 1
        assert result.summary == "Nothing to do."

    def test_read_then_finish(self, git_repo_with_files):
        model = StubModel([
            json.dumps({"action": "read_file", "path": "src/app.py", "start": 1, "end": 10}),
            json.dumps({"action": "finish", "summary": "Read the file, looks good."}),
        ])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Read src/app.py and finish.")
        assert result.iterations == 2
        assert "good" in result.summary

    def test_search_then_finish(self, git_repo_with_files):
        model = StubModel([
            json.dumps({"action": "search_code", "query": "def hello", "path": "."}),
            json.dumps({"action": "finish", "summary": "Found it."}),
        ])
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
        assert result.iterations == 5
        assert "Stopped after 5 iterations" in result.summary

    def test_unsupported_action(self, git_repo_with_files):
        model = StubModel([
            json.dumps({"action": "delete_file", "path": "x"}),
            json.dumps({"action": "finish", "summary": "Done."}),
        ])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Try unsupported action.")
        assert result.iterations == 2
        assert "ERROR" in agent.memory[0]

    def test_loop_detection_repeats_error(self, git_repo_with_files):
        repeated = json.dumps({"action": "run_tests"})
        model = StubModel([repeated] * 10)
        agent = CodingAgent(git_repo_with_files, model, "", max_iterations=6)
        result = agent.run("Loop detection test.")
        assert result.iterations == 6
        error_found = any("identical action repeated" in m for m in agent.memory)
        assert error_found

    def test_run_tests_action(self, git_repo_with_files):
        model = StubModel([
            json.dumps({"action": "run_tests"}),
            json.dumps({"action": "finish", "summary": "Tests checked."}),
        ])
        agent = CodingAgent(git_repo_with_files, model, "true")
        result = agent.run("Run tests.")
        assert result.iterations == 2
        assert "EXIT_CODE=0" in agent.memory[0]

    def test_apply_patch_triggers_verification(self, git_repo_with_files):
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'world'\n"
        )
        model = StubModel([
            json.dumps({"action": "apply_patch", "patch": patch}),
            json.dumps({"action": "finish", "summary": "Patched and verified."}),
        ])
        agent = CodingAgent(git_repo_with_files, model, "true")
        result = agent.run("Patch the file.")
        assert result.iterations == 2
        assert "PATCH APPLIED" in agent.memory[0]
        assert "AUTO VERIFICATION" in agent.memory[0]

    def test_apply_patch_failed_no_verification(self, git_repo_with_files):
        model = StubModel([
            json.dumps({"action": "apply_patch", "patch": "invalid diff"}),
            json.dumps({"action": "finish", "summary": "Gave up."}),
        ])
        agent = CodingAgent(git_repo_with_files, model, "true")
        result = agent.run("Try bad patch.")
        assert result.iterations == 2
        assert "PATCH FAILED" in agent.memory[0]
        assert "AUTO VERIFICATION" not in agent.memory[0]

    def test_memory_grows_per_iteration(self, git_repo_with_files):
        model = StubModel([
            json.dumps({"action": "read_file", "path": "src/app.py"}),
            json.dumps({"action": "read_file", "path": "src/utils.py"}),
            json.dumps({"action": "finish", "summary": "Done."}),
        ])
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Read two files.")
        assert len(agent.memory) == 2
        assert "read_file" in agent.memory[0]
        assert "read_file" in agent.memory[1]

    def test_model_receives_system_prompt(self, git_repo_with_files):
        model = StubModel([
            json.dumps({"action": "finish", "summary": "Done."}),
        ])
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Task.")
        assert len(model.calls) == 1
        system, _ = model.calls[0]
        assert "JSON" in system.upper()
