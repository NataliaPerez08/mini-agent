"""Regression tests for v3.1.8 behavior.

* _parse_failure_locations extracts file:line pairs from go test, go
  compile, and pytest diagnostics; dedupes; caps at 12.
* SymbolIndex.at_line returns the innermost symbol containing a line.
* On verification failure, symbols named by file:line diagnostics are
  auto-loaded into the repair context without consuming the read budget
  (v7 core loop, deterministic).
* Symbols already in the working context are not reloaded on repeated
  failures.
"""

import json
from pathlib import Path

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from mini_agent.orchestrator import Orchestrator, _parse_failure_locations
from mini_agent.symbols import Symbol, SymbolIndex
from tests.conftest import StubModel

MODIFY_TASK = "Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío"

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

PATCH_APP_2 = (
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,4 +1,5 @@\n"
    " def ApplyPolicy(ports):\n"
    "     if not ports:\n"
    "+        ports = []\n"
    "         return 'error: ports required'\n"
    "     return 'ok'\n"
)

FAIL_CMD_UTILS = "echo src/utils.py:4: assertion failed && exit 1"
FAIL_CMD_APP = "echo src/app.py:2: assertion failed && exit 1"


def _run_with_trace(
    repo: Path, model: StubModel, task: str, command: str
) -> tuple[CodingAgent, ExperimentLogger]:
    out = repo.parent / "experiments"
    logger = ExperimentLogger(generate_run_id(), out, enabled=True)
    agent = CodingAgent(repo, model, command, experiment_logger=logger)
    agent.run(task)
    return agent, logger


def _trace(logger: ExperimentLogger) -> list[dict]:
    return [
        json.loads(line)
        for line in logger.trace_path.read_text().splitlines()
        if line
    ]


def _diagnostic_events(logger: ExperimentLogger) -> list[dict]:
    return [
        e
        for e in _trace(logger)
        if e["type"] == "auto_symbol_read"
        and e.get("reason") == "test_failure_diagnostic"
    ]


class TestParseFailureLocations:
    def test_go_test_format(self) -> None:
        out = (
            "--- FAIL: TestValidate (0.00s)\n"
            "    validator_test.go:22: policy must be invalid\n"
            "    validator_test.go:30: unexpected issue\n"
            "FAIL\n"
            "FAIL\tiptables-visualizer/internal/engine\t0.003s\n"
        )
        assert _parse_failure_locations(out) == [
            ("validator_test.go", 22),
            ("validator_test.go", 30),
        ]

    def test_go_compile_format(self) -> None:
        out = (
            "# iptables-visualizer/internal/engine\n"
            "./internal/engine/validator.go:68:5: syntax error: unexpected ,\n"
        )
        assert _parse_failure_locations(out) == [
            ("./internal/engine/validator.go", 68)
        ]

    def test_pytest_short_and_traceback_formats(self) -> None:
        out = (
            "FAILED tests/test_app.py::TestX - AssertionError\n"
            "tests/test_app.py:15: AssertionError\n"
            'File "src/app.py", line 42, in ApplyPolicy\n'
        )
        assert _parse_failure_locations(out) == [
            ("tests/test_app.py", 15),
            ("src/app.py", 42),
        ]

    def test_noise_without_extension_is_ignored(self) -> None:
        assert _parse_failure_locations("http://localhost:11434/v1") == []

    def test_dedupe_and_cap(self) -> None:
        out = "\n".join([f"a.go:{i}: x" for i in range(1, 20)])
        out += "\na.go:1: x\n"
        locations = _parse_failure_locations(out)
        assert len(locations) == 12
        assert len(set(locations)) == 12


class TestAtLine:
    def _index(self) -> SymbolIndex:
        return SymbolIndex(
            symbols=[
                Symbol(
                    name="Outer",
                    kind="function",
                    path="src/app.py",
                    start_line=1,
                    end_line=10,
                ),
                Symbol(
                    name="inner",
                    kind="function",
                    path="src/app.py",
                    start_line=3,
                    end_line=5,
                ),
            ]
        )

    def test_innermost_symbol_wins(self) -> None:
        idx = self._index()
        assert idx.at_line("src/app.py", 4).name == "inner"
        assert idx.at_line("src/app.py", 7).name == "Outer"

    def test_boundaries_and_misses(self) -> None:
        idx = self._index()
        assert idx.at_line("src/app.py", 1).name == "Outer"
        assert idx.at_line("src/app.py", 10).name == "Outer"
        assert idx.at_line("src/app.py", 12) is None
        assert idx.at_line("src/other.py", 1) is None


