from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context import (
    build_state_prompt,
    format_symbol_candidates,
    get_system_prompt,
    git_status,
)
from .experiments import (
    MAX_ERROR_MESSAGE_CHARS,
    MAX_FAILURE_SUMMARY_CHARS,
    ExperimentLogger,
    ExperimentRun,
    RunMetrics,
    capture_git_metadata,
    now_iso,
)
from .model import OpenAICompatModel, extract_json_object
from .repo_map import MAX_REPO_MAP_CHARS, MAX_REPO_MAP_CHARS_INSPECT, build_repo_map
from .scanner import scan_repository
from .state import (
    ALLOWED_ACTIONS,
    BUDGET_WARNING,
    FAST_PATH_MAX_CHARS,
    MAX_CODE_CONTEXT_CHARS,
    MAX_READ_ACTIONS,
    MAX_RELEVANT_SYMBOLS,
    MAX_REPAIR_CYCLES,
    AgentState,
    Progress,
    TaskType,
    WorkingContext,
    choose_inspection_files,
    classify_task,
    validate_transition,
)
from .symbols import build_symbol_index, rank_symbols_for_task, reindex_files
from .tools import RepoTools
from .version import HARNESS_VERSION

_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "can",
        "shall",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "and",
        "or",
        "but",
        "not",
        "no",
        "nor",
        "so",
        "yet",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "de",
        "del",
        "en",
        "y",
        "o",
        "que",
        "se",
        "su",
        "sus",
        "fix",
        "change",
        "modify",
        "implement",
        "add",
        "remove",
        "corrige",
        "modifica",
        "cambia",
        "implementa",
        "anade",
        "agrega",
        "elimina",
        "bug",
        "error",
        "failing",
        "debug",
        "donde",
        "encuentra",
        "localiza",
        "where",
        "find",
        "locate",
        "file",
        "files",
        "code",
        "function",
        "method",
        "class",
        "test",
    }
)


