from mini_agent.scanner import scan_repository
from mini_agent.symbols import (
    MAX_SIGNATURE_CHARS,
    MAX_SYMBOL_FILE_BYTES,
    MAX_SYMBOLS_PER_FILE,
    MAX_SYMBOLS_TOTAL,
    Symbol,
    SymbolIndex,
    _tokenize_task,
    build_symbol_index,
    extract_go_symbols,
    extract_python_symbols,
    find_exact_task_symbol,
    rank_symbols_for_task,
)

GO_SOURCE = """package firewall

type Service struct {
\tName string
\tPort int
}

type PolicyStore interface {
\tGetPolicy() string
\tSetPolicy(p string) error
}

func NewService() *Service {
\treturn &Service{Name: "default"}
}

func (s *Service) Status() string {
\treturn s.Name
}

func ApplyPolicy() error {
\treturn nil
}

const (
\tMaxRetries = 3
\tDefaultPort = 8080
)
"""

PYTHON_SOURCE = """def foo():
    x = 1
    return x

class Bar:
    def method(self):
        pass

async def baz():
    pass
"""


class TestExtractGoSymbols:
    def test_extract_function(self, tmp_path):
        p = tmp_path / "service.go"
        p.write_text(GO_SOURCE)
        syms = extract_go_symbols(p, "service.go")
        names = [s.name for s in syms]
        assert "NewService" in names
        assert "ApplyPolicy" in names

    def test_extract_method(self, tmp_path):
        p = tmp_path / "service.go"
        p.write_text(GO_SOURCE)
        syms = extract_go_symbols(p, "service.go")
        method = next(s for s in syms if s.name == "Status")
        assert method.kind == "method"
        assert method.receiver == "*Service"

    def test_extract_struct(self, tmp_path):
        p = tmp_path / "service.go"
        p.write_text(GO_SOURCE)
        syms = extract_go_symbols(p, "service.go")
        struct = next(s for s in syms if s.name == "Service")
        assert struct.kind == "struct"

    def test_extract_interface(self, tmp_path):
        p = tmp_path / "service.go"
        p.write_text(GO_SOURCE)
        syms = extract_go_symbols(p, "service.go")
        iface = next(s for s in syms if s.name == "PolicyStore")
        assert iface.kind == "interface"

    def test_correct_line_ranges(self, tmp_path):
        p = tmp_path / "service.go"
        p.write_text(GO_SOURCE)
        syms = extract_go_symbols(p, "service.go")
        by_name = {s.name: s for s in syms}
        assert by_name["Service"].start_line == 3
        assert by_name["Service"].end_line == 6
        assert by_name["NewService"].start_line == 13
        assert by_name["ApplyPolicy"].start_line == 21

    def test_tolerates_syntax_error(self, tmp_path):
        p = tmp_path / "broken.go"
        p.write_text("package broken\n\nfunc {{{{")
        syms = extract_go_symbols(p, "broken.go")
        assert isinstance(syms, list)

    def test_nonexistent_file(self, tmp_path):
        syms = extract_go_symbols(tmp_path / "nope.go", "nope.go")
        assert syms == []

    def test_max_symbols_limit(self, tmp_path):
        lines = ["package main", ""]
        for i in range(50):
            lines.append(f"func f{i}() {{}}")
            lines.append("")
        p = tmp_path / "many.go"
        p.write_text("\n".join(lines))
        syms = extract_go_symbols(p, "many.go", max_symbols=5)
        assert len(syms) == 5

    def test_const_group_excluded(self, tmp_path):
        p = tmp_path / "service.go"
        p.write_text(GO_SOURCE)
        syms = extract_go_symbols(p, "service.go")
        names = [s.name for s in syms]
        assert "MaxRetries" not in names
        assert "DefaultPort" not in names

    def test_no_bodies_in_symbols(self, tmp_path):
        p = tmp_path / "service.go"
        p.write_text(GO_SOURCE)
        syms = extract_go_symbols(p, "service.go")
        for sym in syms:
            assert sym.kind in (
                "function",
                "method",
                "struct",
                "interface",
            )

    def test_value_receiver(self, tmp_path):
        p = tmp_path / "val.go"
        p.write_text("package x\n\ntype T struct{}\n\nfunc (t T) Foo() {}\n")
        syms = extract_go_symbols(p, "val.go")
        method = next(s for s in syms if s.name == "Foo")
        assert method.receiver == "T"


