from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .scanner import RepoScan


class AgentState(str, Enum):
    DISCOVER = "discover"
    READ = "read"
    PLAN = "plan"
    EDIT = "edit"
    VERIFY = "verify"
    REPAIR = "repair"
    DONE = "done"
    FAILED = "failed"


class TaskType(str, Enum):
    INSPECT = "inspect"
    LOCATE = "locate"
    MODIFY = "modify"
    DEBUG = "debug"


ALLOWED_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.DISCOVER: {
        AgentState.READ,
        AgentState.PLAN,
        AgentState.DONE,
        AgentState.FAILED,
    },
    AgentState.READ: {
        AgentState.READ,
        AgentState.PLAN,
        AgentState.EDIT,
        AgentState.DONE,
        AgentState.FAILED,
    },
    AgentState.PLAN: {AgentState.EDIT, AgentState.READ, AgentState.FAILED},
    AgentState.EDIT: {
        AgentState.EDIT,
        AgentState.VERIFY,
        AgentState.READ,
        AgentState.FAILED,
    },
    AgentState.VERIFY: {AgentState.DONE, AgentState.REPAIR, AgentState.FAILED},
    AgentState.REPAIR: {
        AgentState.REPAIR,
        AgentState.EDIT,
        AgentState.READ,
        AgentState.VERIFY,
        AgentState.FAILED,
    },
    AgentState.DONE: set(),
    AgentState.FAILED: set(),
}

ALLOWED_ACTIONS: dict[AgentState, set[str]] = {
    AgentState.DISCOVER: {"read_file", "read_symbol", "search_code", "finish"},
    AgentState.READ: {"read_file", "read_symbol", "search_code", "finish", "plan"},
    AgentState.PLAN: {"plan"},
    AgentState.EDIT: {"apply_patch"},
    AgentState.VERIFY: set(),
    AgentState.REPAIR: {"read_file", "read_symbol", "apply_patch"},
    AgentState.DONE: set(),
    AgentState.FAILED: set(),
}

TASK_BUDGETS: dict[TaskType, tuple[int, int]] = {
    TaskType.INSPECT: (2, 4),
    TaskType.LOCATE: (3, 5),
    TaskType.MODIFY: (5, 8),
    TaskType.DEBUG: (6, 10),
}

MAX_READ_ACTIONS: dict[TaskType, int] = {
    TaskType.INSPECT: 2,
    TaskType.LOCATE: 3,
    TaskType.MODIFY: 3,
    TaskType.DEBUG: 4,
}

MAX_REPAIR_CYCLES = 2
MAX_GLOBAL_ITERATIONS = 12
GENERATE_FINAL_SUMMARY = False

FAST_PATH_MAX_FILES = 2
FAST_PATH_MAX_CHARS = 6000

MAX_RELEVANT_SYMBOLS = 8

MAX_CODE_CONTEXT_CHARS: dict[TaskType, int] = {
    TaskType.INSPECT: 4000,
    TaskType.LOCATE: 5000,
    TaskType.MODIFY: 8000,
    TaskType.DEBUG: 10000,
}

PRIORITY_FILES = [
    "README.md",
    "README",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
]

BUDGET_WARNING = (
    "\n\nWARNING: You are approaching the action budget. "
    "Finish if the task is sufficiently resolved. "
    "Only request additional information if strictly necessary."
)

_MODIFY_KEYWORDS = {
    "corrige",
    "modifica",
    "cambia",
    "implementa",
    "anade",
    "agrega",
    "elimina",
    "fix",
    "change",
    "implement",
    "add",
    "remove",
}

_DEBUG_KEYWORDS = {
    "bug",
    "error",
    "falla",
    "failing",
    "exception",
    "debug",
}

_LOCATE_KEYWORDS = {
    "donde",
    "encuentra",
    "localiza",
    "where",
    "find",
    "locate",
}


def classify_task(task: str) -> TaskType:
    lower = task.lower()
    for kw in _MODIFY_KEYWORDS:
        if kw in lower:
            return TaskType.MODIFY
    for kw in _DEBUG_KEYWORDS:
        if kw in lower:
            return TaskType.DEBUG
    for kw in _LOCATE_KEYWORDS:
        if kw in lower:
            return TaskType.LOCATE
    return TaskType.INSPECT


