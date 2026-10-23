"""Deterministic name-level reference extraction (v5-lite).

Extracts call sites per file: which top-level symbol calls which name.
No type resolution, no LSP — a call to ``Validate(...)`` references the
*name* Validate. Consumers must treat references as a heuristic signal
(disambiguation tiebreaks, map annotations), not as ground truth.
"""

from __future__ import annotations

import ast
from pathlib import Path

MAX_REFERENCES_PER_NAME = 20
MAX_REFERENCE_FILE_BYTES = 200_000

# ponytail: name-level refs, no type/qualifier resolution; LSP (v4) is
# the upgrade path if name collisions make this too noisy.


def _py_file_references(source: str, rel: str) -> list[tuple[str, str]]:
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []

    refs: list[tuple[str, str]] = []

    def _callee_name(node: ast.Call) -> str | None:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def _collect(root: ast.AST, caller_key: str) -> None:
        for node in ast.walk(root):
            if isinstance(node, ast.Call):
                name = _callee_name(node)
                if name:
                    refs.append((name, caller_key))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node, f"{rel}::{node.name}")
        else:
            _collect(node, rel)
    return refs


def _go_file_references(src: bytes, rel: str) -> list[tuple[str, str]]:
    from .symbols import _go_available, _go_parser

    if not _go_available:
        return []
    try:
        tree = _go_parser.parse(src)
    except Exception:  # noqa: BLE001
        return []

    refs: list[tuple[str, str]] = []

    def _callee_name(node) -> str | None:
        # call_expression: function = identifier | selector_expression
        func = node.child_by_field_name("function")
        if func is None:
            return None
        if func.type == "identifier":
            return func.text.decode("utf-8", errors="replace")
        if func.type == "selector_expression":
            field = func.child_by_field_name("field")
            if field is not None:
                return field.text.decode("utf-8", errors="replace")
        return None

    def _walk(node, caller_key: str) -> None:
        for child in node.children:
            if child.type == "call_expression":
                name = _callee_name(child)
                if name:
                    refs.append((name, caller_key))
            _walk(child, caller_key)

    for node in tree.root_node.children:
        if node.type in ("function_declaration", "method_declaration"):
            name = None
            for child in node.children:
                if child.type == "identifier":
                    name = child.text.decode("utf-8", errors="replace")
                    break
                if child.type == "field_identifier":
                    name = child.text.decode("utf-8", errors="replace")
                    break
            if not name:
                continue
            body = node.child_by_field_name("body")
            if body is not None:
                _walk(body, f"{rel}::{name}")
        else:
            _walk(node, rel)
    return refs


def extract_file_references(path: Path, rel: str) -> list[tuple[str, str]]:
    """Return [(callee_name, caller_key)] for one file.

    caller_key is "rel::SymbolName" for calls inside a top-level symbol
    and "rel" for module-level calls.
    """
    try:
        if path.suffix.lower() == ".py":
            source = path.read_text(errors="replace")
            if len(source.encode("utf-8")) > MAX_REFERENCE_FILE_BYTES:
                return []
            return _py_file_references(source, rel)
        if path.suffix.lower() == ".go":
            src = path.read_bytes()
            if len(src) > MAX_REFERENCE_FILE_BYTES:
                return []
            return _go_file_references(src, rel)
    except OSError:
        return []
    return []


def aggregate_references(
    per_file: list[list[tuple[str, str]]]
) -> dict[str, list[str]]:
    """Aggregate per-file reference lists into name -> caller keys.

    Deduplicated, order-preserving, capped per name.
    """
    result: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for refs in per_file:
        for name, caller in refs:
            if caller not in seen.setdefault(name, set()):
                if len(seen[name]) >= MAX_REFERENCES_PER_NAME:
                    continue
                seen[name].add(caller)
                result.setdefault(name, []).append(caller)
    return result
