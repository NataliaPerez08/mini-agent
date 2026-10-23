from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .action_handler import ActionHandler
from .context import (
    build_state_prompt,
    format_symbol_candidates,
    get_system_prompt,
    git_status,
)
from .experiments import (
    MAX_ERROR_MESSAGE_CHARS,
    MAX_FAILURE_SUMMARY_CHARS,
    capture_git_metadata,
)
from .metrics_collector import (
    AgentResult,
    print_context_breakdown,
    print_metrics,
    print_preflight,
)
from .model import parse_model_output
from .repo_map import MAX_REPO_MAP_CHARS, MAX_REPO_MAP_CHARS_INSPECT, build_repo_map
from .state import (
    ALLOWED_ACTIONS,
    BUDGET_WARNING,
    FAST_PATH_MAX_CHARS,
    MAX_CODE_CONTEXT_CHARS,
    MAX_CONSECUTIVE_INVALID,
    MAX_READ_ACTIONS,
    MAX_RELEVANT_SYMBOLS,
    MAX_REPAIR_CYCLES,
    READ_ACTIONS,
    TASK_BUDGETS,
    AgentState,
    TaskType,
    choose_inspection_files,
    classify_task,
    validate_transition,
)
from .symbols import find_exact_task_symbols, rank_symbols_for_task
from .version import HARNESS_VERSION

if TYPE_CHECKING:
    from .agent import CodingAgent

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


@dataclass
class RunState:
    """Mutable state for a single agent run.

    Replaces the loop-local variables of the original monolithic run()
    so the orchestrator and action handlers can share it explicitly.
    """

    llm_calls: int = 0
    tool_calls: int = 0
    plan: dict[str, Any] | None = None
    patched_files: list[str] = field(default_factory=list)
    repair_cycles: int = 0
    test_failure: str = ""
    finish_summary: str = ""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_prompt_chars: int = 0
    max_prompt_chars: int = 0
    repeated: list[str] = field(default_factory=list)
    consecutive_duplicates: int = 0
    last_system_prompt_chars: int = 0
    last_user_prompt_chars: int = 0
    stop_reason: str = ""
    patchless_finish_rejected: bool = False
    verification_failed: bool = False
    consecutive_invalid: int = 0
    budget_exhausted_logged: bool = False
    budget_recovery_used: bool = False

    def record_repeat(self, entry: str) -> None:
        """Track the last actions for the 3x-identical check only."""
        self.repeated.append(entry)
        if len(self.repeated) > 3:
            del self.repeated[:-3]

    symbol_reads: int = 0
    full_file_reads: int = 0
    symbol_context_chars: int = 0
    full_file_context_chars: int = 0
    auto_read_content: str = ""
    auto_read_files: list[str] = field(default_factory=list)
    auto_symbols: list[str] = field(default_factory=list)

    def duplicate_feedback(self, soft_msg: str) -> str:
        self.consecutive_duplicates += 1
        if self.consecutive_duplicates >= 2:
            return (
                "ERROR: you repeated an action that was already rejected. "
                "Do NOT repeat it again. You MUST answer now using the "
                "context already loaded. Return exactly one action: "
                '{"action": "finish", "summary": "your answer"}'
            )
        return soft_msg


_FAILURE_LOCATION_RE = re.compile(r"([\w./-]+\.\w{1,4}):(\d+)")
_PYTEST_FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)')


def _parse_failure_locations(output: str) -> list[tuple[str, int]]:
    """Extract (path, line) pairs from test/compiler diagnostics.

    Covers go test failures (validator_test.go:22:), go compile errors
    (./pkg/file.go:68:5:), pytest short summaries (tests/x.py:15:) and
    pytest tracebacks (File "src/x.py", line 42). Non-path matches are
    rejected later by path resolution.
    """
    seen: set[tuple[str, int]] = set()
    locations: list[tuple[str, int]] = []
    pairs = _FAILURE_LOCATION_RE.findall(output) + _PYTEST_FILE_LINE_RE.findall(
        output
    )
    for path, line in pairs:
        entry = (path, int(line))
        if entry not in seen:
            seen.add(entry)
            locations.append(entry)
    return locations[:12]


