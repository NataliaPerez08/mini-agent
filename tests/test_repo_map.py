from mini_agent.repo_map import (
    MAX_FILES_IN_MAP,
    MAX_REPO_MAP_CHARS,
    MAX_SYMBOLS_PER_FILE,
    build_repo_map,
)
from mini_agent.scanner import scan_repository
from mini_agent.symbols import build_symbol_index


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


class TestRepoMapLimits:
    def _make_big_repo(self, git_repo, n_files=30, syms_per_file=20):
        for i in range(n_files):
            lines = ["package main", ""]
            for j in range(syms_per_file):
                lines.append(f"func F{i}_{j}() {{}}")
                lines.append("")
            (git_repo / f"pkg{i}").mkdir(exist_ok=True)
            (git_repo / f"pkg{i}" / f"file{i}.go").write_text("\n".join(lines))
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "big"], cwd=git_repo, check=True)
        return scan_repository(git_repo)

    def test_repo_map_respects_max_chars(self, git_repo):
        scan = self._make_big_repo(git_repo)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(git_repo, scan, symbol_index=idx)
        assert len(repo_map) <= MAX_REPO_MAP_CHARS

    def test_repo_map_prioritizes_task_files(self, git_repo):
        scan = self._make_big_repo(git_repo)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(
            git_repo,
            scan,
            symbol_index=idx,
            task="Corrige F5_3",
            max_files_in_map=5,
        )
        assert "pkg5/file5.go" in repo_map

    def test_repo_map_generic_task_no_priority_inversion(self, git_repo):
        scan = self._make_big_repo(git_repo, n_files=5)
        idx = build_symbol_index(git_repo, scan)
        repo_map = build_repo_map(git_repo, scan, symbol_index=idx)
        assert len(repo_map) <= MAX_REPO_MAP_CHARS
        assert "SYMBOLS" in repo_map

    def test_max_repo_map_chars_constant(self):
        assert MAX_REPO_MAP_CHARS == 5000

    def test_max_files_in_map_constant(self):
        assert MAX_FILES_IN_MAP == 60
