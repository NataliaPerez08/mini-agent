from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    return repo


@pytest.fixture
def git_repo_with_files(git_repo: Path) -> Path:
    repo = git_repo
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def hello():\n    return 'hello'\n")
    (repo / "src" / "utils.py").write_text("PI = 3.14\n\ndef add(a, b):\n    return a + b\n")
    (repo / "README.md").write_text("# demo\n")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "ignored.py").write_text("should not appear\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


class StubModel:
    def __init__(self, responses: Sequence[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise AssertionError("StubModel exhausted")
        return self._responses.pop(0)
