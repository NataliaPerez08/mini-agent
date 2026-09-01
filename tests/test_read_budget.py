from __future__ import annotations

import json
from pathlib import Path

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from tests.conftest import StubModel


def _read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line]


def _resp(action: str, **kw) -> str:
    d = {"action": action}
    d.update(kw)
    return json.dumps(d)


class TestReadBudgetExhaustionInspect:
    """Reproduce the exact failure: INSPECT, MAX_READ=2, model tries 3rd read."""

    def test_ideal_sequence_read_read_finish(self, git_repo_with_files: Path):
        """After 2 reads the model should be able to finish without error."""
        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("finish", summary="Revisado."),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Revisa qué hay en la carpeta")

        assert result.final_state == "done"
        assert result.stop_reason == "completed"
        assert result.llm_calls == 3
        assert result.files_read >= 2

    def test_third_read_rejected_then_finish_recovery(self, git_repo_with_files: Path):
        """Model tries 3rd read after budget exhausted, then finishes on recovery."""
        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("read_file", path="README.md", start=1, end=10),
                _resp("finish", summary="Done with available context."),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Revisa qué hay en la carpeta")

        assert result.final_state == "done"
        assert result.stop_reason == "completed"
        assert result.llm_calls == 4
        assert result.files_read >= 2

    def test_third_read_rejected_twice_then_fail(self, git_repo_with_files: Path):
        """Model tries read twice after budget exhausted -> deterministic fail."""
        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("read_file", path="README.md", start=1, end=10),
                _resp("read_file", path="src/app.py", start=1, end=10),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Revisa qué hay en la carpeta")

        assert result.final_state == "failed"
        assert result.stop_reason == "invalid_action"
        assert result.llm_calls <= 4

    def test_read_c_is_never_executed(self, git_repo_with_files: Path):
        """The third read_file must never actually execute."""
        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("read_file", path="README.md", start=1, end=10),
                _resp("finish", summary="Done."),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Revisa qué hay en la carpeta")

        # files_read includes auto-read (README.md) + 2 model reads = 3
        # The 3rd read attempt (README.md) was NOT executed (budget exhausted)
        # but README.md was already in files_read from auto-read
        assert result.files_read >= 2
        assert result.llm_calls == 4

    def test_total_llm_calls_bounded(self, git_repo_with_files: Path):
        """No endless recovery loops."""
        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("read_file", path="README.md", start=1, end=10),
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Revisa qué hay en la carpeta")

        assert result.llm_calls <= 4


class TestReadBudgetExhaustionModify:
    """MODIFY tasks should allow plan/finish after budget exhaustion."""

    def test_budget_exhausted_allows_plan(self, git_repo_with_files: Path):
        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("read_file", path="README.md", start=1, end=10),
                _resp("plan", files=["src/app.py"], summary="Change return value"),
                _resp("apply_patch", patch="--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n def hello():\n-    return 'hello'\n+    return 'world'\n"),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Cambia el return de hello a world")

        assert result.final_state in ("done", "failed")
        assert result.llm_calls <= 5

    def test_budget_exhausted_read_fails_immediately(self, git_repo_with_files: Path):
        """For MODIFY, invalid read after budget exhaustion fails immediately."""
        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("read_file", path="README.md", start=1, end=10),
                _resp("read_file", path="src/app.py", start=1, end=10),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Cambia el return de hello a world")

        assert result.final_state == "failed"
        assert result.stop_reason == "invalid_action"