class TestExtractPythonSymbols:
    def test_extract_function_with_range(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text(PYTHON_SOURCE)
        syms = extract_python_symbols(p, "a.py")
        foo = next(s for s in syms if s.name == "foo")
        assert foo.kind == "function"
        assert foo.start_line == 1
        assert foo.end_line == 3

    def test_extract_class_with_range(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text(PYTHON_SOURCE)
        syms = extract_python_symbols(p, "a.py")
        bar = next(s for s in syms if s.name == "Bar")
        assert bar.kind == "class"
        assert bar.start_line == 5

    def test_extract_async_function(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text(PYTHON_SOURCE)
        syms = extract_python_symbols(p, "a.py")
        baz = next(s for s in syms if s.name == "baz")
        assert baz.kind == "async_function"

    def test_tolerates_syntax_error(self, tmp_path):
        p = tmp_path / "broken.py"
        p.write_text("def foo(:\n    pass\n")
        syms = extract_python_symbols(p, "broken.py")
        assert syms == []

    def test_nonexistent_file(self, tmp_path):
        syms = extract_python_symbols(tmp_path / "nope.py", "nope.py")
        assert syms == []

    def test_nested_not_top_level(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("def outer():\n    def inner():\n        pass\n")
        syms = extract_python_symbols(p, "a.py")
        names = [s.name for s in syms]
        assert "outer" in names
        assert "inner" not in names


class TestSymbolDisplay:
    def test_function_display(self):
        s = Symbol("Foo", "function", "a.go", 10, 20)
        assert "func Foo()" in s.display()
        assert "L10-L20" in s.display()

    def test_method_display_with_receiver(self):
        s = Symbol("Status", "method", "a.go", 80, 102, receiver="*Service")
        d = s.display()
        assert "method" in d
        assert "(*Service)" in d
        assert "Status()" in d
        assert "L80-L102" in d

    def test_struct_compact(self):
        s = Symbol("Service", "struct", "a.go", 10, 16)
        c = s.compact()
        assert "Service" in c
        assert "struct" in c
        assert "L10-L16" in c

    def test_function_compact_no_kind_prefix(self):
        s = Symbol("ApplyPolicy", "function", "a.go", 31, 74)
        c = s.compact()
        assert c.startswith("ApplyPolicy()")
        assert "L31-L74" in c


class TestSymbolIndex:
    def test_find_exact(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
            ]
        )
        results = idx.find("ApplyPolicy")
        assert len(results) == 1
        assert results[0].name == "ApplyPolicy"

    def test_find_case_insensitive(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
            ]
        )
        results = idx.find("applypolicy")
        assert len(results) == 1

    def test_find_partial_match(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "a.go", 1, 10),
                Symbol("PolicyStore", "interface", "b.go", 1, 10),
                Symbol("FirewallPolicy", "struct", "c.go", 1, 10),
            ]
        )
        results = idx.find("Policy")
        assert len(results) == 3

    def test_find_no_match(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Foo", "function", "a.go", 1, 10),
            ]
        )
        assert idx.find("Bar") == []

    def test_find_empty_query(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Foo", "function", "a.go", 1, 10),
            ]
        )
        assert idx.find("") == []

    def test_by_file(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("A", "function", "a.go", 1, 10),
                Symbol("B", "function", "b.go", 1, 10),
                Symbol("C", "struct", "a.go", 12, 20),
            ]
        )
        results = idx.by_file("a.go")
        assert len(results) == 2
        assert all(s.path == "a.go" for s in results)

    def test_by_file_no_match(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("A", "function", "a.go", 1, 10),
            ]
        )
        assert idx.by_file("z.go") == []

    def test_find_candidates(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
                Symbol("PolicyStore", "interface", "fw/types.go", 8, 24),
                Symbol("FirewallPolicy", "struct", "fw/types.go", 26, 40),
                Symbol("Status", "method", "fw/service.go", 80, 102),
            ]
        )
        results = idx.find_candidates(["apply", "policy"])
        names = [s.name for s in results]
        assert "ApplyPolicy" in names
        assert "PolicyStore" in names
        assert "FirewallPolicy" in names

    def test_find_candidates_ignores_short_keywords(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "a.go", 1, 10),
            ]
        )
        results = idx.find_candidates(["ab"])
        assert results == []

    def test_find_candidates_no_duplicates(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "a.go", 1, 10),
            ]
        )
        results = idx.find_candidates(["apply", "policy", "Apply"])
        assert len(results) == 1

    def test_empty_index(self):
        idx = SymbolIndex()
        assert idx.find("anything") == []
        assert idx.by_file("any.go") == []
        assert idx.find_candidates(["keyword"]) == []


