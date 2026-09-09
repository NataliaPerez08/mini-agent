import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from mini_agent.cli import build_parser
from mini_agent.version import HARNESS_VERSION

INSPECT_TASK = (
    "Analiza este repositorio y explicame su arquitectura y componentes principales"
)
MODIFY_TASK = "Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío"

PATCH_APP = (
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,2 +1,4 @@\n"
    " def ApplyPolicy(ports):\n"
    "-    return 'ok'\n"
    "+    if not ports:\n"
    "+        return 'error: ports required'\n"
    "+    return 'ok'\n"
)

FINISH_RESPONSE = json.dumps(
    {"action": "finish", "summary": "El repositorio es un harness minimal."}
)
PLAN_RESPONSE = json.dumps(
    {"action": "plan", "files": ["src/app.py"], "summary": "Validate ports"}
)
PATCH_RESPONSE = json.dumps({"action": "apply_patch", "patch": PATCH_APP})


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _run_cli(
    args: list[str], *, cwd: Path, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mini_agent.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# --------------------------------------------------------------------------- #
# Fake OpenAI-compatible server
# --------------------------------------------------------------------------- #


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        server = self.server  # type: ignore[attr-defined]
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}
        server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": payload,
            }
        )
        if not server.responses:
            self._respond(500, {"error": "no canned responses left"})
            return
        content = server.responses.pop(0)
        self._respond(
            200,
            {
                "choices": [{"message": {"content": content}}],
                "usage": server.usage,
            },
        )

    def _respond(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence request logging
        pass


class _FakeOpenAIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.responses: list[str] = []
        self.requests: list[dict] = []
        self.usage = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        }
        super().__init__(("127.0.0.1", 0), _FakeOpenAIHandler)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1"


@pytest.fixture
def fake_llm_server():
    server = _FakeOpenAIServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# main() failure paths
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# End-to-end through the real CLI entry point (fake model server)
# --------------------------------------------------------------------------- #


class TestEndToEndInspect:
    def test_cli_inspect_generates_experiments(
        self, fake_llm_server, git_repo_with_files, tmp_path
    ):
        server = fake_llm_server
        server.responses = [FINISH_RESPONSE]
        out = tmp_path / "experiments"
        proc = _run_cli(
            [
                "--repo",
                str(git_repo_with_files),
                "--task",
                INSPECT_TASK,
                "--model",
                "qwen2.5-coder:7b",
                "--base-url",
                server.base_url,
                "--verbose",
                "--experiments-dir",
                str(out),
            ],
            cwd=tmp_path,
        )
        assert proc.returncode == 0, proc.stderr
        assert "[experiment]" in proc.stdout
        assert "run_id:" in proc.stdout
        assert "saved:" in proc.stdout
        assert "[1 iterations]" in proc.stdout

        runs = _read_jsonl(out / "runs.jsonl")
        assert len(runs) == 1
        rec = runs[0]
        assert rec["task"] == INSPECT_TASK
        assert rec["model"] == "qwen2.5-coder:7b"
        assert rec["task_type"] == "inspect"
        assert rec["success"] is True
        assert rec["final_state"] == "done"
        assert rec["llm_calls"] == 1
        assert rec["harness_version"] == HARNESS_VERSION
        assert rec["git_commit"]
        assert rec["prompt_tokens"] == 100
        assert rec["completion_tokens"] == 20
        assert "README.md" in rec["auto_read_files"]
        assert rec["auto_symbols"] == []

        trace = _read_jsonl(out / "traces" / f"{rec['run_id']}.jsonl")
        types = [e["type"] for e in trace]
        assert types[0] == "run_start"
        assert trace[0]["task"] == INSPECT_TASK
        assert types[-1] == "run_end"
        assert trace[-1]["success"] is True
        auto_reads = [e for e in trace if e["type"] == "auto_read"]
        assert any(e["path"] == "README.md" for e in auto_reads)
        assert types.index("auto_read") < types.index("state_transition")

        assert len(server.requests) == 1
        assert server.requests[0]["path"] == "/v1/chat/completions"
        assert server.requests[0]["body"]["model"] == "qwen2.5-coder:7b"


