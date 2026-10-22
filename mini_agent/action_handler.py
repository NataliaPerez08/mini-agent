from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .repo_map import MAX_REPO_MAP_CHARS, MAX_REPO_MAP_CHARS_INSPECT, build_repo_map
from .state import AgentState, TaskType
from .symbols import reindex_files, strip_receiver_prefix

if TYPE_CHECKING:
    from .agent import CodingAgent
    from .orchestrator import RunState


@dataclass
class ActionOutcome:
    """Result of executing one validated model action.

    new_state: suggested state-machine transition (validated by the
        orchestrator before being applied).
    finished: the run is complete and the loop should stop.
    """

    new_state: AgentState | None = None
    finished: bool = False


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


def _extract_patched_files(diff_output: str) -> list[str]:
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


class ActionHandler:
    """Executes validated model actions against repository tools.

    The orchestrator validates an action against the current state's
    allowed-action set and delegates here. Handlers mutate run state and
    the agent's shared structures; they never drive the loop themselves.
    """

    def __init__(
        self,
        agent: CodingAgent,
        run_state: RunState,
        log_event: Callable[..., None],
        vprint: Callable[..., None],
        code_budget: int,
        task: str,
        task_type: TaskType,
    ):
        self.agent = agent
        self.rs = run_state
        self._log_event = log_event
        self._vprint = vprint
        self.code_budget = code_budget
        self.task = task
        self.task_type = task_type

    def handle(
        self, kind: str, action: dict[str, Any], state: AgentState
    ) -> ActionOutcome:
        if kind == "finish":
            return self._finish(action, state)
        if kind == "read_file":
            return self._read_file(action, state)
        if kind == "read_symbol":
            return self._read_symbol(action, state)
        if kind == "search_code":
            return self._search_code(action, state)
        if kind == "plan":
            return self._plan(action, state)
        if kind == "apply_patch":
            return self._apply_patch(action, state)
        return ActionOutcome()

    def _duplicate_feedback(self, soft_msg: str) -> str:
        return self.rs.duplicate_feedback(soft_msg)

    def _finish(self, action: dict[str, Any], state: AgentState) -> ActionOutcome:
        if (
            self.task_type in (TaskType.MODIFY, TaskType.DEBUG)
            and self.rs.verification_failed
        ):
            self.rs.finish_summary = str(action.get("summary", "Done."))
            self._vprint(f"[verbose] FINISH after failed verification: {self.rs.finish_summary}")
            self._log_event("finish", state=state.value, iterations=self.rs.llm_calls)
            self.rs.stop_reason = "test_failure"
            return ActionOutcome(new_state=AgentState.FAILED, finished=True)
        if (
            self.task_type in (TaskType.MODIFY, TaskType.DEBUG)
            and self.agent.progress.patches_applied == 0
        ):
            if not self.rs.patchless_finish_rejected:
                self.rs.patchless_finish_rejected = True
                msg = (
                    "ERROR: this is a modification task and no patch has been "
                    "applied, so it is NOT complete. Do NOT finish. Your next "
                    'action MUST be a plan: {"action":"plan",'
                    '"files":["..."],"summary":"..."} using the context '
                    "already loaded. You will then apply the patch."
                )
                self._vprint("[verbose] WARNING: patch-less finish rejected")
                self.agent.last_tool_result = msg
                self.agent.working_context.add_fact(msg)
                self._log_event(
                    "patchless_finish", state=state.value, accepted=False
                )
                return ActionOutcome()
            self.rs.finish_summary = str(action.get("summary", "Done."))
            self._vprint(f"[verbose] FINISH: {self.rs.finish_summary}")
            self._log_event("finish", state=state.value, iterations=self.rs.llm_calls)
            self._log_event(
                "patchless_finish", state=state.value, accepted=True
            )
            self.rs.stop_reason = "completed_without_patch"
            return ActionOutcome(new_state=AgentState.DONE, finished=True)
        self.rs.finish_summary = str(action.get("summary", "Done."))
        self._vprint(f"[verbose] FINISH: {self.rs.finish_summary}")
        self._log_event("finish", state=state.value, iterations=self.rs.llm_calls)
        self.rs.stop_reason = "completed"
        return ActionOutcome(new_state=AgentState.DONE, finished=True)

    def _read_file(self, action: dict[str, Any], state: AgentState) -> ActionOutcome:
        path = str(action.get("path", ""))
        start = int(action.get("start", 1))
        end = int(action.get("end", 220))

        if not self.agent.progress.record_read(path):
            msg = self._duplicate_feedback(
                "The requested file/range was already read and has not changed. "
                "Use existing context or choose another action."
            )
            self._vprint(f"[verbose] WARNING: duplicate read {path}")
            self.agent.working_context.add_fact(msg)
            self._log_event("duplicate_action", action="read_file", path=path)
            return ActionOutcome()

        self.agent.progress.read_actions += 1
        self.rs.full_file_reads += 1
        tool_output = self.agent.tools.read_file(path, start, end)
        self.rs.tool_calls += 1
        compact = tool_output[-4000:]
        self.agent.last_tool_result = compact
        self.agent.working_context.add_snippet(
            f"read_file {path}", compact, budget=self.code_budget
        )
        self.rs.full_file_context_chars += len(compact)
        self._vprint(f"[verbose] read_file: {path} ({len(tool_output)} chars)")

        self._log_event(
            "tool_call",
            action="read_file",
            state=state.value,
            success=True,
        )
        self._log_event("file_read", path=path, chars=len(tool_output))
        return ActionOutcome()

    def _read_symbol(self, action: dict[str, Any], state: AgentState) -> ActionOutcome:
        path = str(action.get("path", ""))
        symbol = strip_receiver_prefix(str(action.get("symbol", "")))
        sym_kind = action.get("kind")
        sym_receiver = action.get("receiver")

        if not self.agent.progress.record_symbol_read(path, symbol):
            msg = self._duplicate_feedback(
                f"The symbol {symbol} in {path} was already read. "
                "Use existing context or choose another action."
            )
            self._vprint(
                f"[verbose] WARNING: duplicate symbol read {path}::{symbol}"
            )
            self.agent.working_context.add_fact(msg)
            self._log_event(
                "duplicate_action", action="read_symbol", path=path, symbol=symbol
            )
            return ActionOutcome()

        self.agent.progress.read_actions += 1
        self.rs.symbol_reads += 1
        tool_output = self.agent.tools.read_symbol(
            path, symbol, kind=sym_kind, receiver=sym_receiver
        )
        self.rs.tool_calls += 1
        compact = tool_output[-4000:]
        self.agent.last_tool_result = compact
        self.agent.working_context.add_snippet(
            f"read_symbol {path}::{symbol}",
            compact,
            budget=self.code_budget,
            is_symbol=True,
        )
        self.rs.symbol_context_chars += len(compact)
        self._vprint(
            f"[verbose] [read_symbol] {path}::{symbol}" f" ({len(tool_output)} chars)"
        )

        matched = self.agent.symbol_index.exact(path, symbol)
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
        return ActionOutcome()

    def _search_code(self, action: dict[str, Any], state: AgentState) -> ActionOutcome:
        query = str(action.get("query", ""))
        path = str(action.get("path", "."))

        if not self.agent.progress.record_search(query):
            msg = self._duplicate_feedback(
                "This search was already performed. "
                "Use existing results or choose another action."
            )
            self._vprint(f"[verbose] WARNING: duplicate search {query}")
            self.agent.working_context.add_fact(msg)
            self._log_event("duplicate_action", action="search_code", query=query)
            return ActionOutcome()

        tool_output = self.agent.tools.search_code(query, path)
        self.rs.tool_calls += 1
        compact = tool_output[-4000:]
        self.agent.last_tool_result = compact
        self.agent.working_context.add_snippet(
            f"search_code {query}", compact, budget=self.code_budget
        )
        self._vprint(f"[verbose] search_code: {query} ({len(tool_output)} chars)")

        match_count = len([l for l in tool_output.strip().splitlines() if l])
        self._log_event(
            "tool_call",
            action="search_code",
            state=state.value,
            success=True,
        )
        self._log_event("search", query=query, matches=match_count)
        return ActionOutcome()

    def _plan(self, action: dict[str, Any], state: AgentState) -> ActionOutcome:
        self.rs.plan = action
        plan_summary = str(action.get("summary", ""))
        self._vprint(f"[verbose] plan: {plan_summary}")
        self.agent.working_context.add_fact(f"plan: {plan_summary}")
        return ActionOutcome(new_state=AgentState.PLAN)

    def _apply_patch(self, action: dict[str, Any], state: AgentState) -> ActionOutcome:
        patch_text = str(action.get("patch") or "")
        if not patch_text.strip():
            msg = (
                "ERROR: apply_patch requires a non-empty 'patch' field "
                "containing a unified diff. Return the same action with "
                "the patch included."
            )
            self.agent.last_tool_result = msg
            self.agent.working_context.add_fact(msg)
            self._log_event(
                "invalid_action",
                action="apply_patch",
                state=state.value,
                reason="missing_patch_field",
            )
            return ActionOutcome()
        if patch_text.lstrip().startswith("@@"):
            target = ""
            files = action.get("files")
            if isinstance(files, list) and files:
                target = str(files[0])
            elif action.get("path"):
                target = str(action["path"])
            if target:
                patch_text = f"--- a/{target}\n+++ b/{target}\n" + patch_text.lstrip()
        tool_output = self.agent.tools.apply_patch(patch_text)
        self.rs.tool_calls += 1

        if tool_output.startswith("PATCH APPLIED"):
            self.agent.progress.record_patch()
            self.rs.patched_files = _extract_patched_files(tool_output)
            compact = tool_output[-4000:]
            self.agent.last_tool_result = compact
            self.agent.working_context.add_snippet(
                "apply_patch", compact, budget=self.code_budget
            )
            for pf in self.rs.patched_files:
                self.agent.working_context.add_modified_file(pf)
            self._vprint("[verbose] patch applied successfully")

            self.agent.symbol_index = reindex_files(
                self.agent.repo, self.agent.symbol_index, self.rs.patched_files
            )
            self.agent.tools.symbol_index = self.agent.symbol_index
            self.agent.repo_map = build_repo_map(
                self.agent.repo,
                self.agent.scan,
                symbol_index=self.agent.symbol_index,
                task=self.task,
                max_chars=(
                    MAX_REPO_MAP_CHARS_INSPECT
                    if self.task_type == TaskType.INSPECT
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
                files=self.rs.patched_files,
                added_lines=added,
                removed_lines=removed,
            )

            if state == AgentState.REPAIR:
                self.rs.repair_cycles += 1
            return ActionOutcome(new_state=AgentState.VERIFY)

        compact = tool_output[-4000:]
        added = [
            line[1:].strip()
            for line in patch_text.split("\n")
            if line.startswith("+")
            and not line.startswith("+++")
            and line[1:].strip()
        ]
        if added:
            diff_proc = subprocess.run(
                ["git", "diff"],
                cwd=self.agent.repo,
                text=True,
                capture_output=True,
                check=False,
            )
            if all(line in diff_proc.stdout for line in added):
                compact = (
                    "PATCH NOT APPLIED: this exact change is already "
                    "present in the working tree. Do NOT resend it. "
                    "Compare the TEST FAILURE with the ACTIVE CODE and "
                    "write a DIFFERENT patch.\n" + compact
                )
        self.agent.last_tool_result = compact
        self.agent.working_context.add_fact(f"apply_patch FAILED\n{compact}")
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

        if state in (AgentState.EDIT, AgentState.REPAIR):
            return ActionOutcome(new_state=AgentState.READ)
        return ActionOutcome()