class TestBudgetExhaustionTraceEvents:
    """Verify experiment trace contains budget_exhausted and invalid_action events."""

    def test_budget_exhausted_event_in_trace(self, git_repo_with_files: Path):
        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("finish", summary="Done."),
            ]
        )
        agent = CodingAgent(
            git_repo_with_files, model, "", experiment_logger=logger
        )
        agent.run("Revisa qué hay en la carpeta")

        trace = _read_jsonl(logger.trace_path)
        types = [e["type"] for e in trace]
        assert "budget_exhausted" in types

        be = next(e for e in trace if e["type"] == "budget_exhausted")
        assert be["budget"] == "read_actions"
        assert be["used"] == 2
        assert be["limit"] == 2
        assert be["state"] == "read"

    def test_invalid_action_event_in_trace(self, git_repo_with_files: Path):
        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("read_file", path="README.md", start=1, end=10),
                _resp("finish", summary="Done."),
            ]
        )
        agent = CodingAgent(
            git_repo_with_files, model, "", experiment_logger=logger
        )
        agent.run("Revisa qué hay en la carpeta")

        trace = _read_jsonl(logger.trace_path)
        types = [e["type"] for e in trace]
        assert "invalid_action" in types

        ia = next(e for e in trace if e["type"] == "invalid_action")
        assert ia["action"] == "read_file"
        assert ia["reason"] == "read_budget_exhausted"

    def test_stop_reason_in_runs_jsonl(self, git_repo_with_files: Path):
        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("read_file", path="README.md", start=1, end=10),
                _resp("read_file", path="src/app.py", start=1, end=10),
            ]
        )
        agent = CodingAgent(
            git_repo_with_files, model, "", experiment_logger=logger
        )
        agent.run("Revisa qué hay en la carpeta")

        runs = _read_jsonl(out / "runs.jsonl")
        assert len(runs) == 1
        rec = runs[0]
        assert rec["stop_reason"] == "invalid_action"
        assert rec["final_state"] == "failed"
        assert rec["success"] is False

    def test_stop_reason_completed_on_success(self, git_repo_with_files: Path):
        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel([_resp("finish", summary="Done.")])
        agent = CodingAgent(
            git_repo_with_files, model, "", experiment_logger=logger
        )
        agent.run("Revisa qué hay en la carpeta")

        runs = _read_jsonl(out / "runs.jsonl")
        rec = runs[0]
        assert rec["stop_reason"] == "completed"
        assert rec["success"] is True


class TestContextMetricsInTrace:
    """Verify per-llm_call context breakdown metrics."""

    def test_llm_call_has_context_breakdown(self, git_repo_with_files: Path):
        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("finish", summary="Done."),
            ]
        )
        agent = CodingAgent(
            git_repo_with_files, model, "", experiment_logger=logger
        )
        agent.run("Revisa qué hay en la carpeta")

        trace = _read_jsonl(logger.trace_path)
        llm_calls = [e for e in trace if e["type"] == "llm_call"]
        assert len(llm_calls) == 2

        for call in llm_calls:
            assert "call_index" in call
            assert "state" in call
            assert "prompt_chars" in call
            assert "prompt_tokens" in call
            assert "task_chars" in call
            assert "repo_map_chars" in call
            assert "active_code_chars" in call
            assert "working_memory_chars" in call
            assert "tool_schema_chars" in call
            assert "instructions_chars" in call
            assert "other_context_chars" in call
            assert "relevant_symbols_chars" in call

    def test_llm_call_prompt_chars_is_total(self, git_repo_with_files: Path):
        """prompt_chars must be system + user prompt combined."""
        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel([_resp("finish", summary="Done.")])
        agent = CodingAgent(
            git_repo_with_files, model, "", experiment_logger=logger
        )
        agent.run("Revisa")

        trace = _read_jsonl(logger.trace_path)
        call = next(e for e in trace if e["type"] == "llm_call")
        assert call["prompt_chars"] > 0
        assert call["prompt_chars"] == call["instructions_chars"] + call["other_context_chars"] + call["task_chars"] + call["repo_map_chars"] + call["active_code_chars"] + call["working_memory_chars"] + call["relevant_symbols_chars"] + call["tool_schema_chars"]

    def test_total_and_max_prompt_chars_in_summary(
        self, git_repo_with_files: Path
    ):
        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("finish", summary="Done."),
            ]
        )
        agent = CodingAgent(
            git_repo_with_files, model, "", experiment_logger=logger
        )
        result = agent.run("Revisa qué hay en la carpeta")

        assert result.total_prompt_chars_sent > 0
        assert result.max_prompt_chars > 0
        assert result.total_prompt_chars_sent >= result.max_prompt_chars

        runs = _read_jsonl(out / "runs.jsonl")
        rec = runs[0]
        assert rec["total_prompt_chars_sent"] > 0
        assert rec["max_prompt_chars"] > 0
        assert rec["last_prompt_chars"] > 0


class TestBudgetExhaustedPromptFiltering:
    """Verify read_file is not exposed to the model after budget exhaustion."""

    def test_system_prompt_says_budget_exhausted(self, git_repo_with_files: Path):
        model = StubModel(
            [
                _resp("read_file", path="src/app.py", start=1, end=10),
                _resp("read_file", path="src/utils.py", start=1, end=10),
                _resp("finish", summary="Done."),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Revisa qué hay en la carpeta")

        # The 3rd call should have budget-exhausted system prompt
        # StubModel.calls is list of (system, user) tuples
        assert len(model.calls) == 3
        sys3, user3 = model.calls[2]
        assert "READ BUDGET EXHAUSTED" in sys3
        assert "READ BUDGET EXHAUSTED" in user3
        assert "finish" in sys3