class TestResolveFailurePath:
    def _orchestrator(self, repo: Path) -> Orchestrator:
        agent = CodingAgent(repo, StubModel([]), "false")
        return Orchestrator(agent, "task")

    def test_exact_repo_relative(self, git_repo_with_symbol: Path) -> None:
        orch = self._orchestrator(git_repo_with_symbol)
        assert orch._resolve_failure_path("src/app.py", []) == "src/app.py"

    def test_unique_basename(self, git_repo_with_symbol: Path) -> None:
        orch = self._orchestrator(git_repo_with_symbol)
        assert orch._resolve_failure_path("utils.py", []) == "src/utils.py"

    def test_basename_prefers_patched_dir(
        self, git_repo_with_symbol: Path
    ) -> None:
        orch = self._orchestrator(git_repo_with_symbol)
        assert (
            orch._resolve_failure_path("app.py", ["src/app.py"]) == "src/app.py"
        )

    def test_unknown_path_rejected(self, git_repo_with_symbol: Path) -> None:
        orch = self._orchestrator(git_repo_with_symbol)
        assert orch._resolve_failure_path("example.com", []) == ""
        assert orch._resolve_failure_path("nope/x.go", []) == ""


class TestDiagnosticSymbolAutoLoad:
    def test_repair_prompt_contains_diagnostic_symbol(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps(
                    {"action": "plan", "files": ["src/app.py"], "summary": "s"}
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "finish", "summary": "cannot fix"}),
            ]
        )
        agent, logger = _run_with_trace(
            git_repo_with_symbol, model, MODIFY_TASK, FAIL_CMD_UTILS
        )

        # call 2 is the first repair prompt (plan, patch, verify-fail)
        assert "def add(a, b):" in model.calls[2][1]
        assert "return a + b" in model.calls[2][1]

        events = _diagnostic_events(logger)
        assert len(events) == 1
        assert events[0]["path"] == "src/utils.py"
        assert events[0]["symbol"] == "add"
        assert events[0]["diagnostic"] == "src/utils.py:4"

        # pre-loads are not model read actions
        assert agent.progress.read_actions == 0

    def test_already_loaded_symbol_not_reloaded(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps(
                    {"action": "plan", "files": ["src/app.py"], "summary": "s"}
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "finish", "summary": "cannot fix"}),
            ]
        )
        _, logger = _run_with_trace(
            git_repo_with_symbol, model, MODIFY_TASK, FAIL_CMD_APP
        )
        # ApplyPolicy is auto-loaded at setup; the diagnostic must not
        # duplicate it.
        assert _diagnostic_events(logger) == []

    def test_line_without_symbol_is_skipped(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps(
                    {"action": "plan", "files": ["src/app.py"], "summary": "s"}
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "finish", "summary": "cannot fix"}),
            ]
        )
        _, logger = _run_with_trace(
            git_repo_with_symbol,
            model,
            MODIFY_TASK,
            "echo src/utils.py:1: boom && exit 1",
        )
        # line 1 is PI, not an indexed symbol
        assert _diagnostic_events(logger) == []

    def test_repeated_failures_do_not_duplicate(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps(
                    {"action": "plan", "files": ["src/app.py"], "summary": "s"}
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP_2}),
                json.dumps({"action": "finish", "summary": "cannot fix"}),
            ]
        )
        agent, logger = _run_with_trace(
            git_repo_with_symbol, model, MODIFY_TASK, FAIL_CMD_UTILS
        )
        # two verify failures, but add is loaded once
        assert len(_diagnostic_events(logger)) == 1
        # second repair prompt still shows the symbol
        assert "def add(a, b):" in model.calls[3][1]
        assert agent.progress.read_actions == 0
