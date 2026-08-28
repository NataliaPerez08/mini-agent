from __future__ import annotations

from pathlib import Path
from typing import Any

from .scanner import git_status as _scan_git_status
from .symbols import Symbol


def git_status(repo: Path) -> str:
    status = _scan_git_status(repo)
    return status or "<clean>"


SYSTEM_PROMPT = r"""
You are a small-model coding agent operating inside one repository.

You MUST return exactly one JSON object and no commentary.
Choose exactly one action per turn.

Allowed actions:
1. {"action":"read_symbol","path":"...","symbol":"..."}
2. {"action":"read_file","path":"...","start":1,"end":220}
3. {"action":"search_code","query":"...","path":"."}
4. {"action":"apply_patch","patch":"<valid unified diff>"}
5. {"action":"run_tests"}
6. {"action":"finish","summary":"..."}

Rules:
- Prefer read_symbol when a relevant symbol exists.
- Use read_file only for tiny files, config files, README, files without indexed symbols, or when surrounding file-level context is genuinely required.
- Read before editing.
- Make the smallest change that solves the task.
- Never modify unrelated files.
- Prefer search before guessing filenames or symbols.
- Patches must be standard unified diffs accepted by `git apply`.
- Never claim success without test evidence when tests are configured.
- If a patch fails, inspect the file and produce a corrected patch.
- Do not emit markdown fences.
""".strip()

_BASE = (
    "You are a small-model coding agent operating inside one repository.\n"
    "You MUST return exactly one JSON object and no commentary.\n"
    "Choose exactly one action per turn."
)

_READ_SYMBOL_HINT = (
    "\nPrefer read_symbol when a relevant symbol exists.\n"
    "Use read_file only for tiny files, config files, README, "
    "files without indexed symbols, or when surrounding file-level context is genuinely required."
)

SYSTEM_PROMPTS: dict[str, str] = {
    "discover_read": _BASE
    + """

Goal: Identify only the information required to answer the task.
Allowed actions:
  {"action":"read_symbol","path":"...","symbol":"..."}
  {"action":"read_file","path":"...","start":1,"end":220}
  {"action":"search_code","query":"...","path":"."}
  {"action":"finish","summary":"..."}
  {"action":"plan","files":["..."],"summary":"..."}
Do not edit.

Rules:
- Prefer read_symbol when a relevant symbol exists.
- Use read_file only for tiny files, config files, README, files without indexed symbols, or when surrounding file-level context is genuinely required.
- Read before editing.
- Prefer search before guessing filenames or symbols.
- Patches must be standard unified diffs accepted by `git apply`.
- Do not emit markdown fences.""",
    "plan": _BASE
    + """

Produce a minimal implementation plan.
Do not call tools.
Maximum 3 steps.

Return:
{"action":"plan","files":["..."],"summary":"..."}
Do not emit markdown fences.""",
    "edit": _BASE
    + """

Return only one patch action.
Do not explain.
Do not read unrelated files.

Return:
{"action":"apply_patch","patch":"<valid unified diff>"}
Do not emit markdown fences.""",
    "repair": _BASE
    + """

Tests failed.
Use the failure output and current diff to make the smallest corrective change.

Allowed actions:
  {"action":"apply_patch","patch":"<valid unified diff>"}
  {"action":"read_symbol","path":"...","symbol":"..."}
  {"action":"read_file","path":"...","start":1,"end":220}
Do not emit markdown fences.""",
}

_STATE_PROMPT_MAP: dict[str, str] = {
    "discover": "discover_read",
    "read": "discover_read",
    "plan": "plan",
    "edit": "edit",
    "repair": "repair",
}


def get_system_prompt(state: str) -> str:
    key = _STATE_PROMPT_MAP.get(state, "discover_read")
    return SYSTEM_PROMPTS[key]


def format_symbol_candidates(symbols: list[Symbol]) -> str:
    if not symbols:
        return ""
    lines: list[str] = []
    for sym in symbols:
        lines.append(f"{sym.path}::{sym.name} L{sym.start_line}-L{sym.end_line}")
    return "\n".join(lines)


def build_initial_context(task: str, repo_map: str, max_chars: int = 12000) -> str:
    instructions = (
        "Decide el siguiente paso usando la menor cantidad posible de lecturas."
    )
    head = f"TASK\n{task}\n\nREPOSITORY MAP\n"
    tail = f"\nINSTRUCTIONS\n{instructions}"
    budget = max_chars - len(head) - len(tail)
    budget = max(budget, 0)
    return head + repo_map[:budget] + tail


def build_turn_prompt(
    task: str, repo_map: str, memory: list[str], tool_result: str, status: str
) -> str:
    mem = "\n".join(memory[-8:]) or "<none>"
    return f"""TASK
{task}

REPOSITORY MAP
{repo_map}

WORKING MEMORY
{mem}

GIT STATUS
{status}

LAST TOOL RESULT
{tool_result or "<none>"}

Select the single best next action. Return JSON only.
"""


def build_state_prompt(
    task: str,
    repo_map: str,
    memory: list[str],
    tool_result: str,
    status: str,
    state: str,
    auto_read: str = "",
    plan: dict[str, Any] | None = None,
    test_failure: str = "",
    symbol_candidates: str = "",
) -> str:
    mem = "\n".join(memory[-8:]) or "<none>"

    if state in ("discover", "read"):
        sections = [f"TASK\n{task}", f"REPOSITORY MAP\n{repo_map}"]
        if symbol_candidates:
            sections.append(f"RELEVANT SYMBOL CANDIDATES\n{symbol_candidates}")
        if auto_read:
            sections.append(f"AUTO-READ CONTENT\n{auto_read}")
        sections.append(f"WORKING MEMORY\n{mem}")
        sections.append(f"GIT STATUS\n{status}")
        sections.append(f"LAST TOOL RESULT\n{tool_result or '<none>'}")
        sections.append("Select the single best next action. Return JSON only.")
        return "\n\n".join(sections)

    if state == "plan":
        sections = [
            f"TASK\n{task}",
            f"REPOSITORY MAP\n{repo_map}",
            f"WORKING MEMORY\n{mem}",
        ]
        if symbol_candidates:
            sections.append(f"RELEVANT SYMBOL CANDIDATES\n{symbol_candidates}")
        sections.append(
            "Produce a minimal implementation plan (max 3 steps). Return JSON only."
        )
        return "\n\n".join(sections)

    if state == "edit":
        plan_str = "<none>"
        if plan:
            files = ", ".join(plan.get("files", []))
            summary = plan.get("summary", "")
            plan_str = f"files: {files}\nsummary: {summary}"
        sections = [
            f"TASK\n{task}",
            f"PLAN\n{plan_str}",
            f"FILE CONTEXT\n{tool_result or '<none>'}",
            "Return only one apply_patch action. Return JSON only.",
        ]
        return "\n\n".join(sections)

    if state == "repair":
        plan_str = "<none>"
        if plan:
            files = ", ".join(plan.get("files", []))
            summary = plan.get("summary", "")
            plan_str = f"files: {files}\nsummary: {summary}"
        sections = [
            f"TASK\n{task}",
            f"PLAN\n{plan_str}",
            f"TEST FAILURE\n{test_failure or '<none>'}",
            f"LAST TOOL RESULT\n{tool_result or '<none>'}",
            "Make the smallest corrective change. Return JSON only.",
        ]
        return "\n\n".join(sections)

    return build_turn_prompt(task, repo_map, memory, tool_result, status)
