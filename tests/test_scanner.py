from mini_agent.scanner import IGNORED_DIRS, RepoScan, scan_repository


class TestScanRepository:
    def test_lists_files_relative(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        assert "src/app.py" in scan.files
        assert "src/utils.py" in scan.files
        assert "README.md" in scan.files
        for f in scan.files:
            assert not f.startswith("/")

    def test_ignores_git(self, git_repo_with_files):
        (git_repo_with_files / "file.py").write_text("x\n")
        scan = scan_repository(git_repo_with_files)
        assert not any(".git" in f for f in scan.files)

    def test_ignores_venv(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        assert ".venv" not in scan.files
        assert "ignored.py" not in scan.files
        assert not any(".venv" in f for f in scan.files)

    def test_ignores_node_modules(self, git_repo):
        (git_repo / "node_modules").mkdir()
        (git_repo / "node_modules" / "pkg.js").write_text("x\n")
        (git_repo / "app.js").write_text("x\n")
        scan = scan_repository(git_repo)
        assert "app.js" in scan.files
        assert "node_modules" not in scan.files
        assert not any("node_modules" in f for f in scan.files)

    def test_max_files_limit(self, git_repo):
        for i in range(10):
            (git_repo / f"f{i}.py").write_text("x\n")
        scan = scan_repository(git_repo, max_files=5)
        assert len(scan.files) == 5
        assert scan.truncated is True

    def test_not_truncated_under_limit(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        assert scan.truncated is False

    def test_detects_python_by_extension(self, git_repo):
        (git_repo / "a.py").write_text("x\n")
        (git_repo / "b.js").write_text("x\n")
        (git_repo / "c.md").write_text("x\n")
        scan = scan_repository(git_repo)
        assert scan.languages.get("Python") == 1
        assert scan.languages.get("JavaScript") == 1
        assert scan.languages.get("Markdown") == 1

    def test_languages_sorted_by_count(self, git_repo):
        (git_repo / "a.py").write_text("x\n")
        (git_repo / "b.py").write_text("x\n")
        (git_repo / "c.md").write_text("x\n")
        scan = scan_repository(git_repo)
        keys = list(scan.languages.keys())
        assert keys[0] == "Python"
        assert scan.languages["Python"] == 2

    def test_detects_important_files(self, git_repo):
        (git_repo / "pyproject.toml").write_text("[project]\n")
        (git_repo / "README.md").write_text("# x\n")
        (git_repo / "a.py").write_text("x\n")
        scan = scan_repository(git_repo)
        assert "pyproject.toml" in scan.important_files
        assert "README.md" in scan.important_files
        assert "a.py" not in scan.important_files

    def test_detects_test_files(self, git_repo):
        (git_repo / "test_auth.py").write_text("x\n")
        (git_repo / "auth_test.py").write_text("x\n")
        (git_repo / "app.py").write_text("x\n")
        scan = scan_repository(git_repo)
        assert "test_auth.py" in scan.test_files
        assert "auth_test.py" in scan.test_files
        assert "app.py" not in scan.test_files

    def test_git_status_clean(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        assert scan.git_status == ""

    def test_git_status_dirty(self, git_repo_with_files):
        (git_repo_with_files / "new.py").write_text("x\n")
        scan = scan_repository(git_repo_with_files)
        assert scan.git_status is not None
        assert "new.py" in scan.git_status

    def test_git_status_none_for_non_git(self, tmp_path):
        d = tmp_path / "notgit"
        d.mkdir()
        (d / "a.py").write_text("x\n")
        scan = scan_repository(d)
        assert scan.git_status is None

    def test_returns_repo_scan_type(self, git_repo_with_files):
        scan = scan_repository(git_repo_with_files)
        assert isinstance(scan, RepoScan)

    def test_empty_repo(self, git_repo):
        scan = scan_repository(git_repo)
        assert scan.files == []
        assert scan.languages == {}
        assert scan.important_files == []

    def test_files_sorted(self, git_repo):
        (git_repo / "z.py").write_text("x\n")
        (git_repo / "a.py").write_text("x\n")
        (git_repo / "m.py").write_text("x\n")
        scan = scan_repository(git_repo)
        assert scan.files == sorted(scan.files)


class TestIgnoredDirs:
    def test_contains_common_dirs(self):
        for d in [".git", ".venv", "node_modules", "build", "dist", "__pycache__"]:
            assert d in IGNORED_DIRS

    def test_contains_cache_dirs(self):
        for d in [".pytest_cache", ".mypy_cache", ".cache", "coverage"]:
            assert d in IGNORED_DIRS