def choose_inspection_files(scan: RepoScan) -> list[str]:
    file_set = set(scan.files)
    candidates: list[str] = []
    for priority in PRIORITY_FILES:
        if priority in file_set and len(candidates) < FAST_PATH_MAX_FILES:
            candidates.append(priority)
    for f in scan.important_files:
        if f not in candidates and len(candidates) < FAST_PATH_MAX_FILES:
            candidates.append(f)
    return candidates


def validate_transition(current: AgentState, target: AgentState) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def validate_action(state: AgentState, action: str) -> bool:
    return action in ALLOWED_ACTIONS.get(state, set())


@dataclass
class Progress:
    files_read: set[str] = field(default_factory=set)
    searches: set[str] = field(default_factory=set)
    patches_applied: int = 0
    tests_run: int = 0
    last_actions: list[str] = field(default_factory=list)
    read_actions: int = 0
    symbols_read: set[str] = field(default_factory=set)

    def record_read(self, path: str) -> bool:
        if path in self.files_read:
            return False
        self.files_read.add(path)
        self.last_actions.append(f"read_file:{path}")
        return True

    def record_symbol_read(self, path: str, symbol: str) -> bool:
        key = f"{path}::{symbol}"
        if key in self.symbols_read:
            return False
        self.symbols_read.add(key)
        self.files_read.add(path)
        self.last_actions.append(f"read_symbol:{key}")
        return True

    def record_search(self, query: str) -> bool:
        key = query.lower()
        if key in self.searches:
            return False
        self.searches.add(key)
        self.last_actions.append(f"search_code:{query}")
        return True

    def record_patch(self) -> None:
        self.patches_applied += 1
        self.last_actions.append("apply_patch")

    def record_test(self) -> None:
        self.tests_run += 1
        self.last_actions.append("run_tests")

    def add_auto_read(self, path: str) -> None:
        self.files_read.add(path)


@dataclass
class WorkingContext:
    active_snippets: dict[str, str] = field(default_factory=dict)
    symbol_snippets: dict[str, str] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    test_failures: list[str] = field(default_factory=list)

    @property
    def active_code_chars(self) -> int:
        return sum(len(v) for v in self.active_snippets.values()) + sum(
            len(v) for v in self.symbol_snippets.values()
        )

    @property
    def symbol_context_chars(self) -> int:
        return sum(len(v) for v in self.symbol_snippets.values())

    @property
    def full_file_context_chars(self) -> int:
        return sum(len(v) for v in self.active_snippets.values())

    @property
    def memory_chars(self) -> int:
        return (
            sum(len(f) for f in self.facts)
            + sum(len(t) for t in self.test_failures)
        )

    def add_snippet(
        self, key: str, content: str, budget: int = 8000, is_symbol: bool = False
    ) -> None:
        if is_symbol:
            self.symbol_snippets[key] = content
        else:
            self.active_snippets[key] = content
        self._prune(budget)

    def add_fact(self, fact: str) -> None:
        self.facts.append(fact)
        if len(self.facts) > 12:
            self.facts = self.facts[-12:]

    def add_test_failure(self, failure: str) -> None:
        self.test_failures.append(failure[-2000:])
        if len(self.test_failures) > 3:
            self.test_failures = self.test_failures[-3:]

    def add_modified_file(self, path: str) -> None:
        if path not in self.modified_files:
            self.modified_files.append(path)

    def _prune(self, budget: int) -> None:
        while self.active_code_chars > budget and self.active_snippets:
            oldest_key = next(iter(self.active_snippets))
            content = self.active_snippets.pop(oldest_key)
            self.facts.append(f"[pruned {oldest_key}: {len(content)} chars]")
        while self.active_code_chars > budget and self.symbol_snippets:
            oldest_key = next(iter(self.symbol_snippets))
            content = self.symbol_snippets.pop(oldest_key)
            self.facts.append(f"[pruned {oldest_key}: {len(content)} chars]")

    def to_memory_lines(self) -> list[str]:
        return list(self.facts)

    def to_active_code_lines(self) -> list[str]:
        lines: list[str] = []
        for key, content in self.active_snippets.items():
            lines.append(f"{key}\n{content}")
        for key, content in self.symbol_snippets.items():
            lines.append(f"{key}\n{content}")
        return lines
