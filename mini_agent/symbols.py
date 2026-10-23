from __future__ import annotations

import ast
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .references import aggregate_references, extract_file_references
from .scanner import RepoScan

MAX_SIGNATURE_CHARS = 300
MAX_SYMBOL_FILE_BYTES = 1_000_000
MAX_SYMBOLS_TOTAL = 5000
MAX_SYMBOLS_PER_FILE = 100

_RECEIVER_PREFIX_RE = re.compile(r"^\(\*?[A-Za-z_][A-Za-z0-9_]*\)\.")


def strip_receiver_prefix(name: str) -> str:
    """Resolve repo-map display names like '(*Service).Status' to 'Status'.

    The repository map renders Go methods as '(*Receiver).Name()', and
    small models copy that format into read_symbol actions.
    """
    return _RECEIVER_PREFIX_RE.sub("", name)


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    signature: str | None = None
    receiver: str | None = None

    def display(self) -> str:
        if self.kind == "method" and self.receiver:
            return f"method ({self.receiver}) {self.name}() L{self.start_line}-L{self.end_line}"
        if self.kind == "function":
            return f"func {self.name}() L{self.start_line}-L{self.end_line}"
        if self.kind == "async_function":
            return f"async func {self.name}() L{self.start_line}-L{self.end_line}"
        return f"{self.kind} {self.name} L{self.start_line}-L{self.end_line}"

    def compact(self) -> str:
        if self.kind == "method" and self.receiver:
            return (
                f"({self.receiver}) {self.name}() L{self.start_line}-L{self.end_line}"
            )
        if self.kind in ("function", "async_function"):
            return f"{self.name}() L{self.start_line}-L{self.end_line}"
        return f"{self.name} {self.kind} L{self.start_line}-L{self.end_line}"


@dataclass
class SymbolIndex:
    symbols: list[Symbol] = field(default_factory=list)
    files_parsed: int = 0
    errors: int = 0
    truncated: bool = False
    index_elapsed_seconds: float = 0.0
    references: dict[str, list[str]] = field(default_factory=dict)
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

    def find(self, query: str, limit: int = 20) -> list[Symbol]:
        self._ensure_indexed()
        q = query.lower().strip()
        if not q:
            return []
        exact = self._by_name.get(q, [])
        if exact:
            return list(exact[:limit])
        results: list[Symbol] = []
        for sym in self.symbols:
            if q in sym.name.lower():
                results.append(sym)
                if len(results) >= limit:
                    break
        return results

    def by_file(self, path: str) -> list[Symbol]:
        self._ensure_indexed()
        return list(self._by_file.get(path, []))

    def exact(self, path: str, name: str) -> list[Symbol]:
        self._ensure_indexed()
        results = [s for s in self.symbols if s.path == path and s.name == name]
        if results:
            return results
        return [s for s in self.symbols if s.path == path and s.name.lower() == name.lower()]

    def at_line(self, path: str, line: int) -> Symbol | None:
        """Innermost indexed symbol containing a line, if any."""
        self._ensure_indexed()
        containing = [
            s
            for s in self._by_file.get(path, [])
            if s.start_line <= line <= s.end_line
        ]
        if not containing:
            return None
        return min(
            containing,
            key=lambda s: (s.end_line - s.start_line, s.start_line),
        )

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

    def by_name(self, name: str, limit: int = 10) -> list[Symbol]:
        self._ensure_indexed()
        return list(self._by_name.get(name.lower(), [])[:limit])

    def same_dir_callers(self, path: str, name: str) -> list[str]:
        """Caller keys referencing `name` from `path`'s directory.

        Name-level heuristic (v5-lite): same-directory call sites are
        unambiguous for the definition side, so they are a safe signal
        for disambiguation and annotation.
        """
        directory = str(Path(path).parent)
        callers: list[str] = []
        for key in self.references.get(name, []):
            caller_file = key.split("::", 1)[0]
            if caller_file and str(Path(caller_file).parent) == directory:
                callers.append(key)
        return callers

    def callers_of(self, path: str, name: str) -> list[str]:
        """Caller keys for a symbol, ambiguity-aware.

        Unique name in the index: all references count. Multiple
        same-name symbols: only same-directory references count.
        """
        self._ensure_indexed()
        if len(self._by_name.get(name.lower(), [])) <= 1:
            return list(self.references.get(name, []))
        return self.same_dir_callers(path, name)


# ---------------------------------------------------------------------------
# Task relevance ranking
# ---------------------------------------------------------------------------

