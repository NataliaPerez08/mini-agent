from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from mini_agent.model import OpenAICompatModel
from mini_agent.version import HARNESS_VERSION

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPO = REPO_ROOT / "iptables-visualizer"

VALIDATOR_TEST = """package engine

import (
    "testing"

    "iptables-visualizer/internal/models"
)

func TestValidateEmptyRulesMessage(t *testing.T) {
    v := NewValidator()
    result := v.Validate(&models.Policy{
        Name:  "web",
        Rules: []models.Rule{},
    })

    if result.Valid {
        t.Error("policy without rules must be invalid")
    }

    found := false
    for _, iss := range result.Issues {
        if iss.Field == "rules" {
            found = true
            if iss.Message != "policy must have at least one rule" {
                t.Errorf("unexpected rules message: %s", iss.Message)
            }
        }
    }
    if !found {
        t.Error("expected a rules issue for policy without rules")
    }
}
"""

MODIFY_TASK = (
    "Corrige Validate en internal/engine/validator.go para que pase el test "
    "TestValidateEmptyRulesMessage: cuando una politica no tiene reglas, el "
    "issue de campo rules debe tener el mensaje 'policy must have at least "
    "one rule' y no 'policy must contain at least one rule'."
)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")


def _model_name() -> str:
    return os.environ.get("OLLAMA_TEST_MODEL", "qwen2.5-coder:14b")


def _host(base_url: str) -> str:
    return base_url.split("//", 1)[1].rsplit("/", 1)[0]


def _reachable(base_url: str) -> bool:
    try:
        httpx.get(f"{base_url}/models", timeout=3.0)
        return True
    except httpx.HTTPError:
        return False


def _model_available(base_url: str, model: str) -> bool:
    try:
        resp = httpx.get(f"{base_url}/models", timeout=10.0)
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return False
    return model in {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}


@pytest.fixture(scope="session")
def ollama_server():
    """Ensure an Ollama server is reachable; start one when allowed.

    Skips unless RUN_OLLAMA_TESTS=1 is set. Auto-start only applies to
    localhost endpoints; a user-provided OLLAMA_BASE_URL to a remote
    server is used as-is and must already be running.
    """
    if not os.environ.get("RUN_OLLAMA_TESTS"):
        pytest.skip("set RUN_OLLAMA_TESTS=1 to run live Ollama tests")
    base_url = _base_url()
    proc: subprocess.Popen | None = None
    if not _reachable(base_url):
        if not _host(base_url).startswith(("localhost", "127.0.0.1")):
            pytest.skip(
                f"Ollama not reachable at {base_url}; auto-start only "
                "supports localhost endpoints"
            )
        proc = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            if _reachable(base_url):
                break
            if proc.poll() is not None:
                pytest.skip("ollama serve exited unexpectedly")
            time.sleep(0.5)
        else:
            proc.terminate()
            proc.wait(timeout=10)
            pytest.skip(f"ollama serve did not become ready at {base_url}")
    yield base_url
    if proc is not None:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def ollama_model(ollama_server) -> str:
    base_url = ollama_server
    model = _model_name()
    if not _model_available(base_url, model):
        subprocess.run(
            ["ollama", "pull", model],
            check=True,
            timeout=int(os.environ.get("OLLAMA_PULL_TIMEOUT", "600")),
            env={**os.environ, "OLLAMA_HOST": _host(base_url)},
        )
    return model


@pytest.fixture()
def visualizer_repo(tmp_path: Path) -> Path:
    """Copy of iptables-visualizer seeded with a failing regression test.

    The seeded test fails against the current validator (empty policy name
    produces both the required-name error and the regex-format error), so
    the agent must change backend/internal/engine/validator.go and pass
    the real Go test suite.
    """
    repo = tmp_path / "iptables-visualizer"
    shutil.copytree(SOURCE_REPO, repo)
    test_path = repo / "backend" / "internal" / "engine" / "validator_test.go"
    test_path.write_text(VALIDATOR_TEST)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed validator regression test"],
        cwd=repo,
        check=True,
    )

    proc = subprocess.run(
        ["go", "test", "./internal/engine/"],
        cwd=repo / "backend",
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode != 0, (
        "seeded regression test must fail before the agent runs:\n"
        + proc.stdout
        + proc.stderr
    )
    return repo


class _LoggingModel:
    """Wraps the real model, dumping prompts/responses when
    OLLAMA_DEBUG_LOG points to a directory. For live-run diagnosis."""

    def __init__(self, inner, log_dir: Path):
        self.inner = inner
        self.log_dir = log_dir
        self.n = 0

    def __setattr__(self, name, value):
        if name != "inner" and hasattr(self, "inner"):
            setattr(self.inner, name, value)
        object.__setattr__(self, name, value)

    def chat(self, system, user):
        import time as _time

        self.n += 1
        t0 = _time.time()
        resp = self.inner.chat(system, user)
        dt = _time.time() - t0
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / f"call{self.n}.txt").write_text(
            f"=== SYSTEM ({len(system)} chars) ===\n{system}\n\n"
            f"=== USER ({len(user)} chars) ===\n{user}\n\n"
            f"=== RESPONSE ({dt:.1f}s) ===\n{resp}\n"
        )
        return resp


class TestLiveOllamaModify:
    def test_modify_validator_with_real_verification(
        self, ollama_server, ollama_model, visualizer_repo, tmp_path
    ):
        base_url = ollama_server
        model = ollama_model
        out = tmp_path / "experiments"
        logger = ExperimentLogger(generate_run_id(), out, enabled=True)
        llm = OpenAICompatModel(
            base_url,
            model,
            "local",
            timeout=float(os.environ.get("OLLAMA_TEST_TIMEOUT", "300")),
        )
        llm.temperature = 0.0
        debug_log = os.environ.get("OLLAMA_DEBUG_LOG")
        if debug_log:
            llm = _LoggingModel(llm, Path(debug_log))
        agent = CodingAgent(
            visualizer_repo,
            llm,
            "cd backend && go test ./...",
            experiment_logger=logger,
        )
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done", result.summary
        assert result.stop_reason == "completed"
        assert result.patches >= 1
        assert result.tests_run >= 1
        assert result.llm_calls >= 1

        rec = _read_jsonl(out / "runs.jsonl")[0]
        assert rec["model"] == model
        assert rec["success"] is True
        assert rec["final_state"] == "done"
        assert rec["stop_reason"] == "completed"
        assert rec["harness_version"] == HARNESS_VERSION
        assert rec["tests"] >= 1
        assert rec["patches"] >= 1

        verify = subprocess.run(
            ["go", "test", "./..."],
            cwd=visualizer_repo / "backend",
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert verify.returncode == 0, verify.stdout + verify.stderr
