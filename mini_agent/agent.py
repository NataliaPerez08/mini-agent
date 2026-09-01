from __future__ import annotations

from pathlib import Path
from typing import Any

from .experiments import ExperimentLogger
from .metrics_collector import AgentResult, build_experiment_run
from .model import OpenAICompatModel
from .orchestrator import Orchestrator
from .repo_map import build_repo_map
from .scanner import scan_repository
from .state import AgentState, Progress, TaskType, WorkingContext
from .symbols import build_symbol_index
from .tools import RepoTools

__all__ = ["AgentResult", "CodingAgent"]


class CodingAgent:
    """Facade for a single coding-agent run.

    Owns repository-level infrastructure (scan, symbol index, repo map,
    tools) and delegates the state-machine loop to Orchestrator.
    """

    def __init__(
        self,
        repo: Path,
        model: OpenAICompatModel,
        test_command: str,
        max_iterations: int = 12,
        verbose: bool = False,
        experiment_logger: ExperimentLogger | None = None,
    ):
        self.repo = repo.resolve()
        self.model = model
        self.scan = scan_repository(self.repo)
        self.symbol_index = build_symbol_index(self.repo, self.scan)
        self.repo_map = build_repo_map(
            self.repo, self.scan, symbol_index=self.symbol_index
        )
        self.tools = RepoTools(self.repo, test_command, symbol_index=self.symbol_index)
        self.max_iterations = max_iterations
        self.working_context = WorkingContext()
        self.last_tool_result = ""
        self.verbose = verbose
        self.progress = Progress()
        self._experiment_logger = experiment_logger

    @property
    def memory(self) -> list[str]:
        return self.working_context.to_memory_lines()

    def _vprint(self, *parts: object) -> None:
        if self.verbose:
            print(*parts)

    def _log_event(self, event_type: str, **data: Any) -> None:
        if self._experiment_logger is not None:
            self._experiment_logger.log_event(event_type, **data)

    def _transition(self, old: AgentState, new: AgentState) -> None:
        self._log_event("state_transition", **{"from": old.value, "to": new.value})

    def _save_experiment_summary(
        self,
        result: AgentResult,
        task: str,
        task_type: TaskType,
        git_commit: str | None,
        repo_dirty: bool | None,
    ) -> None:
        if self._experiment_logger is None:
            return
        run = build_experiment_run(
            result,
            run_id=self._experiment_logger.run_id,
            task=task,
            task_type=task_type.value,
            model=getattr(self.model, "model", "unknown"),
            temperature=getattr(self.model, "temperature", None),
            git_commit=git_commit,
            repo_dirty=repo_dirty,
        )
        self._experiment_logger.save_summary(run)

    def run(self, task: str) -> AgentResult:
        return Orchestrator(self, task).run()