from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .context import SYSTEM_PROMPT, build_repo_map, build_turn_prompt, git_status
from .model import OpenAICompatModel, extract_json_object
from .tools import RepoTools


@dataclass
class AgentResult:
    summary: str
    iterations: int


class CodingAgent:
    def __init__(self, repo: Path, model: OpenAICompatModel, test_command: str, max_iterations: int = 12):
        self.repo = repo.resolve()
        self.model = model
        self.tools = RepoTools(self.repo, test_command)
        self.max_iterations = max_iterations
        self.repo_map = build_repo_map(self.repo)
        self.memory: list[str] = []
        self.last_tool_result = ""

    def run(self, task: str) -> AgentResult:
        repeated: list[str] = []

        for iteration in range(1, self.max_iterations + 1):
            prompt = build_turn_prompt(
                task=task,
                repo_map=self.repo_map,
                memory=self.memory,
                tool_result=self.last_tool_result,
                status=git_status(self.repo),
            )
            raw = self.model.chat(SYSTEM_PROMPT, prompt)
            action = extract_json_object(raw)
            kind = str(action.get("action", ""))

            signature = str(action)
            repeated.append(signature)
            if len(repeated) >= 3 and repeated[-1] == repeated[-2] == repeated[-3]:
                self.last_tool_result = "ERROR: identical action repeated 3 times. Choose a different approach."
                self.memory.append(self.last_tool_result)
                continue

            if kind == "read_file":
                result = self.tools.read_file(
                    str(action.get("path", "")),
                    int(action.get("start", 1)),
                    int(action.get("end", 220)),
                )
            elif kind == "search_code":
                result = self.tools.search_code(
                    str(action.get("query", "")),
                    str(action.get("path", ".")),
                )
            elif kind == "apply_patch":
                result = self.tools.apply_patch(str(action.get("patch", "")))
                if result.startswith("PATCH APPLIED"):
                    verification = self.tools.run_tests()
                    result = result + "\n\nAUTO VERIFICATION\n" + verification
            elif kind == "run_tests":
                result = self.tools.run_tests()
            elif kind == "finish":
                summary = str(action.get("summary", "Done."))
                return AgentResult(summary=summary, iterations=iteration)
            else:
                result = f"ERROR: unsupported action: {kind!r}"

            compact = result[-4000:]
            self.last_tool_result = compact
            self.memory.append(f"iter {iteration}: {kind}\n{compact}")

        return AgentResult(
            summary=f"Stopped after {self.max_iterations} iterations without a finish action.",
            iterations=self.max_iterations,
        )
