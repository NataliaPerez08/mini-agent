from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from .scanner import RepoScan


@dataclass
class Symbol:
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    receiver: str = ""

    def display(self) -> str:
        if self.kind == "method" and self.receiver:
            return f"method ({self.receiver}) {self.name}() L{self.start_line}-L{self.end_line}"
        if self.kind == "function":
            return f"func {self.name}() L{self.start_line}-L{self.end_line}"
        if self.kind == "async_function":
            return f"async func {self.name}() L{self.start_line}-L{self.end_line}"
        if self.kind in ("struct", "interface", "class", "const", "var"):
            return f"{self.kind} {self.name} L{self.start_line}-L{self.end_line}"
        return f"{self.kind} {self.name} L{self.start_line}-L{self.end_line}"

    def compact(self) -> str:
        if self.kind == "method" and self.receiver:
            return (
                f"({self.receiver}) {self.name}() L{self.start_line}-L{self.end_line}"
            )
        if self.kind == "function":
            return f"{self.name}() L{self.start_line}-L{self.end_line}"
        if self.kind == "async_function":
            return f"{self.name}() L{self.start_line}-L{self.end_line}"
        return f"{self.name} {self.kind} L{self.start_line}-L{self.end_line}"


@dataclass
class SymbolIndex:
    symbols: list[Symbol] = field(default_factory=list)
    _by_name: dict[str, list[Symbol]] = field(default_factory=dict, repr=False)
    _by_file: dict[str, list[Symbol]] = field(default_factory=dict, repr=False)
    _finalized: bool = field(default=False, repr=False)

    def _ensure_indexed(self) -> None:
        if self._finalized:
            return
        for sym in self.symbols:
            self._by_name.setdefault(sym.name.lower(), []).append(sym)
            self._by_file.setdefault(sym.path, []).append(sym)
        self._finalized = True

    def find(self, query: str) -> list[Symbol]:
        self._ensure_indexed()
        q = query.lower().strip()
        if not q:
            return []
        exact = self._by_name.get(q, [])
        if exact:
            return list(exact)
        results: list[Symbol] = []
        for sym in self.symbols:
            if q in sym.name.lower():
                results.append(sym)
        return results

    def by_file(self, path: str) -> list[Symbol]:
        self._ensure_indexed()
        return list(self._by_file.get(path, []))

    def find_candidates(self, keywords: list[str]) -> list[Symbol]:
        self._ensure_indexed()
        seen: set[int] = set()
        results: list[Symbol] = []
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if not kw_lower or len(kw_lower) < 3:
                continue
            for sym in self.symbols:
                if id(sym) in seen:
                    continue
                if kw_lower in sym.name.lower():
                    seen.add(id(sym))
                    results.append(sym)
        return results


# ---------------------------------------------------------------------------
# Go extraction via tree-sitter
# ---------------------------------------------------------------------------

_go_parser = None
_go_available = False

try:
    import tree_sitter
    import tree_sitter_go

    _go_language = tree_sitter.Language(tree_sitter_go.language())
    _go_parser = tree_sitter.Parser(_go_language)
    _go_available = True
except Exception:  # noqa: BLE001
    _go_available = False