class TestBuildSymbolIndex:
    def test_build_index_go(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        (git_repo / "fw" / "types.go").write_text(
            "package firewall\n\ntype Config struct {\n\tPort int\n}\n"
        )
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        names = [s.name for s in idx.symbols]
        assert "Service" in names
        assert "ApplyPolicy" in names
        assert "Config" in names

    def test_build_index_python(self, git_repo):
        (git_repo / "app.py").write_text("def main():\n    pass\n")
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "py"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        names = [s.name for s in idx.symbols]
        assert "main" in names

    def test_build_index_mixed(self, git_repo):
        (git_repo / "a.go").write_text("package x\n\nfunc Foo() {}\n")
        (git_repo / "b.py").write_text("def bar():\n    pass\n")
        (git_repo / "c.js").write_text("function baz() {}\n")
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "mix"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        names = [s.name for s in idx.symbols]
        assert "Foo" in names
        assert "bar" in names
        assert "baz" not in names

    def test_build_index_empty_repo(self, git_repo):
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        assert idx.symbols == []


class TestSymbolFrozen:
    def test_symbol_is_frozen(self):
        s = Symbol("Foo", "function", "a.go", 1, 10)
        try:
            s.name = "Bar"
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass

    def test_symbol_with_signature(self):
        s = Symbol("Foo", "function", "a.go", 1, 10, signature="func Foo() error")
        assert s.signature == "func Foo() error"

    def test_symbol_receiver_none_default(self):
        s = Symbol("Foo", "function", "a.go", 1, 10)
        assert s.receiver is None


class TestSymbolSignatures:
    def test_go_function_signature(self, tmp_path):
        p = tmp_path / "a.go"
        p.write_text("package x\n\nfunc ApplyPolicy(ctx context.Context) error {\n\treturn nil\n}\n")
        syms = extract_go_symbols(p, "a.go")
        assert len(syms) == 1
        assert syms[0].signature is not None
        assert "func ApplyPolicy" in syms[0].signature
        assert "error" in syms[0].signature
        assert "{" not in syms[0].signature

    def test_go_method_signature(self, tmp_path):
        p = tmp_path / "a.go"
        p.write_text("package x\n\ntype S struct{}\n\nfunc (s *S) Run() error {\n\treturn nil\n}\n")
        syms = extract_go_symbols(p, "a.go")
        method = next(s for s in syms if s.name == "Run")
        assert method.signature is not None
        assert "(*S)" in method.signature
        assert "Run" in method.signature

    def test_go_struct_signature(self, tmp_path):
        p = tmp_path / "a.go"
        p.write_text("package x\n\ntype Service struct {\n\tName string\n}\n")
        syms = extract_go_symbols(p, "a.go")
        s = next(s for s in syms if s.name == "Service")
        assert s.signature == "type Service struct"

    def test_go_interface_signature(self, tmp_path):
        p = tmp_path / "a.go"
        p.write_text("package x\n\ntype Store interface {\n\tSave() error\n}\n")
        syms = extract_go_symbols(p, "a.go")
        s = next(s for s in syms if s.name == "Store")
        assert s.signature == "type Store interface"

    def test_python_function_signature(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("def foo(a: int, b: str) -> bool:\n    return True\n")
        syms = extract_python_symbols(p, "a.py")
        assert len(syms) == 1
        assert syms[0].signature is not None
        assert "def foo" in syms[0].signature
        assert ":" in syms[0].signature

    def test_python_class_signature(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("class Service(BaseService):\n    pass\n")
        syms = extract_python_symbols(p, "a.py")
        assert len(syms) == 1
        assert syms[0].signature is not None
        assert "class Service" in syms[0].signature

    def test_signature_truncated(self, tmp_path):
        long_params = ", ".join(f"a{i}: int" for i in range(100))
        p = tmp_path / "a.go"
        p.write_text(f"package x\n\nfunc Big({long_params}) {{}}\n")
        syms = extract_go_symbols(p, "a.go")
        assert len(syms) == 1
        assert len(syms[0].signature) <= MAX_SIGNATURE_CHARS


class TestSymbolIndexExact:
    def test_exact_match(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
                Symbol("ApplyPolicy", "method", "fw/other.go", 10, 20, receiver="*S"),
            ]
        )
        results = idx.exact("fw/service.go", "ApplyPolicy")
        assert len(results) == 1
        assert results[0].kind == "function"

    def test_exact_case_insensitive_fallback(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
            ]
        )
        results = idx.exact("fw/service.go", "applypolicy")
        assert len(results) == 1

    def test_exact_no_match(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Foo", "function", "a.go", 1, 10),
            ]
        )
        assert idx.exact("a.go", "Bar") == []

    def test_exact_multiple_same_name_same_file(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Run", "method", "a.go", 10, 20, receiver="*Foo"),
                Symbol("Run", "method", "a.go", 30, 40, receiver="*Bar"),
            ]
        )
        results = idx.exact("a.go", "Run")
        assert len(results) == 2


