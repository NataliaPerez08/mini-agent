"""Regression tests for v3.1.6 behavior.

* Go targeted verification (`<stem>_test.go` same-dir matching,
  package-targeted `go test`).
* Multi-symbol exact auto-load, including path-mention disambiguation.
* Deterministic pre-loads do not consume the model's read-action budget.
"""

import json
import subprocess
from pathlib import Path

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from tests.conftest import StubModel


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    return repo


def _read_trace(logger: ExperimentLogger) -> list[dict]:
    return [json.loads(l) for l in logger.trace_path.read_text().splitlines() if l]


class TestAutoLoadBudget:
    def test_auto_symbols_do_not_consume_read_budget(self, tmp_path: Path) -> None:
        repo = _git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src" / "app.py").write_text(
            "def ApplyPolicy(ports):\n"
            "    return 'ok'\n"
            "\n"
            "\n"
            "def HelperA():\n"
            "    return 1\n"
            "\n"
            "\n"
            "def HelperB():\n"
            "    return 2\n"
        )
        (repo / "src" / "extra.py").write_text(
            "def HelperC():\n"
            "    return 3\n"
            "\n"
            "\n"
            "def HelperD():\n"
            "    return 4\n"
            "\n"
            "\n"
            "def HelperE():\n"
            "    return 5\n"
        )
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True)

        task = "Corrige ApplyPolicy para que use HelperA y HelperB"
        model = StubModel(
            [
                json.dumps({"action": "read_symbol", "path": "src/extra.py", "symbol": "HelperC"}),
                json.dumps({"action": "read_symbol", "path": "src/extra.py", "symbol": "HelperD"}),
                json.dumps({"action": "finish", "summary": "inspected"}),
                json.dumps({"action": "finish", "summary": "inspected"}),
            ]
        )
        logger = ExperimentLogger(generate_run_id(), tmp_path / "exp", enabled=True)
        agent = CodingAgent(repo, model, "true", experiment_logger=logger)
        agent.run(task)

        trace = _read_trace(logger)
        auto = [e for e in trace if e["type"] == "auto_symbol_read"]
        assert len(auto) == 3
        exhausted = [
            e
            for e in trace
            if e["type"] == "budget_exhausted" and e.get("budget") == "read_actions"
        ]
        assert exhausted == []
        read_errors = [
            e
            for e in trace
            if e["type"] == "invalid_action"
            and e.get("reason") == "read_budget_exhausted"
        ]
        assert read_errors == []