def _go_node_text(src: bytes, node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _go_receiver_type(src: bytes, method_node) -> str:
    for child in method_node.children:
        if child.type == "parameter_list":
            for param in child.children:
                if param.type == "parameter_declaration":
                    for ptype in param.children:
                        if ptype.type == "pointer_type":
                            inner = ""
                            for pt in ptype.children:
                                if pt.type == "type_identifier":
                                    inner = _go_node_text(src, pt)
                            return f"*{inner}"
                        if ptype.type == "type_identifier":
                            return _go_node_text(src, ptype)
    return ""


def _go_identifier_child(node) -> str | None:
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8", errors="replace") if child.text else None
    return None


def _go_field_identifier_child(node) -> str | None:
    for child in node.children:
        if child.type == "field_identifier":
            return child.text.decode("utf-8", errors="replace") if child.text else None
    return None


def extract_go_symbols(path: Path, rel: str, max_symbols: int = 30) -> list[Symbol]:
    if not _go_available:
        return []
    try:
        src_bytes = path.read_bytes()
    except OSError:
        return []
    try:
        tree = _go_parser.parse(src_bytes)
    except Exception:  # noqa: BLE001
        return []

    symbols: list[Symbol] = []

    def _add(sym: Symbol) -> bool:
        if len(symbols) >= max_symbols:
            return True
        symbols.append(sym)
        return False

    for node in tree.root_node.children:
        if len(symbols) >= max_symbols:
            break

        if node.type == "function_declaration":
            name = _go_identifier_child(node)
            if name and _add(
                Symbol(
                    name=name,
                    kind="function",
                    path=rel,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                )
            ):
                break

        elif node.type == "method_declaration":
            name = _go_field_identifier_child(node)
            if name:
                receiver = _go_receiver_type(src_bytes, node)
                if _add(
                    Symbol(
                        name=name,
                        kind="method",
                        path=rel,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        receiver=receiver,
                    )
                ):
                    break

        elif node.type == "type_declaration":
            for spec in node.children:
                if spec.type != "type_spec":
                    continue
                if len(symbols) >= max_symbols:
                    break
                type_name = None
                is_struct = False
                is_interface = False
                for child in spec.children:
                    if child.type == "type_identifier":
                        type_name = _go_node_text(src_bytes, child)
                    elif child.type == "struct_type":
                        is_struct = True
                    elif child.type == "interface_type":
                        is_interface = True
                if type_name and is_struct:
                    if _add(
                        Symbol(
                            name=type_name,
                            kind="struct",
                            path=rel,
                            start_line=spec.start_point[0] + 1,
                            end_line=spec.end_point[0] + 1,
                        )
                    ):
                        break
                elif (
                    type_name
                    and is_interface
                    and _add(
                        Symbol(
                            name=type_name,
                            kind="interface",
                            path=rel,
                            start_line=spec.start_point[0] + 1,
                            end_line=spec.end_point[0] + 1,
                        )
                    )
                ):
                    break

        elif node.type in ("const_declaration", "var_declaration"):
            kind = "const" if node.type == "const_declaration" else "var"
            for spec in node.children:
                if spec.type not in ("const_spec", "var_spec"):
                    continue
                if len(symbols) >= max_symbols:
                    break
                for child in spec.children:
                    if child.type == "identifier" and _add(
                        Symbol(
                            name=_go_node_text(src_bytes, child),
                            kind=kind,
                            path=rel,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        )
                    ):
                        break

    return symbols


# ---------------------------------------------------------------------------
# Python extraction via stdlib ast
# ---------------------------------------------------------------------------


def extract_python_symbols(path: Path, rel: str, max_symbols: int = 30) -> list[Symbol]:
    try:
        source = path.read_text(errors="replace")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    symbols: list[Symbol] = []
    for node in tree.body:
        if len(symbols) >= max_symbols:
            break
        if isinstance(node, ast.AsyncFunctionDef):
            symbols.append(
                Symbol(
                    name=node.name,
                    kind="async_function",
                    path=rel,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                )
            )
        elif isinstance(node, ast.FunctionDef):
            symbols.append(
                Symbol(
                    name=node.name,
                    kind="function",
                    path=rel,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                )
            )
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                Symbol(
                    name=node.name,
                    kind="class",
                    path=rel,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                )
            )
    return symbols


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------


def extract_symbols(path: Path, rel: str, max_symbols: int = 30) -> list[Symbol]:
    suffix = path.suffix.lower()
    if suffix == ".go":
        return extract_go_symbols(path, rel, max_symbols)
    if suffix == ".py":
        return extract_python_symbols(path, rel, max_symbols)
    return []


def build_symbol_index(
    repo: Path, scan: RepoScan, max_symbols_per_file: int = 30
) -> SymbolIndex:
    repo = repo.resolve()
    all_symbols: list[Symbol] = []
    for rel in scan.files:
        full = repo / rel
        suffix = full.suffix.lower()
        if suffix not in (".go", ".py"):
            continue
        try:
            syms = extract_symbols(full, rel, max_symbols=max_symbols_per_file)
        except Exception:  # noqa: BLE001
            syms = []
        all_symbols.extend(syms)
    return SymbolIndex(symbols=all_symbols)
