from mini_agent.scanner import scan_repository
from mini_agent.symbols import (
    Symbol,
    SymbolIndex,
    build_symbol_index,
    extract_go_symbols,
    extract_python_symbols,
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

    def test_const_group(self, tmp_path):
        p = tmp_path / "service.go"
        p.write_text(GO_SOURCE)
        syms = extract_go_symbols(p, "service.go")
        names = [s.name for s in syms]
        assert "MaxRetries" in names
        assert "DefaultPort" in names

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
                "const",
                "var",
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
