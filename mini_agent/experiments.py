from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_FAILURE_SUMMARY_CHARS = 2000
MAX_ERROR_MESSAGE_CHARS = 500


@dataclass
class RunMetrics:
    llm_calls: int = 0
    tool_calls: int = 0
    files_read: int = 0
    searches: int = 0
    patches: int = 0
    tests: int = 0

    prompt_tokens: int = 0
    completion_tokens: int = 0

    repo_map_chars: int = 0
    active_code_chars: int = 0
    working_memory_chars: int = 0

    elapsed_seconds: float = 0.0

    total_prompt_chars_sent: int = 0
    last_prompt_chars: int = 0
    max_prompt_chars: int = 0

    symbol_index_files: int = 0
    symbol_index_symbols: int = 0
    symbol_index_errors: int = 0
    symbol_index_truncated: bool = False
    symbol_index_elapsed_seconds: float = 0.0

    symbol_reads: int = 0
    full_file_reads: int = 0
    symbol_context_chars: int = 0
    full_file_context_chars: int = 0

    auto_read_files: list[str] = field(default_factory=list)
    auto_symbols: list[str] = field(default_factory=list)


@dataclass
class ExperimentRun:
    run_id: str
    timestamp: str

    harness_version: str
    model: str
    task: str
    task_type: str

    success: bool
    final_state: str

    metrics: RunMetrics

    stop_reason: str = ""

    git_commit: str | None = None
    repo_dirty: bool | None = None

    temperature: float | None = None
    context_window: int | None = None
    max_tokens: int | None = None


def generate_run_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def capture_git_metadata(repo: Path) -> tuple[str | None, bool | None]:
    git_commit: str | None = None
    repo_dirty: bool | None = None

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            git_commit = proc.stdout.strip()
    except Exception:
        pass

    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            repo_dirty = bool(proc.stdout.strip())
    except Exception:
        pass

    return git_commit, repo_dirty


class ExperimentLogger:
    def __init__(
        self,
        run_id: str,
        output_dir: Path,
        enabled: bool = True,
    ):
        self.run_id = run_id
        self.output_dir = Path(output_dir)
        self.traces_dir = self.output_dir / "traces"
        self.enabled = enabled
        self._start_time = time.time()

        self.trace_path = self.traces_dir / f"{self.run_id}.jsonl"
        self.runs_path = self.output_dir / "runs.jsonl"

        if self.enabled:
            self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.traces_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def log_event(self, event_type: str, **data: Any) -> None:
        if not self.enabled:
            return
        try:
            event: dict[str, Any] = {
                "run_id": self.run_id,
                "type": event_type,
                "timestamp": now_iso(),
                "elapsed_seconds": round(time.time() - self._start_time, 4),
            }
            event.update(data)
            line = json.dumps(event, ensure_ascii=False)
            with open(self.trace_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def save_summary(self, run: ExperimentRun) -> None:
        if not self.enabled:
            return
        try:
            record: dict[str, Any] = {
                "run_id": run.run_id,
                "timestamp": run.timestamp,
                "harness_version": run.harness_version,
                "model": run.model,
                "task": run.task,
                "task_type": run.task_type,
                "success": run.success,
                "final_state": run.final_state,
                "stop_reason": run.stop_reason,
                "llm_calls": run.metrics.llm_calls,
                "tool_calls": run.metrics.tool_calls,
                "files_read": run.metrics.files_read,
                "searches": run.metrics.searches,
                "patches": run.metrics.patches,
                "tests": run.metrics.tests,
                "prompt_tokens": run.metrics.prompt_tokens,
                "completion_tokens": run.metrics.completion_tokens,
                "elapsed_seconds": run.metrics.elapsed_seconds,
                "repo_map_chars": run.metrics.repo_map_chars,
                "active_code_chars": run.metrics.active_code_chars,
                "working_memory_chars": run.metrics.working_memory_chars,
                "total_prompt_chars_sent": run.metrics.total_prompt_chars_sent,
                "last_prompt_chars": run.metrics.last_prompt_chars,
                "max_prompt_chars": run.metrics.max_prompt_chars,
                "git_commit": run.git_commit,
                "repo_dirty": run.repo_dirty,
                "temperature": run.temperature,
                "context_window": run.context_window,
                "max_tokens": run.max_tokens,
                "symbol_index_files": run.metrics.symbol_index_files,
                "symbol_index_symbols": run.metrics.symbol_index_symbols,
                "symbol_index_errors": run.metrics.symbol_index_errors,
                "symbol_index_truncated": run.metrics.symbol_index_truncated,
                "symbol_index_elapsed_seconds": run.metrics.symbol_index_elapsed_seconds,
                "symbol_reads": run.metrics.symbol_reads,
                "full_file_reads": run.metrics.full_file_reads,
                "symbol_context_chars": run.metrics.symbol_context_chars,
                "full_file_context_chars": run.metrics.full_file_context_chars,
                "auto_read_files": run.metrics.auto_read_files,
                "auto_symbols": run.metrics.auto_symbols,
            }
            line = json.dumps(record, ensure_ascii=False)
            with open(self.runs_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
