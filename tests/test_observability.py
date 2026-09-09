from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from mini_agent.version import HARNESS_VERSION
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
INSPECT_TASK = "Revisa qué hay en la carpeta"


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.fixture
def git_repo_with_symbol(git_repo: Path) -> Path:
    repo = git_repo
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text(
        "def ApplyPolicy(ports):\n    return 'ok'\n"
    )
    (repo / "src" / "utils.py").write_text(
        "PI = 3.14\n\ndef add(a, b):\n    return a + b\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _run_with_logger(
    repo: Path, model: StubModel, task: str
) -> tuple[ExperimentLogger, Path]:
    out = repo.parent / "experiments"
    logger = ExperimentLogger(generate_run_id(), out, enabled=True)
    agent = CodingAgent(repo, model, "true", experiment_logger=logger)
    agent.run(task)
    return logger, out


class TestInspectAutoReadTrace:
    def test_auto_read_events_in_trace(self, git_repo_with_files: Path) -> None:
        model = StubModel([json.dumps({"action": "finish", "summary": "ok"})])
        logger, _ = _run_with_logger(git_repo_with_files, model, INSPECT_TASK)

        trace = _read_jsonl(logger.trace_path)
        auto = [e for e in trace if e["type"] == "auto_read"]
        assert auto, "INSPECT auto-reads must emit auto_read events"
        for event in auto:
            assert event["path"]
            assert event["chars"] > 0
            assert event["reason"] == "inspect_fast_path"
        paths = [e["path"] for e in auto]
        assert "README.md" in paths

    def test_run_start_is_first_event(self, git_repo_with_files: Path) -> None:
        model = StubModel([json.dumps({"action": "finish", "summary": "ok"})])
        logger, _ = _run_with_logger(git_repo_with_files, model, INSPECT_TASK)

        trace = _read_jsonl(logger.trace_path)
        assert trace[0]["type"] == "run_start"
        types = [e["type"] for e in trace]
        assert types.index("run_start") < types.index("auto_read")
        first_transition = types.index("state_transition")
        assert types.index("auto_read") < first_transition


class TestModifyAutoSymbolTrace:
    def test_auto_symbol_read_event_in_trace(
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
        logger, _ = _run_with_logger(git_repo_with_symbol, model, MODIFY_TASK)

        trace = _read_jsonl(logger.trace_path)
        auto = [e for e in trace if e["type"] == "auto_symbol_read"]
        assert len(auto) == 1
        event = auto[0]
        assert event["path"] == "src/app.py"
        assert event["symbol"] == "ApplyPolicy"
        assert event["kind"] == "function"
        assert event["start_line"] == 1
        assert event["end_line"] == 2
        assert event["chars"] > 0
        assert event["reason"] == "exact_task_symbol_match"

    def test_auto_symbol_read_ordering(self, git_repo_with_symbol: Path) -> None:
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
        logger, _ = _run_with_logger(git_repo_with_symbol, model, MODIFY_TASK)

        trace = _read_jsonl(logger.trace_path)
        types = [e["type"] for e in trace]
        assert types[0] == "run_start"
        assert types.index("symbol_index") < types.index("auto_symbol_read")
        assert types.index("auto_symbol_read") < types.index("state_transition")

    def test_no_auto_symbol_read_without_exact_match(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [json.dumps({"action": "finish", "summary": "Nothing targeted"})]
        )
        logger, _ = _run_with_logger(
            git_repo_with_symbol, model, "implementa una mejora general"
        )

        trace = _read_jsonl(logger.trace_path)
        assert not [e for e in trace if e["type"] == "auto_symbol_read"]


class TestRunSummaryPreLoadDetail:
    def test_modify_summary_carries_auto_symbols(
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
        _, out = _run_with_logger(git_repo_with_symbol, model, MODIFY_TASK)

        rec = _read_jsonl(out / "runs.jsonl")[0]
        assert rec["auto_symbols"] == ["src/app.py::ApplyPolicy"]
        assert rec["auto_read_files"] == []
        assert rec["harness_version"] == HARNESS_VERSION

    def test_inspect_summary_carries_auto_read_files(
        self, git_repo_with_files: Path
    ) -> None:
        model = StubModel([json.dumps({"action": "finish", "summary": "ok"})])
        _, out = _run_with_logger(git_repo_with_files, model, INSPECT_TASK)

        rec = _read_jsonl(out / "runs.jsonl")[0]
        assert "README.md" in rec["auto_read_files"]
        assert rec["auto_symbols"] == []

    def test_summary_without_pre_loads_has_empty_lists(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [json.dumps({"action": "finish", "summary": "Nothing targeted"})]
        )
        _, out = _run_with_logger(
            git_repo_with_symbol, model, "implementa una mejora general"
        )

        rec = _read_jsonl(out / "runs.jsonl")[0]
        assert rec["auto_symbols"] == []
        assert rec["auto_read_files"] == []