class TestEndToEndModify:
    def test_cli_modify_applies_patch_and_verifies(
        self, fake_llm_server, git_repo_with_symbol, tmp_path
    ):
        server = fake_llm_server
        server.responses = [PLAN_RESPONSE, PATCH_RESPONSE]
        out = tmp_path / "experiments"
        proc = _run_cli(
            [
                "--repo",
                str(git_repo_with_symbol),
                "--task",
                MODIFY_TASK,
                "--model",
                "qwen2.5-coder:7b",
                "--base-url",
                server.base_url,
                "--test-command",
                "true",
                "--experiments-dir",
                str(out),
            ],
            cwd=tmp_path,
        )
        assert proc.returncode == 0, proc.stderr

        assert "ports required" in (
            git_repo_with_symbol / "src" / "app.py"
        ).read_text()

        runs = _read_jsonl(out / "runs.jsonl")
        assert len(runs) == 1
        rec = runs[0]
        assert rec["task_type"] == "modify"
        assert rec["final_state"] == "done"
        assert rec["success"] is True
        assert rec["llm_calls"] == 2
        assert rec["patches"] == 1
        assert rec["tests"] == 1
        assert rec["auto_symbols"] == ["src/app.py::ApplyPolicy"]
        assert rec["auto_read_files"] == []

        trace = _read_jsonl(out / "traces" / f"{rec['run_id']}.jsonl")
        types = [e["type"] for e in trace]
        assert types[-1] == "run_end"
        auto_sym = [e for e in trace if e["type"] == "auto_symbol_read"]
        assert len(auto_sym) == 1
        assert auto_sym[0]["symbol"] == "ApplyPolicy"
        assert types.index("auto_symbol_read") < types.index("llm_call")
        patch_events = [e for e in trace if e["type"] == "patch" and e["success"]]
        assert len(patch_events) == 1
        assert patch_events[0]["files"] == ["src/app.py"]
        test_events = [e for e in trace if e["type"] == "test"]
        assert len(test_events) == 1
        assert test_events[0]["success"] is True


# --------------------------------------------------------------------------- #
# In-process CLI flag behavior (no network)
# --------------------------------------------------------------------------- #


class TestExperimentLogFlags:
    def test_no_experiment_log_writes_nothing(
        self, monkeypatch, git_repo_with_files, tmp_path, capsys
    ):
        from mini_agent.cli import main
        from tests.conftest import StubModel

        def _stub(base_url, model, api_key, verbose=False):
            return StubModel([FINISH_RESPONSE], model=model)

        monkeypatch.setattr("mini_agent.cli.OpenAICompatModel", _stub)
        out = tmp_path / "experiments"
        old = sys.argv
        sys.argv = [
            "mini-agent",
            "--repo",
            str(git_repo_with_files),
            "--task",
            INSPECT_TASK,
            "--no-experiment-log",
            "--experiments-dir",
            str(out),
        ]
        try:
            main()
        finally:
            sys.argv = old

        captured = capsys.readouterr()
        assert "[1 iterations]" in captured.out
        assert not (out / "runs.jsonl").exists()
        assert not (out / "traces").exists()


# --------------------------------------------------------------------------- #
# Live Ollama run (skipped unless Ollama + the model are available)
# --------------------------------------------------------------------------- #


def _ollama_endpoint() -> tuple[str, str]:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    model = os.environ.get("OLLAMA_TEST_MODEL", "qwen2.5-coder:7b")
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{base_url}/models")
    except httpx.HTTPError as exc:
        pytest.skip(f"Ollama not reachable at {base_url}: {exc}")
    if resp.status_code != 200:
        pytest.skip(f"Ollama /models returned {resp.status_code} at {base_url}")
    try:
        data = resp.json()
    except ValueError:
        pytest.skip(f"Ollama /models returned non-JSON at {base_url}")
    ids = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
    if model not in ids:
        pytest.skip(
            f"model {model!r} not pulled at {base_url} (available: {sorted(ids)})"
        )
    return base_url, model


@pytest.fixture(scope="session")
def ollama_endpoint() -> tuple[str, str]:
    return _ollama_endpoint()


class TestLiveOllama:
    def test_cli_live_inspect_generates_experiments(
        self, ollama_endpoint, git_repo_with_files, tmp_path
    ):
        base_url, model = ollama_endpoint
        out = tmp_path / "experiments"
        timeout = int(os.environ.get("OLLAMA_TEST_TIMEOUT", "300"))
        proc = _run_cli(
            [
                "--repo",
                str(git_repo_with_files),
                "--task",
                INSPECT_TASK,
                "--model",
                model,
                "--base-url",
                base_url,
                "--verbose",
                "--experiments-dir",
                str(out),
            ],
            cwd=tmp_path,
            timeout=timeout,
        )
        assert proc.returncode == 0, proc.stderr

        runs = _read_jsonl(out / "runs.jsonl")
        assert len(runs) == 1
        rec = runs[0]
        assert rec["model"] == model
        assert rec["task"] == INSPECT_TASK
        assert rec["task_type"] == "inspect"
        assert rec["harness_version"] == HARNESS_VERSION
        assert rec["git_commit"]
        assert rec["llm_calls"] >= 1
        assert rec["prompt_tokens"] > 0
        assert rec["final_state"] in ("done", "failed")
        assert "README.md" in rec["auto_read_files"]

        trace = _read_jsonl(out / "traces" / f"{rec['run_id']}.jsonl")
        types = [e["type"] for e in trace]
        assert types[0] == "run_start"
        assert types[-1] == "run_end"
        assert types.count("llm_call") >= 1
        assert trace[-1]["success"] == rec["success"]
