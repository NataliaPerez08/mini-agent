from __future__ import annotations

import json
from pathlib import Path

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
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

MODIFY_TASK = "Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío"


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
            json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
        ]
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class TestUnverifiedPatch:
    """Regression for CURRENT_ISSUES 1.2: a patch with no configured test
    command must not be reported as successful."""

    def test_patch_without_test_command_fails_unverified(
        self, git_repo_with_symbol: Path
    ) -> None:
        agent = CodingAgent(git_repo_with_symbol, _patch_model(), "")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "unverified_patch"
        assert result.patches == 1
        assert result.tests_run == 0
        assert result.llm_calls == 2
        assert "unverified" in result.summary.lower()

    def test_unverified_patch_records_failed_run(
        self, git_repo_with_symbol: Path
    ) -> None:
        out = git_repo_with_symbol.parent / "experiments"
        logger = ExperimentLogger(generate_run_id(), out, enabled=True)
        agent = CodingAgent(
            git_repo_with_symbol, _patch_model(), "", experiment_logger=logger
        )
        result = agent.run(MODIFY_TASK)

        rec = _read_jsonl(out / "runs.jsonl")[0]
        assert rec["success"] is False
        assert rec["final_state"] == "failed"
        assert rec["stop_reason"] == "unverified_patch"
        assert rec["tests"] == 0
        assert rec["patches"] == 1
        assert rec["llm_calls"] == result.llm_calls

        trace = _read_jsonl(logger.trace_path)
        test_events = [e for e in trace if e["type"] == "test"]
        assert len(test_events) == 1
        assert test_events[0]["status"] == "unverified"
        assert test_events[0]["success"] is False
        assert trace[-1]["type"] == "run_end"
        assert trace[-1]["success"] is False

    def test_patch_with_test_command_still_completes(
        self, git_repo_with_symbol: Path
    ) -> None:
        agent = CodingAgent(git_repo_with_symbol, _patch_model(), "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.stop_reason == "completed"
        assert result.tests_run == 1