class Orchestrator:
    """Drives the state machine for one task.

    Owns the main loop: budget guards, prompt construction, LLM calls,
    action validation, state transitions, and deterministic verification.
    Action execution is delegated to ActionHandler.
    """

    def __init__(self, agent: CodingAgent, task: str):
        self.agent = agent
        self.task = task

    def _find_related_tests(self, patched_files: list[str]) -> list[str]:
        related: list[str] = []
        for pf in patched_files:
            name = Path(pf).stem
            pf_dir = Path(pf).parent
            for tf in self.agent.scan.test_files:
                tf_path = Path(tf)
                tf_name = tf_path.name
                is_pytest = tf_name in (f"test_{name}.py", f"{name}_test.py")
                is_go = (
                    tf_name == f"{name}_test.go"
                    and tf_path.parent == pf_dir
                )
                if (is_pytest or is_go) and tf not in related:
                    related.append(tf)
        return related

    def _resolve_failure_path(self, raw: str, patched_files: list[str]) -> str:
        """Map a diagnostic path to a repo-relative file, or "".

        Handles the three observed shapes: repo-relative, module-root
        relative (go test run from a go.mod subdirectory), and bare
        basenames (go test prints package-relative paths). Paths that do
        not exist in the repository are rejected; existence is the noise
        filter for non-path colon-number pairs.
        """
        raw = raw.strip().removeprefix("./")
        files = self.agent.scan.files
        file_set = set(files)
        if raw in file_set:
            return raw
        for f in files:
            if Path(f).name == "go.mod":
                parent = str(Path(f).parent)
                if parent != ".":
                    candidate = f"{parent}/{raw}"
                    if candidate in file_set:
                        return candidate
        base = Path(raw).name
        matches = [f for f in files if Path(f).name == base]
        if len(matches) == 1:
            return matches[0]
        patched_dirs = {str(Path(pf).parent) for pf in patched_files}
        preferred = [m for m in matches if str(Path(m).parent) in patched_dirs]
        if len(preferred) == 1:
            return preferred[0]
        return ""

    def _load_diagnostic_symbols(
        self, output: str, rs: RunState, code_budget: int
    ) -> None:
        """Auto-load symbols named by file:line failure diagnostics.

        The failure output already says where the problem is; fetching
        that symbol costs no LLM call (v7 core loop, deterministic).
        Pre-loads do not consume the model's read-action budget.
        """
        agent = self.agent
        loaded = 0
        for raw, line in _parse_failure_locations(output):
            if loaded >= 3:
                break
            path = self._resolve_failure_path(raw, rs.patched_files)
            if not path:
                continue
            sym = agent.symbol_index.at_line(path, int(line))
            if sym is None:
                continue
            key = f"read_symbol {sym.path}::{sym.name}"
            if key in agent.working_context.symbol_snippets:
                continue
            content = agent.tools.read_symbol(sym.path, sym.name)
            if content.startswith("ERROR"):
                continue
            agent.progress.record_symbol_read(sym.path, sym.name)
            rs.symbol_reads += 1
            rs.symbol_context_chars += len(content)
            rs.auto_symbols.append(f"{sym.path}::{sym.name}")
            agent.working_context.add_snippet(
                key,
                content[-4000:],
                budget=code_budget,
                is_symbol=True,
            )
            rs.tool_calls += 1
            agent._vprint(
                f"[verbose] diagnostic-symbol: {sym.path}::{sym.name} "
                f"({len(content)} chars, from {raw}:{line})"
            )
            agent._log_event(
                "auto_symbol_read",
                path=sym.path,
                symbol=sym.name,
                kind=sym.kind,
                start_line=sym.start_line,
                end_line=sym.end_line,
                chars=len(content),
                reason="test_failure_diagnostic",
                diagnostic=f"{raw}:{line}",
            )
            loaded += 1

    def _run_verification(self, patched_files: list[str]) -> tuple[bool, str, bool]:
        """Run deterministic verification.

        Returns (passed, output, verified). verified is False only when no
        test command is configured: nothing executed, so a patch cannot be
        reported as successful.
        """
        if not self.agent.tools.test_command.strip():
            self.agent._vprint("[verbose] no tests configured, patch is unverified")
            return False, "NO TESTS CONFIGURED", False
        related = self._find_related_tests(patched_files)
        if related:
            self.agent._vprint(f"[verbose] targeted tests: {', '.join(related)}")
            output = self.agent.tools.run_targeted_tests(related)
        else:
            self.agent._vprint(f"[verbose] test command: {self.agent.tools.test_command}")
            output = self.agent.tools.run_tests()
        passed = output.split("\n", 1)[0] == "EXIT_CODE=0"
        return passed, output, True

    def _setup(self, task_type: TaskType) -> tuple[RunState, ActionHandler, dict[str, Any]]:
        """Preflight, budget computation, fast path, and run bookkeeping."""
        agent = self.agent
        agent._vprint("\n[verbose] === AGENT START ===")
        agent._vprint(f"[verbose] repo: {agent.repo}")
        agent._vprint(f"[verbose] task: {self.task}")

        soft_limit, hard_limit = TASK_BUDGETS[task_type]
        max_read = MAX_READ_ACTIONS[task_type]
        effective_hard_limit = min(hard_limit, agent.max_iterations)
        code_budget = MAX_CODE_CONTEXT_CHARS.get(task_type, 8000)

        keywords = _extract_keywords(self.task)
        candidates = rank_symbols_for_task(
            self.task, agent.symbol_index, limit=MAX_RELEVANT_SYMBOLS
        )
        if not candidates:
            candidates = agent.symbol_index.find_candidates(keywords)[
                :MAX_RELEVANT_SYMBOLS
            ]
        symbol_candidates_str = format_symbol_candidates(
            candidates[:MAX_RELEVANT_SYMBOLS]
        )
        if candidates and agent.verbose:
            agent._vprint("[verbose] [relevant symbols]")
            for c in candidates[:MAX_RELEVANT_SYMBOLS]:
                agent._vprint(
                    f"[verbose]   {c.path}::{c.name} L{c.start_line}-L{c.end_line}"
                )

        agent._vprint(f"[verbose] task_type: {task_type.value.upper()}")
        agent._vprint(f"[verbose] action_budget: {effective_hard_limit}")
        agent._vprint(f"[verbose] code_budget: {code_budget}")

        git_commit, repo_dirty = capture_git_metadata(agent.repo)
        agent._log_event(
            "run_start",
            task=self.task,
            model=getattr(agent.model, "model", "unknown"),
            harness_version=HARNESS_VERSION,
        )
        agent._log_event(
            "symbol_index",
            files_parsed=agent.symbol_index.files_parsed,
            symbols=len(agent.symbol_index.symbols),
            errors=agent.symbol_index.errors,
            truncated=agent.symbol_index.truncated,
            duration_seconds=agent.symbol_index.index_elapsed_seconds,
        )

        state = AgentState.READ
        rs = RunState()
        if task_type == TaskType.INSPECT:
            inspection_files = choose_inspection_files(agent.scan)
            for f in inspection_files:
                content = agent.tools.read_file(f)
                if len(rs.auto_read_content) + len(content) > FAST_PATH_MAX_CHARS:
                    break
                rs.auto_read_content += f"\n--- {f} ---\n{content}\n"
                rs.auto_read_files.append(f)
                agent.progress.add_auto_read(f)
                rs.tool_calls += 1
                agent._vprint(f"[verbose] auto-read: {f} ({len(content)} chars)")
                agent._log_event(
                    "auto_read",
                    path=f,
                    chars=len(content),
                    reason="inspect_fast_path",
                )
        elif task_type in (TaskType.MODIFY, TaskType.DEBUG):
            for target in find_exact_task_symbols(
                self.task, agent.symbol_index
            ):
                content = agent.tools.read_symbol(target.path, target.name)
                if content.startswith("ERROR"):
                    continue
                agent.progress.record_symbol_read(target.path, target.name)
                rs.symbol_reads += 1
                rs.symbol_context_chars += len(content)
                rs.auto_symbols.append(f"{target.path}::{target.name}")
                compact = content[-4000:]
                agent.last_tool_result = compact
                agent.working_context.add_snippet(
                    f"read_symbol {target.path}::{target.name}",
                    compact,
                    budget=code_budget,
                    is_symbol=True,
                )
                rs.tool_calls += 1
                agent._vprint(
                    f"[verbose] auto-symbol: {target.path}::{target.name} "
                    f"({len(content)} chars)"
                )
                agent._log_event(
                    "auto_symbol_read",
                    path=target.path,
                    symbol=target.name,
                    kind=target.kind,
                    start_line=target.start_line,
                    end_line=target.end_line,
                    chars=len(content),
                    reason="exact_task_symbol_match",
                )

        agent._vprint(f"[verbose] initial_state: {state.value.upper()}")

        focus_paths = [s.split("::")[0] for s in rs.auto_symbols]
        agent.repo_map = build_repo_map(
            agent.repo,
            agent.scan,
            symbol_index=agent.symbol_index,
            task=self.task,
            max_chars=(
                MAX_REPO_MAP_CHARS_INSPECT
                if task_type == TaskType.INSPECT
                else MAX_REPO_MAP_CHARS
            ),
            focus_files=focus_paths,
        )
        print_preflight(agent.scan, agent.symbol_index, agent.repo_map, agent._vprint)

        agent._transition(AgentState.DISCOVER, state)

        handler = ActionHandler(
            agent,
            rs,
            agent._log_event,
            agent._vprint,
            code_budget,
            self.task,
            task_type,
        )
        setup = {
            "state": state,
            "soft_limit": soft_limit,
            "effective_hard_limit": effective_hard_limit,
            "max_read": max_read,
            "symbol_candidates_str": symbol_candidates_str,
            "git_commit": git_commit,
            "repo_dirty": repo_dirty,
            "code_budget": code_budget,
        }
        return rs, handler, setup

    def run(self) -> AgentResult:
        agent = self.agent
        start_time = time.time()
        task_type = classify_task(self.task)

        rs, handler, setup = self._setup(task_type)
        state: AgentState = setup["state"]
        soft_limit: int = setup["soft_limit"]
        effective_hard_limit: int = setup["effective_hard_limit"]
        max_read: int = setup["max_read"]
        symbol_candidates_str: str = setup["symbol_candidates_str"]
        git_commit = setup["git_commit"]
        repo_dirty = setup["repo_dirty"]
        code_budget: int = setup["code_budget"]

        max_loop = effective_hard_limit * 3 + 10
        loop_count = 0

        result: AgentResult | None = None

        try:
            while state not in (AgentState.DONE, AgentState.FAILED):
                loop_count += 1
                if loop_count > max_loop:
                    rs.stop_reason = "max_iterations"
                    prev = state
                    state = AgentState.FAILED
                    agent._transition(prev, state)
                    break

                if rs.llm_calls >= effective_hard_limit:
                    rs.stop_reason = "llm_call_budget_exhausted"
                    prev = state
                    state = AgentState.FAILED
                    agent._transition(prev, state)
                    break

                if state == AgentState.VERIFY:
                    agent._vprint("\n[verbose] --- verify ---")
                    agent._vprint(f"[verbose] state: {state.value.upper()}")
                    test_start = time.time()
                    passed, test_output, verified = self._run_verification(
                        rs.patched_files
                    )
                    if not verified:
                        rs.stop_reason = "unverified_patch"
                        agent._log_event("test", success=False, status="unverified")
                        prev = state
                        state = AgentState.FAILED
                        agent._transition(prev, state)
                        continue
                    test_elapsed = time.time() - test_start
                    agent.progress.record_test()
                    rs.tool_calls += 1
                    verdict = "PASS" if passed else "FAIL"
                    agent.working_context.add_fact(
                        f"verification: {verdict}\n{test_output[-2000:]}"
                    )
                    agent._vprint(f"[verbose] verification: {verdict}")

                    test_data: dict[str, Any] = {
                        "success": passed,
                        "exit_code": 0 if passed else 1,
                        "duration_seconds": round(test_elapsed, 2),
                    }
                    if not passed:
                        test_data["failure_summary"] = test_output[
                            -MAX_FAILURE_SUMMARY_CHARS:
                        ]
                    agent._log_event("test", **test_data)
                    agent._log_event(
                        "tool_call",
                        action="run_tests",
                        state=state.value,
                        success=passed,
                    )

                    if passed:
                        rs.verification_failed = False
                        rs.stop_reason = "completed"
                        prev = state
                        state = AgentState.DONE
                        agent._transition(prev, state)
                    else:
                        rs.verification_failed = True
                        rs.test_failure = test_output[-4000:]
                        self._load_diagnostic_symbols(test_output, rs, code_budget)
                        if rs.repair_cycles >= MAX_REPAIR_CYCLES:
                            rs.stop_reason = "test_failure"
                            prev = state
                            state = AgentState.FAILED
                            agent._transition(prev, state)
                        else:
                            prev = state
                            state = AgentState.REPAIR
                            agent._transition(prev, state)
                    continue

                if state == AgentState.PLAN and rs.plan is not None:
                    agent._vprint("\n[verbose] --- plan (auto-transition) ---")
                    agent._vprint("[verbose] state: PLAN -> EDIT")
                    prev = state
                    state = AgentState.EDIT
                    agent._transition(prev, state)
                    continue

                # --- READ budget guard --- #
                reads_remaining = max_read - agent.progress.read_actions
                budget_exhausted = (
                    reads_remaining <= 0
                    and state
                    in (AgentState.READ, AgentState.DISCOVER, AgentState.REPAIR)
                )

                if budget_exhausted and not rs.budget_exhausted_logged:
                    agent._log_event(
                        "budget_exhausted",
                        budget="read_actions",
                        used=agent.progress.read_actions,
                        limit=max_read,
                        state=state.value,
                    )
                    rs.budget_exhausted_logged = True
                    agent._vprint(
                        f"[verbose] READ BUDGET EXHAUSTED: "
                        f"{agent.progress.read_actions}/{max_read} used"
                    )

                # Compute effective (filtered) actions
                allowed = set(ALLOWED_ACTIONS.get(state, set()))
                if budget_exhausted:
                    allowed -= READ_ACTIONS
                    if task_type in (TaskType.INSPECT, TaskType.LOCATE) and state in (
                        AgentState.READ,
                        AgentState.DISCOVER,
                    ):
                        has_context = (
                            agent.working_context.active_code_chars > 0
                            or len(agent.progress.files_read) > 0
                        )
                        if not has_context:
                            rs.stop_reason = "read_budget_exhausted"
                            prev = state
                            state = AgentState.FAILED
                            agent._transition(prev, state)
                            break
                        allowed = {"finish"}

                rs.llm_calls += 1
                agent._vprint(
                    f"\n[verbose] --- iteration {rs.llm_calls}/{effective_hard_limit} ---"
                )
                agent._vprint(f"[verbose] state: {state.value.upper()}")

                allowed_str = ", ".join(sorted(allowed)) if allowed else "<deterministic>"
                agent._vprint(f"[verbose] allowed_actions: {allowed_str}")

                system_prompt = get_system_prompt(
                    state.value,
                    budget_exhausted=budget_exhausted,
                    task_type=task_type.value,
                )
                user_prompt = build_state_prompt(
                    task=self.task,
                    repo_map=agent.repo_map,
                    memory=agent.memory,
                    tool_result=agent.last_tool_result,
                    status=git_status(agent.repo),
                    state=state.value,
                    auto_read=rs.auto_read_content,
                    plan=rs.plan,
                    test_failure=rs.test_failure,
                    symbol_candidates=symbol_candidates_str,
                    budget_exhausted=budget_exhausted,
                    active_code=agent.working_context.to_active_code_lines(),
                    task_type=task_type.value,
                )

                if rs.llm_calls >= soft_limit and not budget_exhausted:
                    user_prompt += BUDGET_WARNING

                print_context_breakdown(
                    agent.repo_map,
                    agent.working_context,
                    system_prompt,
                    user_prompt,
                    agent._vprint,
                )
                rs.last_system_prompt_chars = len(system_prompt)
                rs.last_user_prompt_chars = len(user_prompt)
                rs.total_prompt_chars += len(user_prompt)
                rs.max_prompt_chars = max(rs.max_prompt_chars, len(user_prompt))

                llm_start = time.time()
                raw = agent.model.chat(system_prompt, user_prompt)
                llm_latency = time.time() - llm_start

                usage = getattr(agent.model, "last_usage", {})
                if isinstance(usage, dict):
                    rs.total_prompt_tokens += int(usage.get("prompt_tokens", 0))
                    rs.total_completion_tokens += int(usage.get("completion_tokens", 0))

                pt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
                ct = (
                    usage.get("completion_tokens")
                    if isinstance(usage, dict)
                    else None
                )

                # Per-llm-call context breakdown
                task_chars = len(self.task)
                repo_map_c = len(agent.repo_map)
                active_code_c = agent.working_context.active_code_chars
                working_mem_c = agent.working_context.memory_chars
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
                agent._log_event(
                    "llm_call",
                    call_index=rs.llm_calls,
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

                action, output_error = parse_model_output(raw)
                if action is None:
                    kind = ""
                    msg = (
                        "ERROR: " + output_error + " "
                        "Return exactly one JSON object with the canonical "
                        "'action' field naming a single allowed action, e.g. "
                        '{"action": "finish", "summary": "..."}. Do NOT use '
                        "alternative field names such as next_action."
                    )
                    agent._vprint(f"[verbose] invalid model output: {output_error}")
                    agent.last_tool_result = msg
                    agent.working_context.add_fact(msg)
                    agent._log_event(
                        "invalid_action",
                        action=kind,
                        reason="model_output_contract",
                        detail=output_error,
                    )
                    rs.record_repeat(f"<invalid:{output_error}>")
                    rs.consecutive_invalid += 1
                    if rs.consecutive_invalid >= MAX_CONSECUTIVE_INVALID:
                        agent._log_event(
                            "budget_exhausted",
                            budget="consecutive_invalid",
                            used=rs.consecutive_invalid,
                            limit=MAX_CONSECUTIVE_INVALID,
                            state=state.value,
                        )
                        rs.stop_reason = "invalid_output"
                        prev = state
                        state = AgentState.FAILED
                        agent._transition(prev, state)
                        break
                    continue

                kind = str(action.get("action", ""))

                agent._vprint(f"[verbose] action: {kind}")

                rs.record_repeat(str(action))
                if len(rs.repeated) >= 3 and rs.repeated[-1] == rs.repeated[-2] == rs.repeated[-3]:
                    msg = (
                        "ERROR: identical action repeated 3 times. Do NOT repeat it. "
                        "You MUST answer now using the context already loaded. "
                        'Return exactly one action: {"action": "finish", "summary": "your answer"}'
                    )
                    agent.last_tool_result = msg
                    agent._vprint(f"[verbose] WARNING: {msg}")
                    agent.working_context.add_fact(msg)
                    continue

                # Validate action against effective (filtered) set
                if kind not in allowed:
                    # Budget-exhausted invalid read action
                    if budget_exhausted and kind in READ_ACTIONS:
                        agent._log_event(
                            "invalid_action",
                            action=kind,
                            reason="read_budget_exhausted",
                        )
                        agent._vprint(
                            f"[verbose] invalid action {kind}: "
                            "read budget exhausted"
                        )
                        if (
                            task_type == TaskType.INSPECT
                            and not rs.budget_recovery_used
                        ):
                            rs.budget_recovery_used = True
                            agent.last_tool_result = (
                                "READ BUDGET EXHAUSTED. "
                                "You must finish with existing context. "
                                "Do not request read_file or search_code."
                            )
                            continue
                        rs.stop_reason = "invalid_action"
                        prev = state
                        state = AgentState.FAILED
                        agent._transition(prev, state)
                        break

                    # Existing EDIT -> READ transition for read actions
                    if state == AgentState.EDIT and kind in ("read_file", "read_symbol"):
                        agent._vprint(f"[verbose] {kind} in EDIT, transitioning to READ")
                        if validate_transition(state, AgentState.READ):
                            prev = state
                            state = AgentState.READ
                            agent._transition(prev, state)
                        continue
                    error = f"ERROR: action {kind!r} is not allowed in state {state.value}. Allowed: {', '.join(sorted(allowed))}"
                    agent._vprint(f"[verbose] {error}")
                    agent.working_context.add_fact(error)
                    agent.last_tool_result = error
                    agent._log_event(
                        "invalid_action", action=kind, reason="not_allowed_in_state"
                    )
                    continue

                rs.consecutive_duplicates = 0
                rs.consecutive_invalid = 0

                outcome = handler.handle(kind, action, state)
                if outcome.new_state is not None and validate_transition(
                    state, outcome.new_state
                ):
                    agent._transition(state, outcome.new_state)
                    state = outcome.new_state
                if outcome.finished:
                    break

            elapsed = time.time() - start_time

            if state == AgentState.DONE:
                if rs.finish_summary:
                    summary = rs.finish_summary
                elif agent.progress.patches_applied > 0:
                    summary = f"Changed {', '.join(rs.patched_files)}."
                    if agent.progress.tests_run > 0:
                        summary += " Tests pass."
                else:
                    summary = "Done."
            else:
                summary = f"Stopped after {rs.llm_calls} LLM calls without completing."
                if rs.stop_reason == "unverified_patch":
                    summary = (
                        f"Changed {', '.join(rs.patched_files)} but no test command "
                        "is configured; the patch is unverified."
                    )

            agent._vprint(
                f"\n[verbose] {state.value.upper()} after {rs.llm_calls} LLM call{'s' if rs.llm_calls != 1 else ''}"
            )
            if rs.stop_reason:
                agent._vprint(f"[verbose] stop_reason: {rs.stop_reason}")

            result = AgentResult(
                summary=summary,
                iterations=rs.llm_calls,
                llm_calls=rs.llm_calls,
                tool_calls=rs.tool_calls,
                files_read=len(agent.progress.files_read),
                searches=len(agent.progress.searches),
                patches=agent.progress.patches_applied,
                tests_run=agent.progress.tests_run,
                prompt_tokens=rs.total_prompt_tokens,
                completion_tokens=rs.total_completion_tokens,
                elapsed=elapsed,
                task_type=task_type.value,
                final_state=state.value,
                stop_reason=rs.stop_reason,
                repo_map_chars=len(agent.repo_map),
                active_code_chars=agent.working_context.active_code_chars,
                working_memory_chars=agent.working_context.memory_chars,
                system_prompt_chars=rs.last_system_prompt_chars,
                user_prompt_chars=rs.last_user_prompt_chars,
                total_prompt_chars_sent=rs.total_prompt_chars,
                max_prompt_chars=rs.max_prompt_chars,
                symbol_index_files=agent.symbol_index.files_parsed,
                symbol_index_symbols=len(agent.symbol_index.symbols),
                symbol_index_errors=agent.symbol_index.errors,
                symbol_index_truncated=agent.symbol_index.truncated,
                symbol_index_elapsed_seconds=agent.symbol_index.index_elapsed_seconds,
                symbol_reads=rs.symbol_reads,
                full_file_reads=rs.full_file_reads,
                symbol_context_chars=rs.symbol_context_chars,
                full_file_context_chars=rs.full_file_context_chars,
                auto_read_files=rs.auto_read_files,
                auto_symbols=rs.auto_symbols,
            )
            print_metrics(result, agent._vprint)
            return result

        except Exception as exc:
            agent._log_event(
                "error",
                error_type=type(exc).__name__,
                message=str(exc)[:MAX_ERROR_MESSAGE_CHARS],
            )
            raise

        finally:
            if result is not None:
                agent._log_event(
                    "run_end",
                    success=(result.final_state == "done"),
                    final_state=result.final_state,
                    stop_reason=result.stop_reason,
                    elapsed_seconds=round(result.elapsed, 2),
                )
                agent._save_experiment_summary(
                    result, self.task, task_type, git_commit, repo_dirty
                )
            else:
                elapsed = time.time() - start_time
                agent._log_event(
                    "run_end",
                    success=False,
                    final_state="error",
                    stop_reason="exception",
                    elapsed_seconds=round(elapsed, 2),
                )
