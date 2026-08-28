from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .symbols import Symbol, SymbolIndex

CONTEXT_LINES = 5


class RepoTools:
    def __init__(
        self, repo: Path, test_command: str, symbol_index: SymbolIndex | None = None
    ):
        self.repo = repo.resolve()
        self.test_command = test_command
        self.symbol_index = symbol_index or SymbolIndex()

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
        body = [f"{i:>5}: {lines[i - 1]}" for i in range(start, end + 1)]
        return "\n".join(body) if body else "<empty>"

    def read_symbol(
        self, path: str, symbol_name: str, context_lines: int = CONTEXT_LINES
    ) -> str:
        file_path = self._safe_path(path)
        if not file_path.is_file():
            return f"ERROR: not a file: {path}"
        candidates = self.symbol_index.by_file(path)
        match: Symbol | None = None
        for sym in candidates:
            if sym.name == symbol_name:
                match = sym
                break
        if match is None:
            lowered = symbol_name.lower()
            for sym in candidates:
                if sym.name.lower() == lowered:
                    match = sym
                    break
        if match is None:
            return f"ERROR: symbol {symbol_name!r} not found in {path}"
        lines = file_path.read_text(errors="replace").splitlines()
        start = max(1, match.start_line - context_lines)
        end = min(len(lines), match.end_line + context_lines)
        body = [f"{i:>5}: {lines[i - 1]}" for i in range(start, end + 1)]
        header = f"SYMBOL {symbol_name} ({match.kind}) {path} L{match.start_line}-L{match.end_line}\n"
        return header + "\n".join(body) if body else header + "<empty>"

    def search_code(self, query: str, path: str = ".") -> str:
        target = self._safe_path(path)
        cmd = ["rg", "-n", "--hidden", "--glob", "!.git", "--", query, str(target)]
        proc = subprocess.run(
            cmd, cwd=self.repo, text=True, capture_output=True, check=False
        )
        out = (proc.stdout + proc.stderr).strip()
        return out[:12000] if out else "<no matches>"

    def apply_patch(self, patch: str) -> str:
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=self.repo,
            input=patch,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return f"PATCH FAILED\n{proc.stderr.strip()}"
        diff = subprocess.run(
            ["git", "diff", "--"],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
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
            check=False,
        )
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        return f"EXIT_CODE={proc.returncode}\n{combined[-16000:]}"

    def run_targeted_tests(self, test_files: list[str]) -> str:
        if not test_files:
            return self.run_tests()
        cmd = f"pytest -q {' '.join(test_files)}"
        proc = subprocess.run(
            cmd,
            cwd=self.repo,
            shell=True,
            text=True,
            capture_output=True,
            env={**os.environ, "CI": "1"},
            timeout=180,
            check=False,
        )
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        return f"EXIT_CODE={proc.returncode}\n{combined[-16000:]}"
