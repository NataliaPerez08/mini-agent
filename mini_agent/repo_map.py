from __future__ import annotations

from pathlib import Path

from .scanner import RepoScan
from .symbols import SymbolIndex, _tokenize_task, build_symbol_index

MAX_SYMBOLS_PER_FILE = 20
MAX_SYMBOLS_IN_REPO_MAP_PER_FILE = 20
MAX_FILES_IN_MAP = 60
MAX_FOCUSED_FILES_IN_MAP = 10
MAX_REPO_MAP_CHARS = 5000
MAX_REPO_MAP_CHARS_INSPECT = 2500
MAX_FILES_SECTION_CHARS = 1200


def _primary_language(scan: RepoScan) -> str | None:
    if not scan.languages:
        return None
    return next(iter(scan.languages))


def _file_priority(rel: str, tokens: list[str]) -> int:
    path_lower = rel.lower()
    if any(tok in path_lower for tok in tokens):
        return 0
    return 4


def _focused_sort_key(
    focus: set[str],
    focus_dirs: set[str],
    tokens: list[str],
    important: set[str],
    tests: set[str],
):
    """Tier order for a focused (localized MODIFY/DEBUG) map.

    Focus files first, then their directories, then the unfocused
    tiers (task-token match, important, non-test, test).
    """

    def sort_key(rel: str) -> tuple[int, str]:
        if rel in focus:
            base = 0
        elif str(Path(rel).parent) in focus_dirs:
            base = 1
        elif _file_priority(rel, tokens) == 0:
            base = 2
        elif rel in important:
            base = 3
        elif rel not in tests:
            base = 4
        else:
            base = 5
        return (base, rel)

    return sort_key


def _prioritize_symbol_files(
    scan: RepoScan,
    indexed_paths: set[str],
    max_files: int,
    task_tokens: list[str] | None = None,
    focus: set[str] | None = None,
    focus_dirs: set[str] | None = None,
) -> list[str]:
    tokens = task_tokens or []
    important = set(scan.important_files)
    tests = set(scan.test_files)

    if focus:
        key = _focused_sort_key(focus, focus_dirs or set(), tokens, important, tests)
        return sorted(indexed_paths, key=key)[:max_files]

    def sort_key(rel: str) -> tuple[int, str]:
        if _file_priority(rel, tokens) == 0:
            base = 0
        elif rel in important:
            base = 1
        elif rel not in tests:
            base = 2
        else:
            base = 3
        return (base, rel)

    return sorted(indexed_paths, key=sort_key)[:max_files]


def _fit_files_section(
    scan: RepoScan, budget: int, ordered: list[str] | None = None
) -> str | None:
    files = ordered if ordered is not None else scan.files
    if not files or budget <= len("FILES\n"):
        return None
    lines: list[str] = ["FILES"]
    used = len(lines[0])
    included = 0
    for rel in files:
        cost = len(rel) + 1
        if used + cost > budget:
            break
        lines.append(rel)
        used += cost
        included += 1
    if included < len(files):
        lines.append(f"... ({len(files) - included} more files)")
    return "\n".join(lines)


def build_repo_map(
    repo: Path,
    scan: RepoScan,
    max_symbols_per_file: int = MAX_SYMBOLS_PER_FILE,
    symbol_index: SymbolIndex | None = None,
    max_files_in_map: int = MAX_FILES_IN_MAP,
    task: str = "",
    max_chars: int = MAX_REPO_MAP_CHARS,
    focus_files: list[str] | None = None,
) -> str:
    repo = repo.resolve()
    if symbol_index is None:
        symbol_index = build_symbol_index(
            repo, scan, max_symbols_per_file=max_symbols_per_file
        )

    task_tokens = _tokenize_task(task) if task else []
    focus = {f for f in (focus_files or []) if f}
    focus_dirs = {str(Path(f).parent) for f in focus}

    sections: list[str] = []

    project_lines: list[str] = ["PROJECT"]
    primary = _primary_language(scan)
    if primary:
        project_lines.append(f"Primary language: {primary}")
    project_lines.append(f"Files: {len(scan.files)}")
    if scan.test_files:
        project_lines.append(f"Tests: {len(scan.test_files)}")
    sections.append("\n".join(project_lines))

    if scan.important_files:
        sections.append("IMPORTANT FILES\n" + "\n".join(scan.important_files))

    used = sum(len(s) + 2 for s in sections)

    files_budget = min(max_chars - used - 300, MAX_FILES_SECTION_CHARS)
    if scan.files:
        ordered: list[str] | None = None
        if focus:
            ordered = sorted(
                scan.files,
                key=_focused_sort_key(
                    focus,
                    focus_dirs,
                    task_tokens,
                    set(scan.important_files),
                    set(scan.test_files),
                ),
            )
        files_section = _fit_files_section(scan, files_budget, ordered=ordered)
        if files_section:
            sections.append(files_section)
            used = sum(len(s) + 2 for s in sections)

    indexed_paths = set()
    for sym in symbol_index.symbols:
        indexed_paths.add(sym.path)
    max_files = MAX_FOCUSED_FILES_IN_MAP if focus else max_files_in_map
    prioritized = _prioritize_symbol_files(
        scan,
        indexed_paths,
        max_files,
        task_tokens=task_tokens,
        focus=focus or None,
        focus_dirs=focus_dirs,
    )

    symbol_blocks: list[str] = []
    for rel in prioritized:
        block_budget = max_chars - used - len("SYMBOLS\n") - 2
        if block_budget <= 0:
            break
        file_syms = symbol_index.by_file(rel)
        file_syms_sorted = sorted(file_syms, key=lambda s: s.start_line)
        limited = file_syms_sorted[:max_symbols_per_file]
        if not limited:
            continue
        lines = [rel]
        block_used = len(rel) + 1
        for sym in limited:
            line = f"  {sym.compact()}"
            if block_used + len(line) + 1 > block_budget:
                break
            lines.append(line)
            block_used += len(line) + 1
        if len(lines) <= 1:
            continue
        block = "\n".join(lines)
        symbol_blocks.append(block)
        used += len(block) + 2
    if symbol_blocks:
        sections.append("SYMBOLS\n" + "\n".join(symbol_blocks))

    return "\n\n".join(sections)
