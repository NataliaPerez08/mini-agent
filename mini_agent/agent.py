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
from .repo_map import build_repo_map
from .scanner import scan_repository
from .state import (
    ALLOWED_ACTIONS,
    BUDGET_WARNING,
    FAST_PATH_MAX_CHARS,
    MAX_CODE_CONTEXT_CHARS,
    MAX_READ_ACTIONS,
    MAX_REPAIR_CYCLES,
    AgentState,
    Progress,
    TaskType,
    WorkingContext,
    choose_inspection_files,
    classify_task,
    validate_action,
    validate_transition,
)
from .symbols import build_symbol_index
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
    repo_map_chars: int = 0
    active_code_chars: int = 0
    working_memory_chars: int = 0
    system_prompt_chars: int = 0
    user_prompt_chars: int = 0


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
        self._vprint(f"[verbose] indexed_symbols: {len(self.symbol_index.symbols)}")
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
        self._vprint(f"elapsed: {result.elapsed:.1f}s")

    def run(self, task: str) -> AgentResult:
        start_time = time.time()
        self._print_preflight()
        self._vprint("\n[verbose] === AGENT START ===")
        self._vprint(f"[verbose] repo: {self.repo}")
        self._vprint(f"[verbose] task: {task}")

        task_type = classify_task(task)
        soft_limit, hard_limit = _BUDGETS[task_type]
        max_read = MAX_READ_ACTIONS[task_type]
        effective_hard_limit = min(hard_limit, self.max_iterations)
        code_budget = MAX_CODE_CONTEXT_CHARS.get(task_type, 8000)

        keywords = _extract_keywords(task)
        candidates = self.symbol_index.find_candidates(keywords)
        symbol_candidates_str = format_symbol_candidates(candidates[:10])
        if candidates and self.verbose:
            self._vprint(f"[verbose] symbol_candidates: {len(candidates)} found")
            for c in candidates[:10]:
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
        repeated: list[str] = []
        last_system_prompt_chars = 0
        last_user_prompt_chars = 0

        max_loop = effective_hard_limit * 3 + 10
        loop_count = 0

        result: AgentResult | None = None

        try:
            while state not in (AgentState.DONE, AgentState.FAILED):
                loop_count += 1
                if loop_count > max_loop:
                    prev = state
                    state = AgentState.FAILED
                    self._transition(prev, state)
                    break

                if llm_calls >= effective_hard_limit:
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
                        prev = state
                        state = AgentState.DONE
                        self._transition(prev, state)
                    else:
                        test_failure = test_output[-4000:]
                        if repair_cycles >= MAX_REPAIR_CYCLES:
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

                llm_calls += 1
                self._vprint(
                    f"\n[verbose] --- iteration {llm_calls}/{effective_hard_limit} ---"
                )
                self._vprint(f"[verbose] state: {state.value.upper()}")

                allowed = ALLOWED_ACTIONS.get(state, set())
                allowed_str = ", ".join(sorted(allowed)) if allowed else "<deterministic>"
                self._vprint(f"[verbose] allowed_actions: {allowed_str}")

                system_prompt = get_system_prompt(state.value)
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
                )

                if llm_calls >= soft_limit:
                    user_prompt += BUDGET_WARNING

                self._print_context_breakdown(system_prompt, user_prompt)
                last_system_prompt_chars = len(system_prompt)
                last_user_prompt_chars = len(user_prompt)

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
                self._log_event(
                    "llm_call",
                    call_index=llm_calls,
                    state=state.value,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    prompt_chars=len(user_prompt),
                    latency_seconds=round(llm_latency, 2),
                )

                action = extract_json_object(raw)
                kind = str(action.get("action", ""))

                self._vprint(f"[verbose] action: {kind}")

                signature = str(action)
                repeated.append(signature)
                if len(repeated) >= 3 and repeated[-1] == repeated[-2] == repeated[-3]:
                    msg = "ERROR: identical action repeated 3 times. Choose a different approach."
                    self.last_tool_result = msg
                    self._vprint(f"[verbose] WARNING: {msg}")
                    self.working_context.add_fact(msg)
                    continue

                if not validate_action(state, kind):
                    if state == AgentState.EDIT and kind in ("read_file", "read_symbol"):
                        self._vprint(f"[verbose] {kind} in EDIT, transitioning to READ")
                        if validate_transition(state, AgentState.READ):
                            prev = state
                            state = AgentState.READ
                            self._transition(prev, state)
                        continue
                    error = f"ERROR: action {kind!r} is not allowed in state {state.value}"
                    self._vprint(f"[verbose] {error}")
                    self.working_context.add_fact(error)
                    continue

                if kind == "finish":
                    finish_summary = str(action.get("summary", "Done."))
                    self._vprint(f"[verbose] FINISH: {finish_summary}")
                    self._log_event("finish", state=state.value, iterations=llm_calls)
                    prev = state
                    state = AgentState.DONE
                    self._transition(prev, state)
                    break

                elif kind == "read_file":
                    path = str(action.get("path", ""))
                    start = int(action.get("start", 1))
                    end = int(action.get("end", 220))

                    if self.progress.read_actions >= max_read:
                        msg = f"ERROR: max read actions ({max_read}) reached. Use existing context or finish."
                        self._vprint(f"[verbose] {msg}")
                        self.working_context.add_fact(msg)
                        continue

                    if not self.progress.record_read(path):
                        msg = (
                            "The requested file/range was already read and has not changed. "
                            "Use existing context or choose another action."
                        )
                        self._vprint(f"[verbose] WARNING: duplicate read {path}")
                        self.working_context.add_fact(msg)
                        continue

                    self.progress.read_actions += 1
                    result = self.tools.read_file(path, start, end)
                    tool_calls += 1
                    compact = result[-4000:]
                    self.last_tool_result = compact
                    self.working_context.add_snippet(
                        f"read_file {path}", compact, budget=code_budget
                    )
                    self._vprint(f"[verbose] read_file: {path} ({len(result)} chars)")

                    self._log_event(
                        "tool_call",
                        action="read_file",
                        state=state.value,
                        success=True,
                    )
                    self._log_event("file_read", path=path, chars=len(result))

                elif kind == "read_symbol":
                    path = str(action.get("path", ""))
                    symbol = str(action.get("symbol", ""))

                    if self.progress.read_actions >= max_read:
                        msg = f"ERROR: max read actions ({max_read}) reached. Use existing context or finish."
                        self._vprint(f"[verbose] {msg}")
                        self.working_context.add_fact(msg)
                        continue

                    if not self.progress.record_symbol_read(path, symbol):
                        msg = (
                            f"The symbol {symbol} in {path} was already read. "
                            "Use existing context or choose another action."
                        )
                        self._vprint(
                            f"[verbose] WARNING: duplicate symbol read {path}::{symbol}"
                        )
                        self.working_context.add_fact(msg)
                        continue

                    self.progress.read_actions += 1
                    result = self.tools.read_symbol(path, symbol)
                    tool_calls += 1
                    compact = result[-4000:]
                    self.last_tool_result = compact
                    self.working_context.add_snippet(
                        f"read_symbol {path}::{symbol}", compact, budget=code_budget
                    )
                    self._vprint(
                        f"[verbose] read_symbol: {path}::{symbol} ({len(result)} chars)"
                    )

                    self._log_event(
                        "tool_call",
                        action="read_symbol",
                        state=state.value,
                        success=True,
                    )
                    self._log_event("file_read", path=path, chars=len(result))

                elif kind == "search_code":
                    query = str(action.get("query", ""))
                    path = str(action.get("path", "."))

                    if not self.progress.record_search(query):
                        msg = (
                            "This search was already performed. "
                            "Use existing results or choose another action."
                        )
                        self._vprint(f"[verbose] WARNING: duplicate search {query}")
                        self.working_context.add_fact(msg)
                        continue

                    result = self.tools.search_code(query, path)
                    tool_calls += 1
                    compact = result[-4000:]
                    self.last_tool_result = compact
                    self.working_context.add_snippet(
                        f"search_code {query}", compact, budget=code_budget
                    )
                    self._vprint(f"[verbose] search_code: {query} ({len(result)} chars)")

                    match_count = len(
                        [l for l in result.strip().splitlines() if l]
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
                    result = self.tools.apply_patch(patch_text)
                    tool_calls += 1

                    if result.startswith("PATCH APPLIED"):
                        self.progress.record_patch()
                        patched_files = self._extract_patched_files(result)
                        compact = result[-4000:]
                        self.last_tool_result = compact
                        self.working_context.add_snippet(
                            "apply_patch", compact, budget=code_budget
                        )
                        for pf in patched_files:
                            self.working_context.add_modified_file(pf)
                        self._vprint("[verbose] patch applied successfully")

                        added, removed = _count_diff_lines(result)
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
                        compact = result[-4000:]
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
                repo_map_chars=len(self.repo_map),
                active_code_chars=self.working_context.active_code_chars,
                working_memory_chars=self.working_context.memory_chars,
                system_prompt_chars=last_system_prompt_chars,
                user_prompt_chars=last_user_prompt_chars,
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
                    elapsed_seconds=round(elapsed, 2),
                )


_BUDGETS = {
    TaskType.INSPECT: (2, 4),
    TaskType.LOCATE: (3, 5),
    TaskType.MODIFY: (5, 8),
    TaskType.DEBUG: (6, 10),
}
