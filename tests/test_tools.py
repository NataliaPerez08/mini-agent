import pytest

from mini_agent.symbols import Symbol, SymbolIndex
from mini_agent.tools import RepoTools


class TestSafePath:
    def test_normal_path(self, git_repo):
        tools = RepoTools(git_repo, "")
        p = tools._safe_path("src/app.py")
        assert p == (git_repo / "src" / "app.py").resolve()

    def test_path_traversal_rejected(self, git_repo):
        tools = RepoTools(git_repo, "")
        with pytest.raises(ValueError, match="escapes repository"):
            tools._safe_path("../../etc/passwd")

    def test_dot_returns_repo(self, git_repo):
        tools = RepoTools(git_repo, "")
        p = tools._safe_path(".")
        assert p == git_repo.resolve()

    def test_absolute_path_inside_repo(self, git_repo):
        tools = RepoTools(git_repo, "")
        p = tools._safe_path(str(git_repo / "src" / "app.py"))
        assert p == (git_repo / "src" / "app.py").resolve()


class TestReadFile:
    def test_read_full_file(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/app.py")
        assert "def hello" in result
        assert "return 'hello'" in result

    def test_line_numbers_prefixed(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/app.py")
        lines = result.splitlines()
        assert lines[0].lstrip().startswith("1:")

    def test_read_partial_range(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/utils.py", start=2, end=3)
        lines = result.splitlines()
        assert len(lines) == 2
        assert "PI" not in lines[0]

    def test_nonexistent_file(self, git_repo):
        tools = RepoTools(git_repo, "")
        result = tools.read_file("nope.py")
        assert result.startswith("ERROR:")

    def test_empty_file(self, git_repo):
        (git_repo / "empty.py").write_text("")
        tools = RepoTools(git_repo, "")
        result = tools.read_file("empty.py")
        assert result == "<empty>"

    def test_start_clamped_to_one(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/app.py", start=-5, end=1)
        lines = result.splitlines()
        assert len(lines) == 1

    def test_end_clamped_to_file_length(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.read_file("src/app.py", start=1, end=9999)
        lines = result.splitlines()
        assert len(lines) == 2


class TestSearchCode:
    def test_finds_match(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.search_code("def hello")
        assert "src/app.py" in result
        assert "def hello" in result

    def test_no_matches(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.search_code("xyzzy_nonexistent")
        assert result == "<no matches>"

    def test_search_with_path_filter(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.search_code("def", path="src")
        assert "src/app.py" in result
        assert "src/utils.py" in result

    def test_result_truncated(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        result = tools.search_code("def")
        assert len(result) <= 12000


class TestApplyPatch:
    def test_successful_patch(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'world'\n"
        )
        result = tools.apply_patch(patch)
        assert result.startswith("PATCH APPLIED")
        assert "return 'world'" in result

    def test_failed_patch(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        patch = "garbage that is not a valid diff\n"
        result = tools.apply_patch(patch)
        assert result.startswith("PATCH FAILED")

    def test_patch_modifies_file(self, git_repo_with_files):
        tools = RepoTools(git_repo_with_files, "")
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def hello():\n"
            "-    return 'hello'\n"
            "+    return 'patched'\n"
        )
        tools.apply_patch(patch)
        content = (git_repo_with_files / "src" / "app.py").read_text()
        assert "patched" in content


class TestRunTests:
    def test_no_command_configured(self, git_repo):
        tools = RepoTools(git_repo, "")
        result = tools.run_tests()
        assert result == "NO TEST COMMAND CONFIGURED"

    def test_whitespace_only_command(self, git_repo):
        tools = RepoTools(git_repo, "   ")
        result = tools.run_tests()
        assert result == "NO TEST COMMAND CONFIGURED"

    def test_passing_test(self, git_repo):
        tools = RepoTools(git_repo, "true")
        result = tools.run_tests()
        assert "EXIT_CODE=0" in result

    def test_failing_test(self, git_repo):
        tools = RepoTools(git_repo, "false")
        result = tools.run_tests()
        assert "EXIT_CODE=1" in result

    def test_test_output_captured(self, git_repo):
        tools = RepoTools(git_repo, "echo hello_test")
        result = tools.run_tests()
        assert "hello_test" in result
        assert "EXIT_CODE=0" in result


GO_FILE = """package firewall

type Service struct {
\tName string
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


class TestReadSymbol:
    def _make_tools(self, git_repo):
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(GO_FILE)
        idx = SymbolIndex(
            symbols=[
                Symbol("Service", "struct", "fw/service.go", 3, 5),
                Symbol("NewService", "function", "fw/service.go", 7, 9),
                Symbol(
                    "Status", "method", "fw/service.go", 11, 13, receiver="*Service"
                ),
                Symbol("ApplyPolicy", "function", "fw/service.go", 15, 17),
            ]
        )
        return RepoTools(git_repo, "", symbol_index=idx)

    def test_read_symbol_returns_range(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/service.go", "ApplyPolicy")
        assert "SYMBOL: ApplyPolicy" in result
        assert "func ApplyPolicy" in result
        assert "LINES: 15-17" in result

    def test_read_symbol_adds_context(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/service.go", "ApplyPolicy")
        lines = result.splitlines()
        body_lines = [
            l
            for l in lines
            if l.strip()
            and ":" in l
            and not l.startswith(("SYMBOL", "FILE", "LINES"))
        ]
        first_line_num = int(body_lines[0].strip().split(":")[0])
        last_line_num = int(body_lines[-1].strip().split(":")[0])
        assert first_line_num == 11
        assert last_line_num == 17

    def test_read_symbol_not_found(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/service.go", "NonExistent")
        assert "ERROR" in result
        assert "not found" in result

    def test_read_symbol_nonexistent_file(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/nope.go", "Foo")
        assert result.startswith("ERROR:")

    def test_read_symbol_case_insensitive(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/service.go", "applypolicy")
        assert "ApplyPolicy" in result

    def test_read_symbol_method(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/service.go", "Status")
        assert "SYMBOL: (*Service).Status" in result
        assert "func (s *Service) Status" in result

    def test_read_symbol_struct(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/service.go", "Service")
        assert "SYMBOL: Service" in result
        assert "type Service struct" in result

    def test_read_symbol_custom_context_lines(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/service.go", "ApplyPolicy", context_lines=1)
        lines = result.splitlines()
        body_lines = [
            l
            for l in lines
            if l.strip()
            and ":" in l
            and not l.startswith(("SYMBOL", "FILE", "LINES"))
        ]
        first_line_num = int(body_lines[0].strip().split(":")[0])
        assert first_line_num == 14

    def test_read_symbol_no_index(self, git_repo):
        (git_repo / "a.go").write_text("package x\n\nfunc Foo() {}\n")
        tools = RepoTools(git_repo, "")
        result = tools.read_symbol("a.go", "Foo")
        assert "ERROR" in result

    def test_read_file_still_works_as_fallback(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_file("fw/service.go")
        assert "func ApplyPolicy" in result

    def test_read_symbol_ambiguous(self, git_repo):
        idx = SymbolIndex(
            symbols=[
                Symbol("Run", "method", "fw/a.go", 10, 20, receiver="*Foo"),
                Symbol("Run", "method", "fw/a.go", 30, 40, receiver="*Bar"),
            ]
        )
        tools = RepoTools(git_repo, "", symbol_index=idx)
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "a.go").write_text(
            "package fw\n\ntype Foo struct{}\nfunc (f *Foo) Run() {}\n\ntype Bar struct{}\nfunc (b *Bar) Run() {}\n"
        )
        result = tools.read_symbol("fw/a.go", "Run")
        assert "ambiguous" in result.lower()
        assert "*Foo" in result
        assert "*Bar" in result

    def test_read_symbol_resolved_by_receiver(self, git_repo):
        idx = SymbolIndex(
            symbols=[
                Symbol("Run", "method", "fw/a.go", 10, 20, receiver="*Foo"),
                Symbol("Run", "method", "fw/a.go", 30, 40, receiver="*Bar"),
            ]
        )
        tools = RepoTools(git_repo, "", symbol_index=idx)
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "a.go").write_text(
            "package fw\n\ntype Foo struct{}\nfunc (f *Foo) Run() {}\n\ntype Bar struct{}\nfunc (b *Bar) Run() {}\n"
        )
        result = tools.read_symbol("fw/a.go", "Run", receiver="*Foo")
        assert "ERROR" not in result
        assert "(*Foo).Run" in result

    def test_read_symbol_resolved_by_kind(self, git_repo):
        idx = SymbolIndex(
            symbols=[
                Symbol("Foo", "function", "fw/a.go", 1, 5),
                Symbol("Foo", "struct", "fw/a.go", 7, 10),
            ]
        )
        tools = RepoTools(git_repo, "", symbol_index=idx)
        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "a.go").write_text(
            "package fw\n\nfunc Foo() {}\n\ntype Foo struct{}\n"
        )
        result = tools.read_symbol("fw/a.go", "Foo", kind="struct")
        assert "ERROR" not in result
        assert "type Foo struct" in result

    def test_read_symbol_new_format(self, git_repo):
        tools = self._make_tools(git_repo)
        result = tools.read_symbol("fw/service.go", "ApplyPolicy")
        assert "FILE: fw/service.go" in result
        assert "SYMBOL: ApplyPolicy" in result
        assert "LINES:" in result
