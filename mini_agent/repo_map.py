from __future__ import annotations

import ast
from pathlib import Path

from .scanner import RepoScan
from .symbols import SymbolIndex, build_symbol_index

MAX_SYMBOLS_PER_FILE = 20
MAX_FILES_IN_MAP = 60


def extract_python_symbols(path: Path, max_symbols: int = 30) -> list[str]:
    try:
        source = path.read_text(errors="replace")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    symbols: list[str] = []
    for node in tree.body:
        if len(symbols) >= max_symbols:
            break
        if isinstance(node, ast.AsyncFunctionDef):
            symbols.append(f"async def {node.name}()")
        elif isinstance(node, ast.FunctionDef):
            symbols.append(f"def {node.name}()")
        elif isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
    return symbols


def _primary_language(scan: RepoScan) -> str | None:
    if not scan.languages:
        return None
    return next(iter(scan.languages))


def _prioritize_symbol_files(
    scan: RepoScan, indexed_paths: set[str], max_files: int
) -> list[str]:
    important = set(scan.important_files)
    tests = set(scan.test_files)
    ordered: list[str] = []
    for rel in sorted(indexed_paths):
        if rel in important:
            ordered.append(rel)
    for rel in sorted(indexed_paths):
        if rel not in important and rel not in tests and rel not in ordered:
            ordered.append(rel)
    for rel in sorted(indexed_paths):
        if rel in tests and rel not in ordered:
            ordered.append(rel)
    return ordered[:max_files]


def build_repo_map(
    repo: Path,
    scan: RepoScan,
    max_symbols_per_file: int = MAX_SYMBOLS_PER_FILE,
    symbol_index: SymbolIndex | None = None,
    max_files_in_map: int = MAX_FILES_IN_MAP,
) -> str:
    repo = repo.resolve()
    if symbol_index is None:
        symbol_index = build_symbol_index(
            repo, scan, max_symbols_per_file=max_symbols_per_file
        )

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

    if scan.files:
        sections.append("FILES\n" + "\n".join(scan.files))

    indexed_paths = set()
    for sym in symbol_index.symbols:
        indexed_paths.add(sym.path)
    prioritized = _prioritize_symbol_files(scan, indexed_paths, max_files_in_map)

    symbol_blocks: list[str] = []
    for rel in prioritized:
        file_syms = symbol_index.by_file(rel)
        file_syms_sorted = sorted(file_syms, key=lambda s: s.start_line)
        limited = file_syms_sorted[:max_symbols_per_file]
        if not limited:
            continue
        lines = [rel]
        for sym in limited:
            lines.append(f"  {sym.compact()}")
        symbol_blocks.append("\n".join(lines))
    if symbol_blocks:
        sections.append("SYMBOLS\n" + "\n".join(symbol_blocks))

    return "\n\n".join(sections)