_TASK_STOP_WORDS = frozenset(
    {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "can", "shall", "in", "on", "at",
        "to", "for", "of", "with", "by", "from", "and", "or", "but", "not",
        "no", "nor", "so", "yet", "this", "that", "these", "those", "it",
        "its", "when", "where", "what", "which", "who", "how", "why", "all",
        "any", "each", "few", "more", "most", "other", "some", "such", "only",
        "own", "same", "than", "too", "very", "just", "also", "then", "once",
        "here", "there", "while", "about", "into", "over", "under",
        "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
        "en", "y", "o", "que", "se", "su", "sus", "es", "son", "esta",
        "este", "esto", "estan", "hay", "hace", "hacen", "dime", "di",
        "diga", "revisa", "revisar", "revise", "mira", "mirar", "ver",
        "ves", "carpeta", "directorio", "folder", "directory", "proyecto",
        "project", "archivo", "file", "files", "codigo", "code", "como",
        "funciona", "function", "method", "class", "test", "por", "para",
        "con", "sin", "sobre", "entre", "hasta", "desde", "donde",
        "encuentra", "localiza", "find", "locate", "cual", "cuales",
        "corrige", "corregir", "fix", "change", "cambia", "cambiar",
        "modify", "modifica", "implement", "implementa", "add", "anade",
        "agrega", "elimina", "remove", "delete", "bug", "error", "failing",
        "debug", "empty", "vacio", "cuando", "tambien", "porque", "muy",
        "poco", "devuelva", "devuelve", "return", "returns",
    }
)


def _normalize_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _tokenize_task(task: str) -> list[str]:
    normalized = _normalize_accents(task)
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", normalized)
    tokens: list[str] = []
    seen: set[str] = set()
    for w in raw:
        wl = w.lower()
        if len(wl) < 3 or wl in _TASK_STOP_WORDS or wl in seen:
            continue
        seen.add(wl)
        tokens.append(wl)
    return tokens


def rank_symbols_for_task(
    task: str,
    symbols: SymbolIndex,
    limit: int = 10,
) -> list[Symbol]:
    symbols._ensure_indexed()
    tokens = _tokenize_task(task)
    if not tokens:
        return []

    scored: list[tuple[int, int, int, Symbol]] = []
    for idx, sym in enumerate(symbols.symbols):
        name_lower = sym.name.lower()
        path_lower = sym.path.lower()
        score = 0
        for tok in tokens:
            if name_lower == tok:
                score += 100
            elif tok in name_lower:
                score += 10
            elif tok in path_lower:
                score += 5
        if score > 0:
            scored.append((-score, sym.start_line, idx, sym))

    scored.sort()
    return [s[3] for s in scored[:limit]]


def find_exact_task_symbols(
    task: str, symbols: SymbolIndex, limit: int = 10, max_symbols: int = 3
) -> list[Symbol]:
    """Resolve the exact symbol names embedded in the task text.

    Deterministic and accent-insensitive: task words that exactly match
    indexed symbol names resolve to symbols, longest word first, so a
    task like "Corrige ApplyPolicy para que use validatePorts" resolves
    both ApplyPolicy and validatePorts. One symbol per word; same-name
    ambiguity prefers a file named in the task text, then the candidate
    with more same-directory call sites (v5-lite reference signal),
    then index order.
    """
    symbols._ensure_indexed()
    raw = _normalize_accents(task)
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", raw)
    seen: set[str] = set()
    ordered: list[str] = []
    for w in words:
        wl = w.lower()
        if len(wl) < 3 or wl in seen:
            continue
        seen.add(wl)
        ordered.append(wl)
    found: list[Symbol] = []
    loaded: set[tuple[str, str]] = set()
    task_lower = raw.lower()
    # Prefer the most specific (longest) identifiers in the task text.
    for wl in sorted(ordered, key=lambda w: -len(w)):
        candidates = list(symbols._by_name.get(wl, [])[:limit])
        if candidates:
            # Same-name ambiguity, in order: a candidate whose file is
            # named in the task text (e.g. "Corrige Validate en
            # internal/engine/validator.go"), then the candidate with
            # more call sites from its own directory (v5-lite
            # reference signal; CURRENT_ISSUES 4.2).
            sym = next(
                (c for c in candidates if c.path.lower() in task_lower
                 or Path(c.path).name.lower() in task_lower),
                None,
            )
            if sym is None and len(candidates) > 1:
                sym = max(
                    candidates,
                    key=lambda c: len(symbols.same_dir_callers(c.path, c.name)),
                )
            if sym is None:
                sym = candidates[0]
            key = (sym.path, sym.name)
            if key not in loaded:
                loaded.add(key)
                found.append(sym)
        if len(found) >= max_symbols:
            break
    return found[:max_symbols]


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


def _go_signature(src: bytes, node, kind: str, receiver: str = "") -> str:
    text = _go_node_text(src, node)
    brace_idx = text.find("{")
    if brace_idx != -1:
        sig = text[:brace_idx].strip()
    else:
        sig = text.strip().split("\n")[0]

    if kind == "method" and receiver and not sig.startswith(f"func ({receiver})"):
        paren_idx = sig.find(")")
        if paren_idx != -1:
            after = sig[paren_idx + 1 :].strip()
            sig = f"func ({receiver}) {after}"

    if kind in ("struct", "interface"):
        name = _go_node_text(src, node)
        for child in node.children:
            if child.type == "type_spec":
                for c in child.children:
                    if c.type == "type_identifier":
                        name = _go_node_text(src, c)
                        break
                break
        sig = f"type {name} {kind}"

    return sig[:MAX_SIGNATURE_CHARS]


