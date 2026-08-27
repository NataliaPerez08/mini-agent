from __future__ import annotations

import subprocess
from pathlib import Path


IGNORED = {".git", ".venv", "venv", "node_modules", "dist", "build", ".next", "target"}


def build_repo_map(repo: Path, max_files: int = 250) -> str:
    files: list[str] = []
    for p in repo.rglob("*"):
        if any(part in IGNORED for part in p.parts):
            continue
        if p.is_file():
            try:
                files.append(str(p.relative_to(repo)))
            except ValueError:
                continue
        if len(files) >= max_files:
            break
    return "\n".join(sorted(files))


def git_status(repo: Path) -> str:
    proc = subprocess.run(["git", "status", "--short"], cwd=repo, text=True, capture_output=True)
    return proc.stdout.strip() or "<clean>"


SYSTEM_PROMPT = r"""
You are a small-model coding agent operating inside one repository.

You MUST return exactly one JSON object and no commentary.
Choose exactly one action per turn.

Allowed actions:
1. {"action":"read_file","path":"...","start":1,"end":220}
2. {"action":"search_code","query":"...","path":"."}
3. {"action":"apply_patch","patch":"<valid unified diff>"}
4. {"action":"run_tests"}
5. {"action":"finish","summary":"..."}

Rules:
- Read before editing.
- Make the smallest change that solves the task.
- Never modify unrelated files.
- Prefer search before guessing filenames or symbols.
- Patches must be standard unified diffs accepted by `git apply`.
- Never claim success without test evidence when tests are configured.
- If a patch fails, inspect the file and produce a corrected patch.
- Do not emit markdown fences.
""".strip()


def build_turn_prompt(task: str, repo_map: str, memory: list[str], tool_result: str, status: str) -> str:
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
{tool_result or '<none>'}

Select the single best next action. Return JSON only.
"""
