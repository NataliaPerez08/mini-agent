import pytest

from mini_agent.cli import build_parser


class TestBuildParser:
    def test_required_args(self):
        parser = build_parser()
        args = parser.parse_args(["--repo", "/tmp/x", "--task", "do something"])
        assert args.repo == "/tmp/x"
        assert args.task == "do something"

    def test_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["--repo", "/tmp/x", "--task", "t"])
        assert args.base_url == "http://localhost:11434/v1"
        assert args.model == "qwen2.5-coder:7b"
        assert args.api_key == "local"
        assert args.test_command == ""
        assert args.max_iterations == 12
        assert args.experiment_log is True
        assert args.experiments_dir == "./experiments"

    def test_no_experiment_log(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--repo", "/tmp/x", "--task", "t", "--no-experiment-log"]
        )
        assert args.experiment_log is False

    def test_explicit_experiment_log(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--repo", "/tmp/x", "--task", "t", "--experiment-log"]
        )
        assert args.experiment_log is True

    def test_custom_experiments_dir(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--repo", "/tmp/x", "--task", "t", "--experiments-dir", "/tmp/exp"]
        )
        assert args.experiments_dir == "/tmp/exp"

    def test_custom_values(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--repo",
                "/tmp/x",
                "--task",
                "t",
                "--base-url",
                "http://localhost:8080/v1",
                "--model",
                "custom-model",
                "--api-key",
                "secret",
                "--test-command",
                "pytest -q",
                "--max-iterations",
                "5",
            ]
        )
        assert args.base_url == "http://localhost:8080/v1"
        assert args.model == "custom-model"
        assert args.api_key == "secret"
        assert args.test_command == "pytest -q"
        assert args.max_iterations == 5

    def test_missing_required_args(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


class TestMain:
    def test_nonexistent_dir_exits(self):
        import sys

        from mini_agent.cli import main

        old = sys.argv
        sys.argv = ["mini-agent", "--repo", "/nonexistent/path/xyz", "--task", "t"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert "Not a directory" in str(exc_info.value)
        finally:
            sys.argv = old

    def test_non_git_dir_exits(self, tmp_path):
        import sys

        from mini_agent.cli import main

        d = tmp_path / "notgit"
        d.mkdir()
        old = sys.argv
        sys.argv = ["mini-agent", "--repo", str(d), "--task", "t"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert "git" in str(exc_info.value).lower()
        finally:
            sys.argv = old
