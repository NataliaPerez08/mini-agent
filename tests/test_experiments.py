from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mini_agent.experiments import (
    ExperimentLogger,
    ExperimentRun,
    RunMetrics,
    capture_git_metadata,
    generate_run_id,
    now_iso,
)
from mini_agent.version import HARNESS_VERSION

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line]


def _make_run(
    run_id: str = "test123",
    success: bool = True,
    final_state: str = "done",
) -> ExperimentRun:
    return ExperimentRun(
        run_id=run_id,
        timestamp=now_iso(),
        harness_version=HARNESS_VERSION,
        model="test-model",
        task="test task",
        task_type="inspect",
        success=success,
        final_state=final_state,
        metrics=RunMetrics(
            llm_calls=2,
            tool_calls=1,
            files_read=1,
            searches=0,
            patches=0,
            tests=0,
            prompt_tokens=100,
            completion_tokens=20,
            repo_map_chars=500,
            active_code_chars=300,
            working_memory_chars=100,
            elapsed_seconds=5.0,
        ),
        git_commit="abc123",
        repo_dirty=False,
        temperature=0.1,
        context_window=None,
        max_tokens=None,
    )


# --------------------------------------------------------------------------- #
# 1. run_id
# --------------------------------------------------------------------------- #


class TestGenerateRunId:
    def test_generates_valid_hex(self) -> None:
        rid = generate_run_id()
        assert len(rid) == 32
        int(rid, 16)

    def test_unique(self) -> None:
        ids = {generate_run_id() for _ in range(100)}
        assert len(ids) == 100


# --------------------------------------------------------------------------- #
# 2-3. directory creation
# --------------------------------------------------------------------------- #


class TestDirectoryCreation:
    def test_creates_experiments_and_traces(self, tmp_path: Path) -> None:
        out = tmp_path / "experiments"
        ExperimentLogger("rid", out, enabled=True)
        assert out.is_dir()
        assert (out / "traces").is_dir()

    def test_creates_nested_dirs(self, tmp_path: Path) -> None:
        out = tmp_path / "a" / "b" / "experiments"
        ExperimentLogger("rid", out, enabled=True)
        assert out.is_dir()
        assert (out / "traces").is_dir()


# --------------------------------------------------------------------------- #
# 4-5. save_summary
# --------------------------------------------------------------------------- #


