from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .experiments import ExperimentRun, RunMetrics, now_iso
from .scanner import RepoScan
from .state import WorkingContext
from .symbols import SymbolIndex
from .version import HARNESS_VERSION

VPrint = Callable[..., None]


@dataclass
class AgentResult:
    summary: str
    iterations: int
    llm_calls: int = 0
    tool_calls: int = 0
    files_read: int = 0
    searches: int = 0
    patches: int = 0
    tests_run: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed: float = 0.0
    task_type: str = ""
    final_state: str = ""
    stop_reason: str = ""
    repo_map_chars: int = 0
    active_code_chars: int = 0
    working_memory_chars: int = 0
    system_prompt_chars: int = 0
    user_prompt_chars: int = 0
    total_prompt_chars_sent: int = 0
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


def print_preflight(
    scan: RepoScan,
    symbol_index: SymbolIndex,
    repo_map: str,
    vprint: VPrint,
) -> None:
    vprint("[verbose] === PREFLIGHT ===")
    vprint(f"[verbose] files: {len(scan.files)}")
    langs = ", ".join(f"{k}={v}" for k, v in scan.languages.items())
    vprint(f"[verbose] languages: {langs or '<none>'}")
    vprint(f"[verbose] important_files: {len(scan.important_files)}")
    vprint(f"[verbose] tests: {len(scan.test_files)}")
    vprint(f"[verbose] symbols: {len(symbol_index.symbols)}")
    vprint(
        f"[verbose] symbol_index: {symbol_index.index_elapsed_seconds:.2f}s"
        if symbol_index.index_elapsed_seconds
        else "[verbose] symbol_index: 0.00s"
    )
    if symbol_index.errors:
        vprint(f"[verbose] symbol_index_errors: {symbol_index.errors}")
    if symbol_index.truncated:
        vprint("[verbose] symbol_index_truncated: true")
    symbol_count = sum(
        1 for line in repo_map.splitlines() if line.startswith("  ")
    )
    vprint(f"[verbose] repo_map_symbols: {symbol_count}")
    vprint(f"[verbose] repo_map_chars: {len(repo_map)}")


def print_context_breakdown(
    repo_map: str,
    working_context: WorkingContext,
    system_prompt: str,
    user_prompt: str,
    vprint: VPrint,
) -> None:
    vprint("[verbose] context breakdown:")
    vprint(f"[verbose]   repo_map_chars: {len(repo_map)}")
    vprint(f"[verbose]   active_code_chars: {working_context.active_code_chars}")
    vprint(
        f"[verbose]   symbol_context_chars: {working_context.symbol_context_chars}"
    )
    vprint(
        f"[verbose]   full_file_context_chars: {working_context.full_file_context_chars}"
    )
    vprint(f"[verbose]   memory_chars: {working_context.memory_chars}")
    vprint(f"[verbose]   system_prompt_chars: {len(system_prompt)}")
    vprint(f"[verbose]   user_prompt_chars: {len(user_prompt)}")


def print_metrics(result: AgentResult, vprint: VPrint) -> None:
    vprint("")
    vprint("[metrics]")
    vprint(f"llm_calls: {result.llm_calls}")
    vprint(f"tool_calls: {result.tool_calls}")
    vprint(f"files_read: {result.files_read}")
    vprint(f"searches: {result.searches}")
    vprint(f"patches: {result.patches}")
    vprint(f"tests: {result.tests_run}")
    if result.prompt_tokens:
        vprint(f"prompt_tokens: {result.prompt_tokens}")
    if result.completion_tokens:
        vprint(f"completion_tokens: {result.completion_tokens}")
    vprint(f"repo_map_chars: {result.repo_map_chars}")
    vprint(f"active_code_chars: {result.active_code_chars}")
    vprint(f"working_memory_chars: {result.working_memory_chars}")
    vprint(f"system_prompt_chars: {result.system_prompt_chars}")
    vprint(f"user_prompt_chars: {result.user_prompt_chars}")
    vprint(f"total_prompt_chars_sent: {result.total_prompt_chars_sent}")
    vprint(f"max_prompt_chars: {result.max_prompt_chars}")
    vprint(f"stop_reason: {result.stop_reason}")
    vprint(f"elapsed: {result.elapsed:.1f}s")
    vprint("")
    vprint("[symbol metrics]")
    vprint(f"indexed_symbols: {result.symbol_index_symbols}")
    vprint(f"symbol_reads: {result.symbol_reads}")
    vprint(f"full_file_reads: {result.full_file_reads}")
    vprint(f"symbol_context_chars: {result.symbol_context_chars}")
    vprint(f"full_file_context_chars: {result.full_file_context_chars}")


def build_run_metrics(result: AgentResult) -> RunMetrics:
    return RunMetrics(
        llm_calls=result.llm_calls,
        tool_calls=result.tool_calls,
        files_read=result.files_read,
        searches=result.searches,
        patches=result.patches,
        tests=result.tests_run,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        repo_map_chars=result.repo_map_chars,
        active_code_chars=result.active_code_chars,
        working_memory_chars=result.working_memory_chars,
        elapsed_seconds=result.elapsed,
        total_prompt_chars_sent=result.total_prompt_chars_sent,
        last_prompt_chars=result.user_prompt_chars,
        max_prompt_chars=result.max_prompt_chars,
        symbol_index_files=result.symbol_index_files,
        symbol_index_symbols=result.symbol_index_symbols,
        symbol_index_errors=result.symbol_index_errors,
        symbol_index_truncated=result.symbol_index_truncated,
        symbol_index_elapsed_seconds=result.symbol_index_elapsed_seconds,
        symbol_reads=result.symbol_reads,
        full_file_reads=result.full_file_reads,
        symbol_context_chars=result.symbol_context_chars,
        full_file_context_chars=result.full_file_context_chars,
    )


def build_experiment_run(
    result: AgentResult,
    *,
    run_id: str,
    task: str,
    task_type: str,
    model: str,
    temperature: float | None,
    git_commit: str | None,
    repo_dirty: bool | None,
) -> ExperimentRun:
    return ExperimentRun(
        run_id=run_id,
        timestamp=now_iso(),
        harness_version=HARNESS_VERSION,
        model=model,
        task=task,
        task_type=task_type,
        success=(result.final_state == "done"),
        final_state=result.final_state,
        metrics=build_run_metrics(result),
        stop_reason=result.stop_reason,
        git_commit=git_commit,
        repo_dirty=repo_dirty,
        temperature=temperature,
        context_window=None,
        max_tokens=None,
    )