class TestSymbolIndexFindLimit:
    def test_find_respects_limit(self):
        syms = [Symbol(f"F{i}", "function", "a.go", i, i + 1) for i in range(50)]
        idx = SymbolIndex(symbols=syms)
        results = idx.find("F", limit=5)
        assert len(results) == 5

    def test_find_default_limit(self):
        syms = [Symbol(f"F{i}", "function", "a.go", i, i + 1) for i in range(50)]
        idx = SymbolIndex(symbols=syms)
        results = idx.find("F")
        assert len(results) <= 20


class TestRankSymbolsForTask:
    def test_exact_name_match_scores_high(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
                Symbol("Status", "method", "fw/service.go", 80, 102),
            ]
        )
        results = rank_symbols_for_task("Corrige ApplyPolicy", idx)
        assert results[0].name == "ApplyPolicy"

    def test_generic_task_no_candidates(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Query", "method", "handlers/audit.go", 10, 20),
                Symbol("AuditQuery", "struct", "models/audit.go", 1, 10),
                Symbol("doRequest", "function", "main_test.go", 5, 8),
            ]
        )
        results = rank_symbols_for_task(
            "Revisa que hay en la carpeta y dime que hace el proyecto", idx
        )
        assert results == []

    def test_stop_words_filtered(self):
        tokens = _tokenize_task("Revisa que hay en la carpeta y dime que hace")
        assert tokens == []

    def test_accents_normalized(self):
        tokens = _tokenize_task("Corrige ApplyPolicy cuando esté vacío")
        assert tokens == ["applypolicy"]

    def test_accented_words_do_not_match_test_prefix(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("TestLoadDefaults", "function", "config_test.go", 1, 5),
                Symbol("ApplyPolicy", "method", "service.go", 10, 20),
            ]
        )
        results = rank_symbols_for_task(
            "Corrige ApplyPolicy cuando esté vacío", idx
        )
        names = [s.name for s in results]
        assert "ApplyPolicy" in names
        assert "TestLoadDefaults" not in names

    def test_partial_match_scores_lower(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
                Symbol("PolicyStore", "interface", "fw/types.go", 8, 24),
            ]
        )
        results = rank_symbols_for_task("Revisa Policy", idx)
        assert len(results) == 2
        assert results[0].start_line <= results[1].start_line

    def test_path_relevance(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Foo", "function", "other/util.go", 1, 10),
                Symbol("Bar", "function", "fw/firewall.go", 1, 10),
            ]
        )
        results = rank_symbols_for_task("Revisa el firewall", idx, limit=5)
        assert len(results) == 1
        assert results[0].name == "Bar"

    def test_no_match_returns_empty(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Foo", "function", "a.go", 1, 10),
            ]
        )
        assert rank_symbols_for_task("XYZABC", idx) == []

    def test_limit_respected(self):
        syms = [Symbol(f"Policy{i}", "function", "a.go", i, i + 1) for i in range(20)]
        idx = SymbolIndex(symbols=syms)
        results = rank_symbols_for_task("Revisa Policy", idx, limit=5)
        assert len(results) == 5


