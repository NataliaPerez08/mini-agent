from mini_agent.repo_map import (
    MAX_SYMBOLS_PER_FILE,
    build_repo_map,
    extract_python_symbols,
)
from mini_agent.scanner import scan_repository
from mini_agent.symbols import build_symbol_index


class TestExtractPythonSymbols:
    def test_extract_functions(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("def foo():\n    pass\n\ndef bar():\n    return 1\n")
        syms = extract_python_symbols(p)
        assert "def foo()" in syms
        assert "def bar()" in syms

    def test_extract_async_functions(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("async def fetch():\n    pass\n")
        syms = extract_python_symbols(p)
        assert "async def fetch()" in syms

    def test_extract_classes(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("class User:\n    pass\n\nclass Admin(User):\n    pass\n")
        syms = extract_python_symbols(p)
        assert "class User" in syms
        assert "class Admin" in syms

    def test_mixed_symbols(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text(
            "def login():\n    pass\n\n"
            "class User:\n    pass\n\n"
            "async def validate():\n    pass\n"
        )
        syms = extract_python_symbols(p)
        assert syms == ["def login()", "class User", "async def validate()"]

    def test_tolerates_syntax_error(self, tmp_path):
        p = tmp_path / "broken.py"
        p.write_text("def foo(:\n    pass\n")
        syms = extract_python_symbols(p)
        assert syms == []

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.py"
        p.write_text("")
        syms = extract_python_symbols(p)
        assert syms == []

    def test_nonexistent_file(self, tmp_path):
        syms = extract_python_symbols(tmp_path / "nope.py")
        assert syms == []

    def test_no_body_extracted(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("def foo():\n    x = 1\n    y = 2\n    return x + y\n")
        syms = extract_python_symbols(p)
        assert syms == ["def foo()"]
        assert "x = 1" not in syms

    def test_max_symbols_limit(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("".join(f"def f{i}():\n    pass\n\n" for i in range(50)))
        syms = extract_python_symbols(p, max_symbols=5)
        assert len(syms) == 5

    def test_nested_functions_not_top_level(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("def outer():\n    def inner():\n        pass\n")
        syms = extract_python_symbols(p)
        assert syms == ["def outer()"]
        assert "def inner()" not in syms


class TestBuildRepoMap:
    def test_project_section(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        repo_map = build_repo_map(git_repo_with_files, scan)
        assert repo_map.startswith("PROJECT")
        assert "Primary language: Python" in repo_map
        assert f"Files: {len(scan.files)}" in repo_map

    def test_files_section(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        repo_map = build_repo_map(git_repo_with_files, scan)
        assert "FILES" in repo_map
        assert "src/app.py" in repo_map
        assert "src/utils.py" in repo_map

    def test_important_files_section(self, git_repo):
        (git_repo / "pyproject.toml").write_text("[project]\n")
        (git_repo / "README.md").write_text("# x\n")
        (git_repo / "a.py").write_text("x\n")
        scan = scan_repository(git_repo)
        repo_map = build_repo_map(git_repo, scan)
        assert "IMPORTANT FILES" in repo_map
        assert "pyproject.toml" in repo_map
        assert "README.md" in repo_map

    def test_symbols_section(self, git_repo):
        (git_repo / "src").mkdir()
        (git_repo / "src" / "auth.py").write_text(
            "def login():\n    pass\n\ndef validate_password():\n    pass\n"
        )
        scan = scan_repository(git_repo)
        repo_map = build_repo_map(git_repo, scan)
        assert "SYMBOLS" in repo_map
        assert "src/auth.py" in repo_map
        assert "login() L1-L2" in repo_map
        assert "validate_password() L4-L5" in repo_map

    def test_no_symbols_for_non_python(self, git_repo):
        (git_repo / "a.js").write_text("function foo() {}\n")
        scan = scan_repository(git_repo)
        repo_map = build_repo_map(git_repo, scan)
        assert "SYMBOLS" not in repo_map

    def test_empty_repo(self, git_repo):
        scan = scan_repository(git_repo)
        repo_map = build_repo_map(git_repo, scan)
        assert "Files: 0" in repo_map

    def test_tests_count(self, git_repo):
        (git_repo / "test_auth.py").write_text("def test_x():\n    pass\n")
        (git_repo / "app.py").write_text("def main():\n    pass\n")
        scan = scan_repository(git_repo)
        repo_map = build_repo_map(git_repo, scan)
        assert "Tests: 1" in repo_map

    def test_compact_output(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        repo_map = build_repo_map(git_repo_with_files, scan)
        for line in repo_map.splitlines():
            assert len(line) < 200

    def test_files_section_sorted(self, git_repo):
        (git_repo / "z.py").write_text("x\n")
        (git_repo / "a.py").write_text("x\n")
        (git_repo / "m.py").write_text("x\n")
        scan = scan_repository(git_repo)
        repo_map = build_repo_map(git_repo, scan)
        files_idx = repo_map.index("FILES\n") + len("FILES\n")
        end = repo_map.find("\n\n", files_idx)
        if end == -1:
            end = len(repo_map)
        file_lines = repo_map[files_idx:end].splitlines()
        assert file_lines == sorted(file_lines)


GO_SOURCE = """package firewall

type Service struct {
\tName string
}

type PolicyStore interface {
\tGetPolicy() string
}

func NewService() *Service {
\treturn &Service{}
}

func (s *Service) Status() string {
\treturn s.Name
}

func ApplyPolicy() error {
\treturn nil
}
"""


class TestRepoMapGoSymbols:
    def test_go_symbols_in_repo_map(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(git_repo, scan, symbol_index=idx)
        assert "SYMBOLS" in repo_map
        assert "fw/service.go" in repo_map
        assert "ApplyPolicy()" in repo_map
        assert "L" in repo_map

    def test_go_symbol_line_ranges(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(git_repo, scan, symbol_index=idx)
        assert "Service struct L3-L5" in repo_map
        assert "ApplyPolicy() L19-L21" in repo_map

    def test_go_method_with_receiver_in_map(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(git_repo, scan, symbol_index=idx)
        assert "(*Service) Status()" in repo_map

    def test_go_struct_in_map(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(git_repo, scan, symbol_index=idx)
        assert "Service struct" in repo_map

    def test_go_interface_in_map(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(git_repo, scan, symbol_index=idx)
        assert "PolicyStore interface" in repo_map

    def test_no_bodies_in_repo_map(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_SOURCE)
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(git_repo, scan, symbol_index=idx)
        assert "return nil" not in repo_map
        assert "return &Service" not in repo_map

    def test_max_symbols_per_file_default(self):
        assert MAX_SYMBOLS_PER_FILE == 20

    def test_max_symbols_per_file_limit(self, git_repo):
        lines = ["package main", ""]
        for i in range(50):
            lines.append(f"func f{i}() {{}}")
            lines.append("")
        (git_repo / "many.go").write_text("\n".join(lines))
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "many"], cwd=git_repo, check=True)
        scan = scan_repository(git_repo)
        idx = build_symbol_index(git_repo, scan, max_symbols_per_file=50)
        repo_map = build_repo_map(
            git_repo, scan, symbol_index=idx, max_symbols_per_file=5
        )
        symbol_lines = [
            l for l in repo_map.splitlines() if l.startswith("  ") and "L" in l
        ]
        assert len(symbol_lines) <= 5