class TestSaveSummary:
    def test_adds_one_valid_json_line(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid1", tmp_path / "exp", enabled=True)
        logger.save_summary(_make_run(run_id="rid1"))
        lines = _read_jsonl(logger.runs_path)
        assert len(lines) == 1
        rec = lines[0]
        assert rec["run_id"] == "rid1"
        assert rec["success"] is True
        assert rec["final_state"] == "done"
        assert rec["model"] == "test-model"
        assert rec["task"] == "test task"
        assert rec["task_type"] == "inspect"
        assert rec["llm_calls"] == 2
        assert rec["tool_calls"] == 1
        assert rec["prompt_tokens"] == 100
        assert rec["completion_tokens"] == 20
        assert rec["elapsed_seconds"] == 5.0
        assert rec["harness_version"] == HARNESS_VERSION
        assert rec["git_commit"] == "abc123"
        assert rec["repo_dirty"] is False
        assert rec["temperature"] == 0.1

    def test_two_runs_generate_two_lines(self, tmp_path: Path) -> None:
        out = tmp_path / "exp"
        logger1 = ExperimentLogger("rid1", out, enabled=True)
        logger1.save_summary(_make_run(run_id="rid1"))
        logger2 = ExperimentLogger("rid2", out, enabled=True)
        logger2.save_summary(_make_run(run_id="rid2"))
        lines = _read_jsonl(out / "runs.jsonl")
        assert len(lines) == 2
        assert lines[0]["run_id"] == "rid1"
        assert lines[1]["run_id"] == "rid2"

    def test_summary_is_flat(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        logger.save_summary(_make_run())
        rec = _read_jsonl(logger.runs_path)[0]
        assert "metrics" not in rec
        for key in (
            "llm_calls",
            "tool_calls",
            "files_read",
            "searches",
            "patches",
            "tests",
            "prompt_tokens",
            "completion_tokens",
            "repo_map_chars",
            "active_code_chars",
            "working_memory_chars",
            "elapsed_seconds",
        ):
            assert key in rec


# --------------------------------------------------------------------------- #
# 6-8. log_event
# --------------------------------------------------------------------------- #


class TestLogEvent:
    def test_adds_valid_events(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        logger.log_event("run_start", task="hello", model="m")
        logger.log_event("llm_call", call_index=1, state="read")
        events = _read_jsonl(logger.trace_path)
        assert len(events) == 2
        assert events[0]["type"] == "run_start"
        assert events[1]["type"] == "llm_call"

    def test_events_carry_run_id(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("myrid", tmp_path / "exp", enabled=True)
        logger.log_event("run_start")
        logger.log_event("run_end", success=True)
        events = _read_jsonl(logger.trace_path)
        for e in events:
            assert e["run_id"] == "myrid"

    def test_events_carry_timestamp(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        logger.log_event("run_start")
        events = _read_jsonl(logger.trace_path)
        ts = events[0]["timestamp"]
        assert "T" in ts
        assert ts.endswith(("+00:00", "Z"))

    def test_events_carry_elapsed_seconds(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        logger.log_event("run_start")
        events = _read_jsonl(logger.trace_path)
        assert "elapsed_seconds" in events[0]
        assert isinstance(events[0]["elapsed_seconds"], (int, float))


# --------------------------------------------------------------------------- #
# 9-10. git metadata
# --------------------------------------------------------------------------- #


class TestGitMetadata:
    def test_works_in_git_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@e.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
        (repo / "file.txt").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

        commit, dirty = capture_git_metadata(repo)
        assert commit is not None
        assert len(commit) == 40
        assert dirty is False

        (repo / "file.txt").write_text("modified\n")
        commit2, dirty2 = capture_git_metadata(repo)
        assert commit2 == commit
        assert dirty2 is True

    def test_fails_cleanly_outside_git(self, tmp_path: Path) -> None:
        non_git = tmp_path / "notgit"
        non_git.mkdir()
        commit, dirty = capture_git_metadata(non_git)
        assert commit is None
        assert dirty is None


# --------------------------------------------------------------------------- #
# 11. logger disabled
# --------------------------------------------------------------------------- #


class TestDisabledLogger:
    def test_disabled_does_not_write(self, tmp_path: Path) -> None:
        out = tmp_path / "exp"
        logger = ExperimentLogger("rid", out, enabled=False)
        logger.log_event("run_start", task="t")
        logger.save_summary(_make_run())
        assert not out.exists()
        assert not (out / "runs.jsonl").exists()


# --------------------------------------------------------------------------- #
# 12. write failure does not crash
# --------------------------------------------------------------------------- #


class TestWriteFailure:
    def test_log_event_swallows_error(self, tmp_path: Path) -> None:
        out = tmp_path / "exp"
        logger = ExperimentLogger("rid", out, enabled=True)
        logger.trace_path = tmp_path / "nonexistent_dir" / "trace.jsonl"
        logger.log_event("run_start", task="t")

    def test_save_summary_swallows_error(self, tmp_path: Path) -> None:
        out = tmp_path / "exp"
        logger = ExperimentLogger("rid", out, enabled=True)
        logger.runs_path = tmp_path / "nonexistent_dir" / "runs.jsonl"
        logger.save_summary(_make_run())


# --------------------------------------------------------------------------- #
# 13. Unicode
# --------------------------------------------------------------------------- #


class TestUnicode:
    def test_unicode_in_events(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        logger.log_event("run_start", task="Revisa qué hay en la carpeta ñ")
        events = _read_jsonl(logger.trace_path)
        assert events[0]["task"] == "Revisa qué hay en la carpeta ñ"

    def test_unicode_in_summary(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        run = _make_run()
        run.task = "Tarea con ñ y emojis"
        logger.save_summary(run)
        rec = _read_jsonl(logger.runs_path)[0]
        assert rec["task"] == "Tarea con ñ y emojis"

    def test_raw_bytes_are_utf8(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        logger.log_event("run_start", task="日本語")
        raw = logger.trace_path.read_bytes()
        raw.decode("utf-8")


# --------------------------------------------------------------------------- #
# 14. no secrets
# --------------------------------------------------------------------------- #


class TestNoSecrets:
    def test_api_key_not_in_logs(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        secret_key = "sk-super-secret-key-12345"
        logger.log_event(
            "run_start",
            task="test",
            model="test-model",
            harness_version="v2",
        )
        logger.save_summary(_make_run())
        trace_raw = logger.trace_path.read_text(encoding="utf-8")
        runs_raw = logger.runs_path.read_text(encoding="utf-8")
        assert secret_key not in trace_raw
        assert secret_key not in runs_raw

    def test_no_authorization_in_logs(self, tmp_path: Path) -> None:
        logger = ExperimentLogger("rid", tmp_path / "exp", enabled=True)
        logger.log_event("llm_call", call_index=1, state="read")
        events = _read_jsonl(logger.trace_path)
        event_json = json.dumps(events[0])
        assert "Authorization" not in event_json
        assert "Bearer" not in event_json
        assert "api_key" not in event_json
        assert "api-key" not in event_json


# --------------------------------------------------------------------------- #
# Integration test
# --------------------------------------------------------------------------- #


class TestIntegrationInspectRun:
    def test_inspect_run_produces_logs(
        self, git_repo_with_files: Path
    ) -> None:
        from mini_agent.agent import CodingAgent
        from tests.conftest import StubModel

        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            responses=['{"action": "finish", "summary": "Revisado"}']
        )
        agent = CodingAgent(
            git_repo_with_files,
            model,
            test_command="",
            verbose=False,
            experiment_logger=logger,
        )
        result = agent.run("Revisa qué hay en la carpeta")

        # -- runs.jsonl -- #
        runs = _read_jsonl(out / "runs.jsonl")
        assert len(runs) == 1
        rec = runs[0]
        assert rec["run_id"] == run_id
        assert rec["final_state"] == "done"
        assert rec["success"] is True
        assert rec["task_type"] == "inspect"
        assert rec["llm_calls"] == result.llm_calls
        assert rec["model"] == "qwen2.5-coder:7b"
        assert rec["harness_version"] == HARNESS_VERSION
        assert rec["git_commit"] is not None
        assert rec["temperature"] == 0.1

        # -- trace -- #
        trace = _read_jsonl(logger.trace_path)
        assert len(trace) >= 3
        assert trace[0]["type"] == "run_start"
        assert trace[0]["run_id"] == run_id
        assert trace[0]["task"] == "Revisa qué hay en la carpeta"

        types = [e["type"] for e in trace]
        assert "llm_call" in types
        assert "finish" in types
        assert types[-1] == "run_end"

        run_end = trace[-1]
        assert run_end["success"] is True
        assert run_end["final_state"] == "done"


class TestSymbolMetrics:
    def test_run_summary_has_symbol_fields(self, git_repo_with_files: Path) -> None:
        from mini_agent.agent import CodingAgent
        from tests.conftest import StubModel

        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            responses=['{"action": "finish", "summary": "Done"}']
        )
        agent = CodingAgent(
            git_repo_with_files,
            model,
            test_command="",
            experiment_logger=logger,
        )
        agent.run("Revisa")

        runs = _read_jsonl(out / "runs.jsonl")
        rec = runs[0]
        assert "symbol_index_files" in rec
        assert "symbol_index_symbols" in rec
        assert "symbol_index_errors" in rec
        assert "symbol_index_truncated" in rec
        assert "symbol_reads" in rec
        assert "full_file_reads" in rec
        assert "symbol_context_chars" in rec
        assert "full_file_context_chars" in rec

    def test_symbol_index_trace_event(self, git_repo_with_files: Path) -> None:
        from mini_agent.agent import CodingAgent
        from tests.conftest import StubModel

        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            responses=['{"action": "finish", "summary": "Done"}']
        )
        agent = CodingAgent(
            git_repo_with_files,
            model,
            test_command="",
            experiment_logger=logger,
        )
        agent.run("Revisa")

        trace = _read_jsonl(logger.trace_path)
        si_events = [e for e in trace if e["type"] == "symbol_index"]
        assert len(si_events) == 1
        evt = si_events[0]
        assert "files_parsed" in evt
        assert "symbols" in evt
        assert "errors" in evt
        assert "truncated" in evt

    def test_symbol_read_trace_event(self, git_repo: Path) -> None:
        from mini_agent.agent import CodingAgent
        from tests.conftest import StubModel

        (git_repo / "fw").mkdir()
        (git_repo / "fw" / "service.go").write_text(
            "package fw\n\nfunc ApplyPolicy() error {\n\treturn nil\n}\n"
        )
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)

        out = git_repo.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            responses=[
                '{"action": "read_symbol", "path": "fw/service.go", "symbol": "ApplyPolicy"}',
                '{"action": "finish", "summary": "Done"}',
            ]
        )
        agent = CodingAgent(
            git_repo,
            model,
            test_command="",
            experiment_logger=logger,
        )
        result = agent.run("Revisa ApplyPolicy")

        trace = _read_jsonl(logger.trace_path)
        sr_events = [e for e in trace if e["type"] == "symbol_read"]
        assert len(sr_events) == 1
        evt = sr_events[0]
        assert evt["path"] == "fw/service.go"
        assert evt["symbol"] == "ApplyPolicy"
        assert evt["success"] is True

        assert result.symbol_reads == 1
        assert result.full_file_reads == 0
        assert result.symbol_context_chars > 0

    def test_full_file_read_not_counted_as_symbol(self, git_repo_with_files: Path) -> None:
        from mini_agent.agent import CodingAgent
        from tests.conftest import StubModel

        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            responses=[
                '{"action": "read_file", "path": "src/app.py"}',
                '{"action": "finish", "summary": "Done"}',
            ]
        )
        agent = CodingAgent(
            git_repo_with_files,
            model,
            test_command="",
            experiment_logger=logger,
        )
        result = agent.run("Read app.py")

        assert result.symbol_reads == 0
        assert result.full_file_reads == 1
        assert result.full_file_context_chars > 0

    def test_duplicate_action_trace_event(self, git_repo_with_files: Path) -> None:
        from mini_agent.agent import CodingAgent
        from tests.conftest import StubModel

        out = git_repo_with_files.parent / "experiments"
        run_id = generate_run_id()
        logger = ExperimentLogger(run_id, out, enabled=True)

        model = StubModel(
            responses=[
                '{"action": "read_file", "path": "src/app.py"}',
                '{"action": "read_file", "path": "src/app.py"}',
                '{"action": "finish", "summary": "Done"}',
            ]
        )
        agent = CodingAgent(
            git_repo_with_files,
            model,
            test_command="",
            experiment_logger=logger,
        )
        agent.run("Read twice")

        trace = _read_jsonl(logger.trace_path)
        dup_events = [e for e in trace if e["type"] == "duplicate_action"]
        assert len(dup_events) == 1
        assert dup_events[0]["action"] == "read_file"
        assert dup_events[0]["path"] == "src/app.py"