class TestFindExactTaskSymbol:
    def test_longest_exact_match_wins(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
                Symbol("Port", "method", "fw/types.go", 8, 24),
            ]
        )
        result = find_exact_task_symbol(
            "Corrige ApplyPolicy para que devuelva error cuando Port esté vacío", idx
        )
        assert result is not None
        assert result.name == "ApplyPolicy"

    def test_accent_insensitive(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
            ]
        )
        result = find_exact_task_symbol("Corrige ApplyPolicy", idx)
        assert result is not None
        assert result.name == "ApplyPolicy"

    def test_case_insensitive(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
                Symbol("PolicyStore", "interface", "fw/types.go", 8, 24),
            ]
        )
        result = find_exact_task_symbol("corrige applypolicy", idx)
        assert result is not None
        assert result.name == "ApplyPolicy"

    def test_no_exact_symbol_returns_none(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Foo", "function", "a.go", 1, 10),
            ]
        )
        assert find_exact_task_symbol("mejora el rendimiento general", idx) is None

    def test_short_words_ignored(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Id", "method", "a.go", 1, 10),
            ]
        )
        assert find_exact_task_symbol("muestra el id", idx) is None


class TestSymbolIndexByName:
    def test_by_name_exact(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("ApplyPolicy", "function", "fw/service.go", 31, 74),
                Symbol("ApplyPolicy", "method", "fw/other.go", 8, 24),
            ]
        )
        results = idx.by_name("ApplyPolicy")
        assert len(results) == 2
        assert all(s.name == "ApplyPolicy" for s in results)

    def test_by_name_missing_returns_empty(self):
        idx = SymbolIndex(
            symbols=[
                Symbol("Foo", "function", "a.go", 1, 10),
            ]
        )
        assert idx.by_name("Nope") == []


class TestIndexLimits:
    def test_max_symbols_total(self, git_repo):
        for i in range(100):
            lines = ["x = 1", ""]
            for j in range(60):
                lines.append(f"def f{j}():")
                lines.append("    pass")
                lines.append("")
            (git_repo / f"f{i}.py").write_text("\n".join(lines))
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "many"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        assert len(idx.symbols) <= MAX_SYMBOLS_TOTAL
        assert idx.truncated is True

    def test_max_symbols_per_file(self, tmp_path):
        lines = ["package main", ""]
        for i in range(200):
            lines.append(f"func f{i}() {{}}")
            lines.append("")
        p = tmp_path / "many.go"
        p.write_text("\n".join(lines))
        syms = extract_go_symbols(p, "many.go", max_symbols=MAX_SYMBOLS_PER_FILE)
        assert len(syms) <= MAX_SYMBOLS_PER_FILE

    def test_oversized_file_skipped(self, git_repo):
        filler = "# " + "x" * 99 + "\n"
        content = (
            "def foo():\n    pass\n\n"
            + filler * (MAX_SYMBOL_FILE_BYTES // len(filler) + 10)
        )
        (git_repo / "big.py").write_text(content)
        (git_repo / "small.py").write_text("def bar():\n    pass\n")
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "big"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        assert len((git_repo / "big.py").read_bytes()) > MAX_SYMBOL_FILE_BYTES
        idx = build_symbol_index(git_repo, scan)
        names = [s.name for s in idx.symbols]
        assert "foo" not in names
        assert "bar" in names

    def test_index_metadata_tracked(self, git_repo):
        (git_repo / "a.py").write_text("def foo():\n    pass\n")
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        assert idx.files_parsed >= 1
        assert idx.errors >= 0
        assert idx.truncated is False


class TestMalformedGoSource:
    def test_partial_error_extracts_valid_symbols(self, tmp_path):
        src = """package broken

func Good() {}

func Broken(

func AlsoGood() {}
"""
        p = tmp_path / "broken.go"
        p.write_text(src)
        syms = extract_go_symbols(p, "broken.go")
        names = [s.name for s in syms]
        assert "Good" in names
        assert isinstance(syms, list)

    def test_malformed_does_not_crash_build(self, git_repo):
        (git_repo / "bad.go").write_text("package broken\n\nfunc {{{{")
        (git_repo / "good.go").write_text("package good\n\nfunc Foo() {}\n")
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "mix"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        names = [s.name for s in idx.symbols]
        assert "Foo" in names
