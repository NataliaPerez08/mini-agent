from __future__ import annotations

import json
from pathlib import Path

from mini_agent.agent import CodingAgent
from tests.conftest import StubModel

PATCH_APP = (
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,2 +1,4 @@\n"
    " def ApplyPolicy(ports):\n"
    "-    return 'ok'\n"
    "+    if not ports:\n"
    "+        return 'error: ports required'\n"
    "+    return 'ok'\n"
)


class TestExactSymbolAutoLoad:
    def test_modify_task_auto_loads_exact_target_symbol(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(
            "Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío"
        )
        assert result.final_state == "done"
        assert result.llm_calls <= 4
        assert result.symbol_reads >= 1
        assert result.full_file_reads == 0
        assert result.patches == 1
        assert result.tests_run == 1
        assert any("ApplyPolicy" in line for line in agent.working_context.to_active_code_lines())

    def test_auto_symbol_read_consumes_read_budget(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        agent.run("Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío")
        assert len(agent.progress.symbols_read) == 1
        assert next(iter(agent.progress.symbols_read)) == "src/app.py::ApplyPolicy"
        assert agent.progress.read_actions == 1

    def test_modify_task_without_exact_symbol_does_not_auto_load(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps({"action": "run_tests"}),
                json.dumps({"action": "finish", "summary": "Nothing targeted"}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "")
        result = agent.run("implementa una mejora general en el proyecto")
        assert result.symbol_reads == 0
        assert result.final_state == "done"


class TestModelOutputContract:
    def test_next_action_rejected_then_recovers(self, git_repo_with_files: Path) -> None:
        model = StubModel(
            [
                json.dumps({"next_action": "none"}),
                json.dumps({"action": "finish", "summary": "Recovered"}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Do nothing.")
        assert result.final_state == "done"
        assert any("next_action" in m for m in agent.memory)
        assert any("'action'" in m for m in agent.memory)

    def test_missing_action_rejected(self, git_repo_with_files: Path) -> None:
        model = StubModel(
            [
                json.dumps({"summary": "no action here"}),
                json.dumps({"action": "finish", "summary": "Done"}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Do nothing.")
        assert result.final_state == "done"
        assert any("'action'" in m for m in agent.memory)

    def test_repeated_invalid_output_hits_soft_bound(
        self, git_repo_with_files: Path
    ) -> None:
        model = StubModel([json.dumps({"foo": "bar"})] * 4)
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Do nothing.")
        assert any("identical invalid output repeated" in m for m in agent.memory)
        assert result.iterations <= 5

    def test_invalid_output_does_not_reach_state_machine(
        self, git_repo_with_files: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps({"next_action": "none"}),
                json.dumps({"action": "finish", "summary": "Recovered"}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Do nothing.")
        assert not any(a.startswith("next_action") for a in agent.progress.last_actions)


class TestV311Metrics:
    def test_run_metrics_reflect_auto_symbol(self, git_repo_with_symbol: Path) -> None:
        from mini_agent.experiments import ExperimentLogger, generate_run_id

        out = git_repo_with_symbol.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
            ]
        )
        agent = CodingAgent(
            git_repo_with_symbol,
            model,
            "true",
            experiment_logger=logger,
        )
        result = agent.run(
            "Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío"
        )
        assert result.final_state == "done"

        def _read_jsonl(p: Path):
            return [json.loads(line) for line in p.read_text().splitlines()]

        runs = _read_jsonl(out / "runs.jsonl")
        rec = runs[0]
        assert rec["harness_version"] == "v3.1.2"
        assert rec["symbol_reads"] == result.symbol_reads
        assert rec["full_file_reads"] == result.full_file_reads
        assert rec["final_state"] == "done"

    def test_invalid_action_trace_event(self, git_repo_with_files: Path) -> None:
        from mini_agent.experiments import ExperimentLogger, generate_run_id

        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            [
                json.dumps({"next_action": "none"}),
                json.dumps({"action": "finish", "summary": "Recovered"}),
            ]
        )
        agent = CodingAgent(
            git_repo_with_files,
            model,
            "",
            experiment_logger=logger,
        )
        agent.run("Do nothing.")

        events = [
            json.loads(line)
            for line in logger.trace_path.read_text().splitlines()
        ]
        invalid = [
            e for e in events
            if e["type"] == "invalid_action"
            and e.get("reason") == "model_output_contract"
        ]
        assert len(invalid) == 1