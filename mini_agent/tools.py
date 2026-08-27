from __future__ import annotations

import os
import subprocess
from pathlib import Path


class RepoTools:
    def __init__(self, repo: Path, test_command: str):
        self.repo = repo.resolve()
        self.test_command = test_command

    def _safe_path(self, rel: str) -> Path:
        candidate = (self.repo / rel).resolve()
        if candidate != self.repo and self.repo not in candidate.parents:
            raise ValueError("Path escapes repository root")
        return candidate

    def read_file(self, path: str, start: int = 1, end: int = 220) -> str:
        file_path = self._safe_path(path)
        if not file_path.is_file():
            return f"ERROR: not a file: {path}"
        lines = file_path.read_text(errors="replace").splitlines()
        start = max(1, start)
        end = min(len(lines), max(start, end))
        body = [f"{i:>5}: {lines[i-1]}" for i in range(start, end + 1)]
        return "\n".join(body) if body else "<empty>"

    def search_code(self, query: str, path: str = ".") -> str:
        target = self._safe_path(path)
        cmd = ["rg", "-n", "--hidden", "--glob", "!.git", "--", query, str(target)]
        proc = subprocess.run(cmd, cwd=self.repo, text=True, capture_output=True)
        out = (proc.stdout + proc.stderr).strip()
        return out[:12000] if out else "<no matches>"

    def apply_patch(self, patch: str) -> str:
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=self.repo,
            input=patch,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            return f"PATCH FAILED\n{proc.stderr.strip()}"
        diff = subprocess.run(
            ["git", "diff", "--"], cwd=self.repo, text=True, capture_output=True
        )
        return "PATCH APPLIED\n" + diff.stdout[-12000:]

    def run_tests(self) -> str:
        if not self.test_command.strip():
            return "NO TEST COMMAND CONFIGURED"
        proc = subprocess.run(
            self.test_command,
            cwd=self.repo,
            shell=True,
            text=True,
            capture_output=True,
            env={**os.environ, "CI": "1"},
            timeout=180,
        )
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        return f"EXIT_CODE={proc.returncode}\n{combined[-16000:]}"
