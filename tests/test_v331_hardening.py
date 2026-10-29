"""Regression tests for v3.3.1 (CURRENT_ISSUES 2, ROADMAP v6 ladder).

Verification used to stop at the targeted run: when the patched
file's related test passed, the run went DONE without ever running the
full configured command, so a patch that broke tests outside the
targeted set was reported as successful.

v3.3.1 escalates narrow-to-broad: the targeted run fails fast with
narrow feedback; a targeted pass is confirmed against the full
configured command. When the configured command cannot be narrowed
(no related tests, or a framework without narrowing support), the
full command runs exactly once. Each executed command emits a
test_run trace event with its scope; the verdict test event is
unchanged (one per VERIFY cycle).
"""

from __future__ import annotations

import json
from pathlib import Path

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from mini_agent.tools import RepoTools
from tests.conftest import StubModel

MODIFY_TASK = "Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío"

REAL_PATCH = (
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,2 +1,4 @@\n"
    " def ApplyPolicy(ports):\n"
    "-    return 'ok'\n"
    "+    if not ports:\n"
    "+        return 'error: ports required'\n"
    "+    return 'ok'\n"
)

# Passes only after REAL_PATCH; matches pytest naming for src/app.py.
TEST_APP = (
    "from src.app import ApplyPolicy\n"
    "\n"
    "\n"
    "def test_apply_policy():\n"
    "    assert ApplyPolicy([]) == 'error: ports required'\n"
)

# Asserts the OLD behavior, so REAL_PATCH breaks it. It is NOT a
# related test of src/app.py by naming, so the targeted run never
# executes it: the regression the escalation must catch.
TEST_OTHER = (
    "from src.app import ApplyPolicy\n"
    "\n"
    "\n"
    "def test_old_behavior():\n"
    "    assert ApplyPolicy([]) == 'ok'\n"
)


def _repo_with_tests(git_repo_with_symbol: Path, *tests: tuple[str, str]) -> Path:
    repo = git_repo_with_symbol
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    for name, content in tests:
        (tests_dir / name).write_text(content)
    return repo


def _patch_model() -> StubModel:
    return StubModel(
        [
            json.dumps(
                {
                    "action": "plan",
                    "files": ["src/app.py"],
                    "summary": "Validate ports before accepting",
                }
            ),
            json.dumps({"action": "apply_patch", "patch": REAL_PATCH}),
        ]
    )


def _patch_then_finish_model() -> StubModel:
    return StubModel(
        [
            json.dumps(
                {
                    "action": "plan",
                    "files": ["src/app.py"],
                    "summary": "Validate ports before accepting",
                }
            ),
            json.dumps({"action": "apply_patch", "patch": REAL_PATCH}),
            json.dumps({"action": "finish", "summary": "Patched ApplyPolicy."}),
        ]
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _scopes(trace: list[dict]) -> list[str]:
    return [e["scope"] for e in trace if e["type"] == "test_run"]


class TestTargetedTestCommand:
    def test_pytest_narrowed(self, git_repo_with_symbol: Path) -> None:
        tools = RepoTools(git_repo_with_symbol, "python -m pytest -q")
        cmd = tools.targeted_test_command(["tests/test_app.py"])
        assert cmd == "python -m pytest -q tests/test_app.py"

    def test_unrecognized_framework_not_narrowed(
        self, git_repo_with_symbol: Path
    ) -> None:
        tools = RepoTools(git_repo_with_symbol, "npm test")
        assert tools.targeted_test_command(["tests/app.test.js"]) == ""

    def test_no_test_files_not_narrowed(self, git_repo_with_symbol: Path) -> None:
        tools = RepoTools(git_repo_with_symbol, "python -m pytest -q")
        assert tools.targeted_test_command([]) == ""


class TestEscalation:
    def test_regression_outside_targeted_set_is_caught(
        self, git_repo_with_symbol: Path
    ) -> None:
        repo = _repo_with_tests(
            git_repo_with_symbol, ("test_app.py", TEST_APP), ("test_other.py", TEST_OTHER)
        )
        agent = CodingAgent(repo, _patch_then_finish_model(), "python -m pytest -q")
        result = agent.run(MODIFY_TASK)

        # The targeted run passes; the full command catches test_other.
        assert result.final_state == "failed"
        assert result.stop_reason == "test_failure"
        assert result.patches == 1
        assert result.tests_run == 1

    def test_regression_caught_emits_both_scopes(
        self, git_repo_with_symbol: Path
    ) -> None:
        repo = _repo_with_tests(
            git_repo_with_symbol, ("test_app.py", TEST_APP), ("test_other.py", TEST_OTHER)
        )
        out = repo.parent / "experiments"
        logger = ExperimentLogger(generate_run_id(), out, enabled=True)
        agent = CodingAgent(
            repo, _patch_then_finish_model(), "python -m pytest -q", experiment_logger=logger
        )
        agent.run(MODIFY_TASK)

        trace = _read_jsonl(logger.trace_path)
        assert _scopes(trace) == ["targeted", "full"]
        runs = [e for e in trace if e["type"] == "test_run"]
        assert runs[0]["success"] is True
        assert runs[1]["success"] is False
        verdict = [e for e in trace if e["type"] == "test"]
        assert len(verdict) == 1
        assert verdict[0]["success"] is False
        assert "test_old_behavior" in verdict[0]["failure_summary"]

        rec = _read_jsonl(out / "runs.jsonl")[0]
        assert rec["success"] is False
        assert rec["stop_reason"] == "test_failure"
        assert rec["harness_version"] == "v3.3.1"

    def test_targeted_failure_is_fast(self, git_repo_with_symbol: Path) -> None:
        # The related test itself fails after the patch: verification
        # must stop at the targeted run, not pay for the full command.
        repo = _repo_with_tests(git_repo_with_symbol, ("test_app.py", TEST_OTHER))
        out = repo.parent / "experiments"
        logger = ExperimentLogger(generate_run_id(), out, enabled=True)
        agent = CodingAgent(
            repo, _patch_then_finish_model(), "python -m pytest -q", experiment_logger=logger
        )
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "test_failure"
        assert _scopes(_read_jsonl(logger.trace_path)) == ["targeted"]

    def test_clean_success_runs_both_and_completes(
        self, git_repo_with_symbol: Path
    ) -> None:
        repo = _repo_with_tests(git_repo_with_symbol, ("test_app.py", TEST_APP))
        out = repo.parent / "experiments"
        logger = ExperimentLogger(generate_run_id(), out, enabled=True)
        agent = CodingAgent(
            repo, _patch_model(), "python -m pytest -q", experiment_logger=logger
        )
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.stop_reason == "completed"
        assert result.tests_run == 1
        trace = _read_jsonl(logger.trace_path)
        assert _scopes(trace) == ["targeted", "full"]
        assert all(e["success"] for e in trace if e["type"] == "test_run")

    def test_unnarrowable_command_runs_full_once(
        self, git_repo_with_symbol: Path
    ) -> None:
        # The test file is collected by pytest but not related to
        # src/app.py by naming: a single full run, no escalation.
        repo = _repo_with_tests(git_repo_with_symbol, ("test_zzz.py", TEST_APP))
        out = repo.parent / "experiments"
        logger = ExperimentLogger(generate_run_id(), out, enabled=True)
        agent = CodingAgent(
            repo, _patch_model(), "python -m pytest -q", experiment_logger=logger
        )
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        trace = _read_jsonl(logger.trace_path)
        assert _scopes(trace) == ["full"]