def _extract_keywords(task: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", task)
    keywords: list[str] = []
    seen: set[str] = set()
    for w in words:
        wl = w.lower()
        if len(wl) < 3 or wl in _STOP_WORDS or wl in seen:
            continue
        seen.add(wl)
        keywords.append(w)
    return keywords


def _count_diff_lines(diff_text: str) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


_READ_ACTIONS = frozenset({"read_file", "read_symbol", "search_code"})


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


class CodingAgent:
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
        metrics = RunMetrics(
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
        run = ExperimentRun(
            run_id=self._experiment_logger.run_id,
            timestamp=now_iso(),
            harness_version=HARNESS_VERSION,
            model=getattr(self.model, "model", "unknown"),
            task=task,
            task_type=task_type.value,
            success=(result.final_state == "done"),
            final_state=result.final_state,
            metrics=metrics,
            stop_reason=result.stop_reason,
            git_commit=git_commit,
            repo_dirty=repo_dirty,
            temperature=getattr(self.model, "temperature", None),
            context_window=None,
            max_tokens=None,
        )
        self._experiment_logger.save_summary(run)

    def _print_preflight(self) -> None:
        self._vprint("[verbose] === PREFLIGHT ===")
        self._vprint(f"[verbose] files: {len(self.scan.files)}")
        langs = ", ".join(f"{k}={v}" for k, v in self.scan.languages.items())
        self._vprint(f"[verbose] languages: {langs or '<none>'}")
        self._vprint(f"[verbose] important_files: {len(self.scan.important_files)}")
        self._vprint(f"[verbose] tests: {len(self.scan.test_files)}")
        self._vprint(f"[verbose] symbols: {len(self.symbol_index.symbols)}")
        self._vprint(
            f"[verbose] symbol_index: {self.symbol_index.index_elapsed_seconds:.2f}s"
            if self.symbol_index.index_elapsed_seconds
            else "[verbose] symbol_index: 0.00s"
        )
        if self.symbol_index.errors:
            self._vprint(f"[verbose] symbol_index_errors: {self.symbol_index.errors}")
        if self.symbol_index.truncated:
            self._vprint("[verbose] symbol_index_truncated: true")
        symbol_count = sum(
            1 for line in self.repo_map.splitlines() if line.startswith("  ")
        )
        self._vprint(f"[verbose] repo_map_symbols: {symbol_count}")
        self._vprint(f"[verbose] repo_map_chars: {len(self.repo_map)}")

    def _find_related_tests(self, patched_files: list[str]) -> list[str]:
        related: list[str] = []
        for pf in patched_files:
            name = Path(pf).stem
            for tf in self.scan.test_files:
                tf_name = Path(tf).name
                if (
                    tf_name in (f"test_{name}.py", f"{name}_test.py")
                    and tf not in related
                ):
                    related.append(tf)
        return related

    def _extract_patched_files(self, diff_output: str) -> list[str]:
        files: list[str] = []
        for line in diff_output.splitlines():
            if line.startswith("diff --git"):
                parts = line.split()
                if len(parts) >= 4:
                    path = parts[3]
                    path = path.removeprefix("b/")
                    if path not in files:
                        files.append(path)
        return files

    def _run_verification(self, patched_files: list[str]) -> tuple[bool, str]:
        related = self._find_related_tests(patched_files)
        if related:
            self._vprint(f"[verbose] targeted tests: {', '.join(related)}")
            output = self.tools.run_targeted_tests(related)
        elif self.tools.test_command.strip():
            self._vprint(f"[verbose] test command: {self.tools.test_command}")
            output = self.tools.run_tests()
        else:
            self._vprint("[verbose] no tests configured, skipping verification")
            return True, "NO TESTS CONFIGURED"
        passed = "EXIT_CODE=0" in output
        return passed, output

    def _print_context_breakdown(self, system_prompt: str, user_prompt: str) -> None:
        self._vprint("[verbose] context breakdown:")
        self._vprint(f"[verbose]   repo_map_chars: {len(self.repo_map)}")
        self._vprint(
            f"[verbose]   active_code_chars: {self.working_context.active_code_chars}"
        )
        self._vprint(
            f"[verbose]   symbol_context_chars: {self.working_context.symbol_context_chars}"
        )
        self._vprint(
            f"[verbose]   full_file_context_chars: {self.working_context.full_file_context_chars}"
        )
        self._vprint(f"[verbose]   memory_chars: {self.working_context.memory_chars}")
        self._vprint(f"[verbose]   system_prompt_chars: {len(system_prompt)}")
        self._vprint(f"[verbose]   user_prompt_chars: {len(user_prompt)}")

    def _print_metrics(self, result: AgentResult) -> None:
        self._vprint("")
        self._vprint("[metrics]")
        self._vprint(f"llm_calls: {result.llm_calls}")
        self._vprint(f"tool_calls: {result.tool_calls}")
        self._vprint(f"files_read: {result.files_read}")
        self._vprint(f"searches: {result.searches}")
        self._vprint(f"patches: {result.patches}")
        self._vprint(f"tests: {result.tests_run}")
        if result.prompt_tokens:
            self._vprint(f"prompt_tokens: {result.prompt_tokens}")
        if result.completion_tokens:
            self._vprint(f"completion_tokens: {result.completion_tokens}")
        self._vprint(f"repo_map_chars: {result.repo_map_chars}")
        self._vprint(f"active_code_chars: {result.active_code_chars}")
        self._vprint(f"working_memory_chars: {result.working_memory_chars}")
        self._vprint(f"system_prompt_chars: {result.system_prompt_chars}")
        self._vprint(f"user_prompt_chars: {result.user_prompt_chars}")
        self._vprint(f"total_prompt_chars_sent: {result.total_prompt_chars_sent}")
        self._vprint(f"max_prompt_chars: {result.max_prompt_chars}")
        self._vprint(f"stop_reason: {result.stop_reason}")
        self._vprint(f"elapsed: {result.elapsed:.1f}s")
        self._vprint("")
        self._vprint("[symbol metrics]")
        self._vprint(f"indexed_symbols: {result.symbol_index_symbols}")
        self._vprint(f"symbol_reads: {result.symbol_reads}")
        self._vprint(f"full_file_reads: {result.full_file_reads}")
        self._vprint(f"symbol_context_chars: {result.symbol_context_chars}")
        self._vprint(f"full_file_context_chars: {result.full_file_context_chars}")

    def run(self, task: str) -> AgentResult:
        start_time = time.time()
        task_type = classify_task(task)
        self.repo_map = build_repo_map(
            self.repo,
            self.scan,
            symbol_index=self.symbol_index,
            task=task,
            max_chars=(
                MAX_REPO_MAP_CHARS_INSPECT
                if task_type == TaskType.INSPECT
                else MAX_REPO_MAP_CHARS
            ),
        )
        self._print_preflight()
        self._vprint("\n[verbose] === AGENT START ===")
        self._vprint(f"[verbose] repo: {self.repo}")
        self._vprint(f"[verbose] task: {task}")

        soft_limit, hard_limit = _BUDGETS[task_type]
        max_read = MAX_READ_ACTIONS[task_type]
        effective_hard_limit = min(hard_limit, self.max_iterations)
        code_budget = MAX_CODE_CONTEXT_CHARS.get(task_type, 8000)

        keywords = _extract_keywords(task)
        candidates = rank_symbols_for_task(task, self.symbol_index, limit=MAX_RELEVANT_SYMBOLS)
        if not candidates:
            candidates = self.symbol_index.find_candidates(keywords)[:MAX_RELEVANT_SYMBOLS]
        symbol_candidates_str = format_symbol_candidates(candidates[:MAX_RELEVANT_SYMBOLS])
        if candidates and self.verbose:
            self._vprint("[verbose] [relevant symbols]")
            for c in candidates[:MAX_RELEVANT_SYMBOLS]:
                self._vprint(
                    f"[verbose]   {c.path}::{c.name} L{c.start_line}-L{c.end_line}"
                )

        self._vprint(f"[verbose] task_type: {task_type.value.upper()}")
        self._vprint(f"[verbose] action_budget: {effective_hard_limit}")
        self._vprint(f"[verbose] code_budget: {code_budget}")

        state = AgentState.DISCOVER
        auto_read_content = ""
        auto_read_files: list[str] = []

        if task_type == TaskType.INSPECT:
            candidates = choose_inspection_files(self.scan)
            for f in candidates:
                content = self.tools.read_file(f)
                if len(auto_read_content) + len(content) > FAST_PATH_MAX_CHARS:
                    break
                auto_read_content += f"\n--- {f} ---\n{content}\n"
                auto_read_files.append(f)
                self.progress.add_auto_read(f)
                self._vprint(f"[verbose] auto-read: {f} ({len(content)} chars)")
            state = AgentState.READ
        else:
            state = AgentState.READ

        self._vprint(f"[verbose] initial_state: {state.value.upper()}")

        # --- experiment logging: run_start --- #
        git_commit, repo_dirty = capture_git_metadata(self.repo)
        self._log_event(
            "run_start",
            task=task,
            model=getattr(self.model, "model", "unknown"),
            harness_version=HARNESS_VERSION,
        )
        self._log_event(
            "symbol_index",
            files_parsed=self.symbol_index.files_parsed,
            symbols=len(self.symbol_index.symbols),
            errors=self.symbol_index.errors,
            truncated=self.symbol_index.truncated,
            duration_seconds=self.symbol_index.index_elapsed_seconds,
        )
        self._transition(AgentState.DISCOVER, state)

        llm_calls = 0
        tool_calls = len(auto_read_files)
        plan: dict[str, Any] | None = None
        patched_files: list[str] = []
        repair_cycles = 0
        test_failure = ""
        finish_summary = ""
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_prompt_chars = 0
        max_prompt_chars = 0
        repeated: list[str] = []
        consecutive_duplicates = 0
        last_system_prompt_chars = 0
        last_user_prompt_chars = 0
        stop_reason = ""
        budget_exhausted_logged = False
        budget_recovery_used = False
        symbol_reads = 0
        full_file_reads = 0
        symbol_context_chars = 0
        full_file_context_chars = 0

        max_loop = effective_hard_limit * 3 + 10
        loop_count = 0

        def duplicate_feedback(soft_msg: str) -> str:
            nonlocal consecutive_duplicates
            consecutive_duplicates += 1
            if consecutive_duplicates >= 2:
                return (
                    "ERROR: you repeated an action that was already rejected. "
                    "Do NOT repeat it again. You MUST answer now using the "
                    "context already loaded. Return exactly one action: "
                    '{"action": "finish", "summary": "your answer"}'
                )
            return soft_msg

        result: AgentResult | None = None

        try:
            while state not in (AgentState.DONE, AgentState.FAILED):
                loop_count += 1
                if loop_count > max_loop:
                    stop_reason = "max_iterations"
                    prev = state
                    state = AgentState.FAILED
                    self._transition(prev, state)
                    break

                if llm_calls >= effective_hard_limit:
                    stop_reason = "llm_call_budget_exhausted"
                    prev = state
                    state = AgentState.FAILED
                    self._transition(prev, state)
                    break

                if state == AgentState.VERIFY:
                    self._vprint("\n[verbose] --- verify ---")
                    self._vprint(f"[verbose] state: {state.value.upper()}")
                    test_start = time.time()
                    passed, test_output = self._run_verification(patched_files)
                    test_elapsed = time.time() - test_start
                    self.progress.record_test()
                    tool_calls += 1
                    verdict = "PASS" if passed else "FAIL"
                    self.working_context.add_fact(
                        f"verification: {verdict}\n{test_output[-2000:]}"
                    )
                    self._vprint(f"[verbose] verification: {verdict}")

                    test_data: dict[str, Any] = {
                        "success": passed,
                        "exit_code": 0 if passed else 1,
                        "duration_seconds": round(test_elapsed, 2),
                    }
                    if not passed:
                        test_data["failure_summary"] = test_output[
                            -MAX_FAILURE_SUMMARY_CHARS:
                        ]
                    self._log_event("test", **test_data)
                    self._log_event(
                        "tool_call",
                        action="run_tests",
                        state=state.value,
                        success=passed,
                    )

                    if passed:
                        stop_reason = "completed"
                        prev = state
                        state = AgentState.DONE
                        self._transition(prev, state)
                    else:
                        test_failure = test_output[-4000:]
                        if repair_cycles >= MAX_REPAIR_CYCLES:
                            stop_reason = "test_failure"
                            prev = state
                            state = AgentState.FAILED
                            self._transition(prev, state)
                        else:
                            prev = state
                            state = AgentState.REPAIR
                            self._transition(prev, state)
                    continue

                if state == AgentState.PLAN and plan is not None:
                    self._vprint("\n[verbose] --- plan (auto-transition) ---")
                    self._vprint("[verbose] state: PLAN -> EDIT")
                    prev = state
                    state = AgentState.EDIT
                    self._transition(prev, state)
                    continue

                # --- READ budget guard --- #
                reads_remaining = max_read - self.progress.read_actions
                budget_exhausted = (
                    reads_remaining <= 0
                    and state in (AgentState.READ, AgentState.DISCOVER, AgentState.REPAIR)
                )

                if budget_exhausted and not budget_exhausted_logged:
                    self._log_event(
                        "budget_exhausted",
                        budget="read_actions",
                        used=self.progress.read_actions,
                        limit=max_read,
                        state=state.value,
                    )
                    budget_exhausted_logged = True
                    self._vprint(
                        f"[verbose] READ BUDGET EXHAUSTED: "
                        f"{self.progress.read_actions}/{max_read} used"
                    )

                # Compute effective (filtered) actions
                allowed = set(ALLOWED_ACTIONS.get(state, set()))
                if budget_exhausted:
                    allowed -= _READ_ACTIONS
                    if task_type in (TaskType.INSPECT, TaskType.LOCATE) and state in (
                        AgentState.READ,
                        AgentState.DISCOVER,
                    ):
                            has_context = (
                                self.working_context.active_code_chars > 0
                                or len(self.progress.files_read) > 0
                            )
                            if not has_context:
                                stop_reason = "read_budget_exhausted"
                                prev = state
                                state = AgentState.FAILED
                                self._transition(prev, state)
                                break
                            allowed = {"finish"}

                llm_calls += 1
                self._vprint(
                    f"\n[verbose] --- iteration {llm_calls}/{effective_hard_limit} ---"
                )
                self._vprint(f"[verbose] state: {state.value.upper()}")

                allowed_str = ", ".join(sorted(allowed)) if allowed else "<deterministic>"
                self._vprint(f"[verbose] allowed_actions: {allowed_str}")

                system_prompt = get_system_prompt(
                    state.value,
                    budget_exhausted=budget_exhausted,
                    task_type=task_type.value,
                )
                user_prompt = build_state_prompt(
                    task=task,
                    repo_map=self.repo_map,
                    memory=self.memory,
                    tool_result=self.last_tool_result,
                    status=git_status(self.repo),
                    state=state.value,
                    auto_read=auto_read_content,
                    plan=plan,
                    test_failure=test_failure,
                    symbol_candidates=symbol_candidates_str,
                    budget_exhausted=budget_exhausted,
                    active_code=self.working_context.to_active_code_lines(),
                )

                if llm_calls >= soft_limit and not budget_exhausted:
                    user_prompt += BUDGET_WARNING

                self._print_context_breakdown(system_prompt, user_prompt)
                last_system_prompt_chars = len(system_prompt)
                last_user_prompt_chars = len(user_prompt)
                total_prompt_chars += len(user_prompt)
                max_prompt_chars = max(max_prompt_chars, len(user_prompt))

                llm_start = time.time()
                raw = self.model.chat(system_prompt, user_prompt)
                llm_latency = time.time() - llm_start

                usage = getattr(self.model, "last_usage", {})
                if isinstance(usage, dict):
                    total_prompt_tokens += int(usage.get("prompt_tokens", 0))
                    total_completion_tokens += int(usage.get("completion_tokens", 0))

                pt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
                ct = (
                    usage.get("completion_tokens")
                    if isinstance(usage, dict)
                    else None
                )

                # Per-llm-call context breakdown
                task_chars = len(task)
                repo_map_c = len(self.repo_map)
                active_code_c = self.working_context.active_code_chars
                working_mem_c = self.working_context.memory_chars
                relevant_sym_c = len(symbol_candidates_str)
                tool_schema_c = len(allowed_str)
                instructions_c = len(system_prompt)
                total_prompt_c = len(system_prompt) + len(user_prompt)
                other_c = max(
                    0,
                    total_prompt_c
                    - task_chars
                    - repo_map_c
                    - active_code_c
                    - working_mem_c
                    - relevant_sym_c
                    - tool_schema_c
                    - instructions_c,
                )
                self._log_event(
                    "llm_call",
                    call_index=llm_calls,
                    state=state.value,
                    prompt_chars=total_prompt_c,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    task_chars=task_chars,
                    repo_map_chars=repo_map_c,
                    active_code_chars=active_code_c,
                    working_memory_chars=working_mem_c,
                    relevant_symbols_chars=relevant_sym_c,
                    tool_schema_chars=tool_schema_c,
                    instructions_chars=instructions_c,
                    other_context_chars=other_c,
                    latency_seconds=round(llm_latency, 2),
                )

                action = extract_json_object(raw)
                kind = str(action.get("action", ""))

                self._vprint(f"[verbose] action: {kind}")

                signature = str(action)
                repeated.append(signature)
                if len(repeated) >= 3 and repeated[-1] == repeated[-2] == repeated[-3]:
                    msg = (
                        "ERROR: identical action repeated 3 times. Do NOT repeat it. "
                        "You MUST answer now using the context already loaded. "
                        'Return exactly one action: {"action": "finish", "summary": "your answer"}'
                    )
                    self.last_tool_result = msg
                    self._vprint(f"[verbose] WARNING: {msg}")
                    self.working_context.add_fact(msg)
                    continue

                # Validate action against effective (filtered) set
                if kind not in allowed:
                    # Budget-exhausted invalid read action
                    if budget_exhausted and kind in _READ_ACTIONS:
                        self._log_event(
                            "invalid_action",
                            action=kind,
                            reason="read_budget_exhausted",
                        )
                        self._vprint(
                            f"[verbose] invalid action {kind}: "
                            "read budget exhausted"
                        )
                        if (
                            task_type == TaskType.INSPECT
                            and not budget_recovery_used
                        ):
                            budget_recovery_used = True
                            self.last_tool_result = (
                                "READ BUDGET EXHAUSTED. "
                                "You must finish with existing context. "
                                "Do not request read_file or search_code."
                            )
                            continue
                        stop_reason = "invalid_action"
                        prev = state
                        state = AgentState.FAILED
                        self._transition(prev, state)
                        break

                    # Existing EDIT -> READ transition for read actions
                    if state == AgentState.EDIT and kind in ("read_file", "read_symbol"):
                        self._vprint(f"[verbose] {kind} in EDIT, transitioning to READ")
                        if validate_transition(state, AgentState.READ):
                            prev = state
                            state = AgentState.READ
                            self._transition(prev, state)
                        continue
                    error = f"ERROR: action {kind!r} is not allowed in state {state.value}. Allowed: {', '.join(sorted(allowed))}"
                    self._vprint(f"[verbose] {error}")
                    self.working_context.add_fact(error)
                    self.last_tool_result = error
                    self._log_event(
                        "invalid_action", action=kind, reason="not_allowed_in_state"
                    )
                    continue

                consecutive_duplicates = 0

                if kind == "finish":
                    finish_summary = str(action.get("summary", "Done."))
                    self._vprint(f"[verbose] FINISH: {finish_summary}")
                    self._log_event("finish", state=state.value, iterations=llm_calls)
                    stop_reason = "completed"
                    prev = state
                    state = AgentState.DONE
                    self._transition(prev, state)
                    break

                elif kind == "read_file":
                    path = str(action.get("path", ""))
                    start = int(action.get("start", 1))
                    end = int(action.get("end", 220))

                    if not self.progress.record_read(path):
                        msg = duplicate_feedback(
                            "The requested file/range was already read and has not changed. "
                            "Use existing context or choose another action."
                        )
                        self._vprint(f"[verbose] WARNING: duplicate read {path}")
                        self.working_context.add_fact(msg)
                        self._log_event("duplicate_action", action="read_file", path=path)
                        continue

                    self.progress.read_actions += 1
                    full_file_reads += 1
                    tool_output = self.tools.read_file(path, start, end)
                    tool_calls += 1
                    compact = tool_output[-4000:]
                    self.last_tool_result = compact
                    self.working_context.add_snippet(
                        f"read_file {path}", compact, budget=code_budget
                    )
                    full_file_context_chars += len(compact)
                    self._vprint(f"[verbose] read_file: {path} ({len(tool_output)} chars)")

                    self._log_event(
                        "tool_call",
                        action="read_file",
                        state=state.value,
                        success=True,
                    )
                    self._log_event("file_read", path=path, chars=len(tool_output))

                elif kind == "read_symbol":
                    path = str(action.get("path", ""))
                    symbol = str(action.get("symbol", ""))
                    sym_kind = action.get("kind")
                    sym_receiver = action.get("receiver")

                    if not self.progress.record_symbol_read(path, symbol):
                        msg = duplicate_feedback(
                            f"The symbol {symbol} in {path} was already read. "
                            "Use existing context or choose another action."
                        )
                        self._vprint(
                            f"[verbose] WARNING: duplicate symbol read {path}::{symbol}"
                        )
                        self.working_context.add_fact(msg)
                        self._log_event(
                            "duplicate_action", action="read_symbol", path=path, symbol=symbol
                        )
                        continue

                    self.progress.read_actions += 1
                    symbol_reads += 1
                    tool_output = self.tools.read_symbol(
                        path, symbol, kind=sym_kind, receiver=sym_receiver
                    )
                    tool_calls += 1
                    compact = tool_output[-4000:]
                    self.last_tool_result = compact
                    self.working_context.add_snippet(
                        f"read_symbol {path}::{symbol}",
                        compact,
                        budget=code_budget,
                        is_symbol=True,
                    )
                    symbol_context_chars += len(compact)
                    self._vprint(
                        f"[verbose] [read_symbol] {path}::{symbol}"
                        f" ({len(tool_output)} chars)"
                    )

                    matched = self.symbol_index.exact(path, symbol)
                    if sym_kind:
                        matched = [s for s in matched if s.kind == sym_kind]
                    if sym_receiver and matched:
                        matched = [
                            s for s in matched if s.receiver and s.receiver == sym_receiver
                        ]

                    self._log_event(
                        "symbol_read",
                        path=path,
                        symbol=symbol,
                        kind=matched[0].kind if matched else "",
                        start_line=matched[0].start_line if matched else 0,
                        end_line=matched[0].end_line if matched else 0,
                        chars=len(tool_output),
                        success=not tool_output.startswith("ERROR"),
                    )
                    self._log_event(
                        "tool_call",
                        action="read_symbol",
                        state=state.value,
                        path=path,
                        symbol=symbol,
                        success=True,
                    )

                elif kind == "search_code":
                    query = str(action.get("query", ""))
                    path = str(action.get("path", "."))

                    if not self.progress.record_search(query):
                        msg = duplicate_feedback(
                            "This search was already performed. "
                            "Use existing results or choose another action."
                        )
                        self._vprint(f"[verbose] WARNING: duplicate search {query}")
                        self.working_context.add_fact(msg)
                        self._log_event(
                            "duplicate_action", action="search_code", query=query
                        )
                        continue

                    tool_output = self.tools.search_code(query, path)
                    tool_calls += 1
                    compact = tool_output[-4000:]
                    self.last_tool_result = compact
                    self.working_context.add_snippet(
                        f"search_code {query}", compact, budget=code_budget
                    )
                    self._vprint(f"[verbose] search_code: {query} ({len(tool_output)} chars)")

                    match_count = len(
                        [l for l in tool_output.strip().splitlines() if l]
                    )
                    self._log_event(
                        "tool_call",
                        action="search_code",
                        state=state.value,
                        success=True,
                    )
                    self._log_event("search", query=query, matches=match_count)

                elif kind == "plan":
                    plan = action
                    plan_summary = str(action.get("summary", ""))
                    self._vprint(f"[verbose] plan: {plan_summary}")
                    self.working_context.add_fact(f"plan: {plan_summary}")
                    if validate_transition(state, AgentState.PLAN):
                        prev = state
                        state = AgentState.PLAN
                        self._transition(prev, state)

                elif kind == "apply_patch":
                    patch_text = str(action.get("patch", ""))
                    tool_output = self.tools.apply_patch(patch_text)
                    tool_calls += 1

                    if tool_output.startswith("PATCH APPLIED"):
                        self.progress.record_patch()
                        patched_files = self._extract_patched_files(tool_output)
                        compact = tool_output[-4000:]
                        self.last_tool_result = compact
                        self.working_context.add_snippet(
                            "apply_patch", compact, budget=code_budget
                        )
                        for pf in patched_files:
                            self.working_context.add_modified_file(pf)
                        self._vprint("[verbose] patch applied successfully")

                        self.symbol_index = reindex_files(
                            self.repo, self.symbol_index, patched_files
                        )
                        self.tools.symbol_index = self.symbol_index
                        self.repo_map = build_repo_map(
                            self.repo,
                            self.scan,
                            symbol_index=self.symbol_index,
                            task=task,
                            max_chars=(
                                MAX_REPO_MAP_CHARS_INSPECT
                                if task_type == TaskType.INSPECT
                                else MAX_REPO_MAP_CHARS
                            ),
                        )

                        added, removed = _count_diff_lines(tool_output)
                        self._log_event(
                            "tool_call",
                            action="apply_patch",
                            state=state.value,
                            success=True,
                        )
                        self._log_event(
                            "patch",
                            success=True,
                            files=patched_files,
                            added_lines=added,
                            removed_lines=removed,
                        )

                        if state == AgentState.REPAIR:
                            repair_cycles += 1
                        if validate_transition(state, AgentState.VERIFY):
                            prev = state
                            state = AgentState.VERIFY
                            self._transition(prev, state)
                    else:
                        compact = tool_output[-4000:]
                        self.last_tool_result = compact
                        self.working_context.add_fact(f"apply_patch FAILED\n{compact}")
                        self._vprint("[verbose] patch FAILED")

                        self._log_event(
                            "tool_call",
                            action="apply_patch",
                            state=state.value,
                            success=False,
                        )
                        self._log_event(
                            "patch",
                            success=False,
                            files=[],
                            added_lines=None,
                            removed_lines=None,
                        )

                        if (
                            state == AgentState.EDIT or state == AgentState.REPAIR
                        ) and validate_transition(state, AgentState.READ):
                            prev = state
                            state = AgentState.READ
                            self._transition(prev, state)

            elapsed = time.time() - start_time

            if state == AgentState.DONE:
                if finish_summary:
                    summary = finish_summary
                elif self.progress.patches_applied > 0:
                    summary = f"Changed {', '.join(patched_files)}."
                    if self.progress.tests_run > 0:
                        summary += " Tests pass."
                else:
                    summary = "Done."
            else:
                summary = f"Stopped after {llm_calls} LLM calls without completing."

            self._vprint(
                f"\n[verbose] {state.value.upper()} after {llm_calls} LLM call{'s' if llm_calls != 1 else ''}"
            )
            if stop_reason:
                self._vprint(f"[verbose] stop_reason: {stop_reason}")

            result = AgentResult(
                summary=summary,
                iterations=llm_calls,
                llm_calls=llm_calls,
                tool_calls=tool_calls,
                files_read=len(self.progress.files_read),
                searches=len(self.progress.searches),
                patches=self.progress.patches_applied,
                tests_run=self.progress.tests_run,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                elapsed=elapsed,
                task_type=task_type.value,
                final_state=state.value,
                stop_reason=stop_reason,
                repo_map_chars=len(self.repo_map),
                active_code_chars=self.working_context.active_code_chars,
                working_memory_chars=self.working_context.memory_chars,
                system_prompt_chars=last_system_prompt_chars,
                user_prompt_chars=last_user_prompt_chars,
                total_prompt_chars_sent=total_prompt_chars,
                max_prompt_chars=max_prompt_chars,
                symbol_index_files=self.symbol_index.files_parsed,
                symbol_index_symbols=len(self.symbol_index.symbols),
                symbol_index_errors=self.symbol_index.errors,
                symbol_index_truncated=self.symbol_index.truncated,
                symbol_index_elapsed_seconds=self.symbol_index.index_elapsed_seconds,
                symbol_reads=symbol_reads,
                full_file_reads=full_file_reads,
                symbol_context_chars=symbol_context_chars,
                full_file_context_chars=full_file_context_chars,
            )
            self._print_metrics(result)
            return result

        except Exception as exc:
            self._log_event(
                "error",
                error_type=type(exc).__name__,
                message=str(exc)[:MAX_ERROR_MESSAGE_CHARS],
            )
            raise

        finally:
            if result is not None:
                self._log_event(
                    "run_end",
                    success=(result.final_state == "done"),
                    final_state=result.final_state,
                    stop_reason=result.stop_reason,
                    elapsed_seconds=round(result.elapsed, 2),
                )
                self._save_experiment_summary(
                    result, task, task_type, git_commit, repo_dirty
                )
            else:
                elapsed = time.time() - start_time
                self._log_event(
                    "run_end",
                    success=False,
                    final_state="error",
                    stop_reason="exception",
                    elapsed_seconds=round(elapsed, 2),
                )


_BUDGETS = {
    TaskType.INSPECT: (2, 4),
    TaskType.LOCATE: (3, 5),
    TaskType.MODIFY: (5, 8),
    TaskType.DEBUG: (6, 10),
}