def extract_go_symbols(path: Path, rel: str, max_symbols: int = 30) -> list[Symbol]:
    if not _go_available:
        return []
    try:
        src_bytes = path.read_bytes()
    except OSError:
        return []
    if len(src_bytes) > MAX_SYMBOL_FILE_BYTES:
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
            if name:
                sig = _go_signature(src_bytes, node, "function")
                if _add(
                    Symbol(
                        name=name,
                        kind="function",
                        path=rel,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        signature=sig,
                    )
                ):
                    break

        elif node.type == "method_declaration":
            name = _go_field_identifier_child(node)
            if not name:
                name = _go_identifier_child(node)
            if name:
                receiver = _go_receiver_type(src_bytes, node)
                sig = _go_signature(src_bytes, node, "method", receiver)
                if _add(
                    Symbol(
                        name=name,
                        kind="method",
                        path=rel,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        signature=sig,
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
                            signature=f"type {type_name} struct",
                        )
                    ):
                        break
                elif type_name and is_interface and _add(
                    Symbol(
                        name=type_name,
                        kind="interface",
                        path=rel,
                        start_line=spec.start_point[0] + 1,
                        end_line=spec.end_point[0] + 1,
                        signature=f"type {type_name} interface",
                    )
                ):
                    break

    return symbols


# ---------------------------------------------------------------------------
# Python extraction via stdlib ast
# ---------------------------------------------------------------------------


def _py_signature(source: str, node: ast.AST) -> str:
    lines = source.splitlines()
    start = node.lineno - 1
    for i in range(start, min(start + 10, len(lines))):
        line = lines[i]
        stripped = line.strip()
        if stripped.endswith(":"):
            return stripped[:MAX_SIGNATURE_CHARS]
    first_line = lines[start].strip() if start < len(lines) else ""
    return first_line[:MAX_SIGNATURE_CHARS]


def extract_python_symbols(path: Path, rel: str, max_symbols: int = 30) -> list[Symbol]:
    try:
        source = path.read_text(errors="replace")
    except OSError:
        return []
    if len(source.encode("utf-8")) > MAX_SYMBOL_FILE_BYTES:
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
            sig = _py_signature(source, node)
            symbols.append(
                Symbol(
                    name=node.name,
                    kind="async_function",
                    path=rel,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=sig,
                )
            )
        elif isinstance(node, ast.FunctionDef):
            sig = _py_signature(source, node)
            symbols.append(
                Symbol(
                    name=node.name,
                    kind="function",
                    path=rel,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=sig,
                )
            )
        elif isinstance(node, ast.ClassDef):
            sig = _py_signature(source, node)
            symbols.append(
                Symbol(
                    name=node.name,
                    kind="class",
                    path=rel,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=sig,
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
    repo: Path, scan: RepoScan, max_symbols_per_file: int = MAX_SYMBOLS_PER_FILE
) -> SymbolIndex:
    repo = repo.resolve()
    start = time.time()
    all_symbols: list[Symbol] = []
    per_file_refs: list[list[tuple[str, str]]] = []
    files_parsed = 0
    errors = 0
    truncated = False

    for rel in scan.files:
        if len(all_symbols) >= MAX_SYMBOLS_TOTAL:
            truncated = True
            break
        full = repo / rel
        suffix = full.suffix.lower()
        if suffix not in (".go", ".py"):
            continue
        try:
            file_size = full.stat().st_size
        except OSError:
            errors += 1
            continue
        if file_size > MAX_SYMBOL_FILE_BYTES:
            errors += 1
            continue
        try:
            syms = extract_symbols(full, rel, max_symbols=max_symbols_per_file)
            files_parsed += 1
        except Exception:  # noqa: BLE001
            syms = []
            errors += 1
        remaining = MAX_SYMBOLS_TOTAL - len(all_symbols)
        all_symbols.extend(syms[:remaining])
        per_file_refs.append(extract_file_references(full, rel))

    elapsed = time.time() - start
    return SymbolIndex(
        symbols=all_symbols,
        files_parsed=files_parsed,
        errors=errors,
        truncated=truncated,
        index_elapsed_seconds=round(elapsed, 4),
        references=aggregate_references(per_file_refs),
    )


def reindex_files(
    repo: Path, index: SymbolIndex, files: list[str]
) -> SymbolIndex:
    repo = repo.resolve()
    patched = set(files)
    existing = [s for s in index.symbols if s.path not in patched]
    per_file_refs: list[list[tuple[str, str]]] = []
    # keep references from untouched files, drop the patched files' old
    # caller keys, and re-extract them
    for name, callers in index.references.items():
        kept = [
            c
            for c in callers
            if c.split("::", 1)[0] not in patched
        ]
        if kept:
            per_file_refs.append([(name, c) for c in kept])
    for rel in files:
        full = repo / rel
        suffix = full.suffix.lower()
        if suffix not in (".go", ".py"):
            continue
        try:
            syms = extract_symbols(full, rel)
        except Exception:  # noqa: BLE001
            syms = []
        existing.extend(syms)
        per_file_refs.append(extract_file_references(full, rel))
    return SymbolIndex(
        symbols=existing, references=aggregate_references(per_file_refs)
    )
