from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .symbols import SymbolIndex, strip_receiver_prefix

SYMBOL_CONTEXT_LINES = 4


def normalize_patch(patch: str) -> str:
    """Repair common small-model unified-diff defects before git apply.

    Context lines must start with a space marker; models often emit the
    raw code line (e.g. a tab) or an empty line instead. Well-formed
    patches pass through unchanged.
    """
    lines = patch.split("\n")
    trailing_newline = lines and lines[-1] == ""
    if trailing_newline:
        lines.pop()
    out: list[str] = []
    in_hunk = False
    for line in lines:
        if line.startswith("@@"):
            in_hunk = True
            out.append(line)
        elif line.startswith("diff --git"):
            in_hunk = False
            out.append(line)
        elif in_hunk:
            if not line:
                out.append(" ")
            elif line[0] not in " +-\\":
                out.append(" " + line)
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if trailing_newline else "")


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
        self,
        path: str,
        symbol_name: str,
        context_lines: int = SYMBOL_CONTEXT_LINES,
        kind: str | None = None,
        receiver: str | None = None,
    ) -> str:
        file_path = self._safe_path(path)
        if not file_path.is_file():
            return f"ERROR: not a file: {path}"
        symbol_name = strip_receiver_prefix(symbol_name)
        candidates = self.symbol_index.by_file(path)
        exact_matches = [s for s in candidates if s.name == symbol_name]
        if not exact_matches:
            lowered = symbol_name.lower()
            exact_matches = [s for s in candidates if s.name.lower() == lowered]
        if not exact_matches:
            return f"ERROR: symbol {symbol_name!r} not found in {path}"

        if kind:
            exact_matches = [s for s in exact_matches if s.kind == kind]
        if receiver:
            exact_matches = [
                s for s in exact_matches if s.receiver and s.receiver == receiver
            ]

        if len(exact_matches) > 1:
            lines_list = []
            for sym in exact_matches:
                rcv = f" ({sym.receiver})" if sym.receiver else ""
                lines_list.append(
                    f"- {sym.kind}{rcv} {sym.name} L{sym.start_line}-L{sym.end_line}"
                )
            return "ambiguous symbol:\n" + "\n".join(lines_list)

        match = exact_matches[0]
        lines = file_path.read_text(errors="replace").splitlines()
        start = max(1, match.start_line - context_lines)
        end = min(len(lines), match.end_line + context_lines)
        body = [f"{i:>5}: {lines[i - 1]}" for i in range(start, end + 1)]
        display = match.name
        if match.receiver:
            display = f"({match.receiver}).{match.name}"
        header = (
            f"FILE: {path}\n"
            f"SYMBOL: {display}\n"
            f"LINES: {match.start_line}-{match.end_line}\n\n"
        )
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
            [
                "git",
                "apply",
                "--whitespace=nowarn",
                "--ignore-whitespace",
                "-C1",
                "--recount",
                "-",
            ],
            cwd=self.repo,
            input=normalize_patch(patch),
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
        if not self.test_command.strip():
            return "NO TEST COMMAND CONFIGURED"
        if not test_files:
            return self.run_tests()
        base = self.test_command.strip()
        if "pytest" in base:
            prefix = "python -m pytest" if "-m pytest" in base else "pytest"
            cmd = f"{prefix} -q {' '.join(test_files)}"
        else:
            cmd = base
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
