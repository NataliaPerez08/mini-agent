from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .symbols import SymbolIndex, strip_receiver_prefix

SYMBOL_CONTEXT_LINES = 4


def normalize_patch(patch: str) -> str:
    """Repair common small-model unified-diff defects before git apply.

    Context lines must start with a space marker; models often emit the
    raw code line (e.g. a tab) or an empty line instead. Hunk line
    counts are recomputed from the actual content (models miscount).
    Well-formed patches pass through unchanged.
    """
    lines = patch.split("\n")
    trailing_newline = lines and lines[-1] == ""
    if trailing_newline:
        lines.pop()
    out: list[str] = []
    hunk: list[str] = []

    def flush_hunk() -> None:
        if not hunk:
            return
        header = hunk[0]
        old = sum(
            1
            for l in hunk[1:]
            if l[:1] in (" ", "-") and not l.startswith("---")
        )
        new = sum(
            1
            for l in hunk[1:]
            if l[:1] in (" ", "+") and not l.startswith("+++")
        )
        m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
        if m:
            header = f"@@ -{m.group(1)},{old} +{m.group(2)},{new} @@{header.split('@@', 2)[2]}"
        out.extend([header, *hunk[1:]])
        hunk.clear()

    for line in lines:
        if line.startswith("@@"):
            flush_hunk()
            hunk.append(line)
        elif line.startswith("diff --git"):
            flush_hunk()
            out.append(line)
        elif hunk:
            if not line:
                hunk.append(" ")
            elif line[0] not in " +-\\":
                hunk.append(" " + line)
            else:
                hunk.append(line)
        else:
            out.append(line)
    flush_hunk()
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

    def find_files_with_line(self, line: str) -> list[str]:
        """Repo-relative paths of files containing a line, matching any
        whitespace run (the model often writes spaces where the file has
        tabs)."""
        rx = re.escape(" ".join(line.split())).replace(r"\ ", r"\s+")
        proc = subprocess.run(
            [
                "rg",
                "-l",
                "--hidden",
                "--glob",
                "!.git",
                "--",
                rx,
                ".",
            ],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        return sorted(
            p[2:] if p.startswith("./") else p
            for p in proc.stdout.strip().splitlines()
        )

    def _git_diff(self) -> str:
        proc = subprocess.run(
            ["git", "diff", "--"],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.stdout

    def _apply_fingerprint(self, patch: str) -> str:
        """Working-tree state relevant to one apply.

        git diff alone is blind to untracked files (an anchored change
        to an untracked target would look like a no-op), so the patch's
        target files are fingerprinted alongside the diff.
        """
        parts = [self._git_diff()]
        targets = re.findall(r"^(?:--- a/|\+\+\+ b/)(\S+)", patch, re.MULTILINE)
        for target in dict.fromkeys(targets):
            try:
                fpath = self._safe_path(target)
                parts.append(fpath.read_bytes().decode("utf-8", "replace"))
            except (ValueError, OSError):
                pass
        return "\x00".join(parts)

    def apply_patch(self, patch: str) -> str:
        state_before = self._apply_fingerprint(patch)
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
            rev = subprocess.run(
                [
                    "git",
                    "apply",
                    "--reverse",
                    "--check",
                    "--whitespace=nowarn",
                    "--ignore-whitespace",
                    "--recount",
                    "-",
                ],
                cwd=self.repo,
                input=normalize_patch(patch),
                text=True,
                capture_output=True,
                check=False,
            )
            if rev.returncode == 0:
                return (
                    "PATCH ALREADY APPLIED\n"
                    "This exact change is already present in the working tree."
                )
            out = self._anchor_apply(normalize_patch(patch))
            if out:
                if self._apply_fingerprint(patch) == state_before:
                    return "PATCH NO-OP\n" + self._git_diff()[-12000:]
                return "PATCH APPLIED\n" + self._git_diff()[-12000:]
            m = re.search(r"patch failed: (\S+):(\d+)", proc.stderr)
            out = f"PATCH FAILED\n{proc.stderr.strip()}"
            if m:
                fpath = self._safe_path(m.group(1))
                if fpath.is_file():
                    lines = fpath.read_text(errors="replace").splitlines()
                    line = int(m.group(2))
                    lo, hi = max(1, line - 8), min(len(lines), line + 8)
                    ctx = "\n".join(
                        f"{i}: {lines[i - 1]}" for i in range(lo, hi + 1)
                    )
                    out += (
                        f"\n\nCURRENT CONTENT of {m.group(1)} around line "
                        f"{line} (your patch context does not match this):\n{ctx}"
                    )
            return out
        if self._apply_fingerprint(patch) == state_before:
            return "PATCH NO-OP\n" + self._git_diff()[-12000:]
        return "PATCH APPLIED\n" + self._git_diff()[-12000:]

    def _anchor_apply(self, patch: str) -> bool:
        """Last-resort apply anchored on each change cluster's removal
        lines.

        Small models write correct removal/addition lines but omit
        context lines in the middle of the hunk (git apply rejects
        that). A cluster is a consecutive run of -/+ lines. When a
        cluster's removal lines appear consecutively at exactly one
        whitespace-insensitive file position, and the nearest context
        line above the cluster exists near that position, replace the
        removal span with the addition lines.
        """
        target = self._patch_target(patch)
        if not target:
            return False
        fpath = self._safe_path(target)
        if not fpath.is_file():
            return False
        clusters: list[tuple[str, list[str], list[str]]] = []
        leading = ""
        rems: list[str] = []
        adds: list[str] = []

        def flush() -> None:
            nonlocal rems, adds
            if rems or adds:
                clusters.append((leading, rems, adds))
            rems, adds = [], []

        in_hunk = False
        for line in patch.splitlines():
            if line.startswith(("--- ", "+++ ")):
                continue
            if line.startswith("@@"):
                flush()
                in_hunk = True
                leading = ""
            elif not in_hunk:
                continue
            elif line.startswith("-"):
                rems.append(line[1:])
            elif line.startswith("+"):
                adds.append(line[1:])
            else:
                flush()
                leading = line[1:]
        flush()
        if not clusters:
            return False

        def norm(s: str) -> str:
            return " ".join(s.split())

        file_lines = fpath.read_text(errors="replace").splitlines()
        for leading, rems, adds in clusters:
            normalized = [norm(l) for l in file_lines]
            if not rems:
                # Insertion-only cluster: anchor on the preceding
                # context line. No context, or an ambiguous one, fails
                # loudly rather than guessing a position.
                if not adds or not leading.strip():
                    return False
                anchors = [
                    i
                    for i, l in enumerate(normalized)
                    if l == norm(leading)
                ]
                if len(anchors) != 1:
                    return False
                pos = anchors[0] + 1
                file_lines[pos:pos] = adds
                continue
            if not any(r.strip() for r in rems):
                return False
            want = [norm(r) for r in rems]
            matches = [
                i
                for i in range(len(file_lines) - len(want) + 1)
                if normalized[i : i + len(want)] == want
            ]
            if len(matches) != 1:
                return False
            pos = matches[0]
            if leading.strip():
                window = normalized[max(0, pos - 10) : pos + 10]
                if norm(leading) not in window:
                    return False
            file_lines[pos : pos + len(rems)] = adds
        fpath.write_text("\n".join(file_lines) + "\n")
        return True

    def _patch_target(self, patch: str) -> str:
        m = re.search(r"--- a/(\S+)", patch)
        return m.group(1) if m else ""

    def _go_test_command(self, test_files: list[str]) -> str:
        """Build a package-targeted `go test` for the given test files.

        Test files are repo-relative; the package path must be relative
        to the module root (the nearest ancestor holding go.mod), and a
        non-root module needs a `cd` prefix. Returns "" when the files
        span multiple modules (fall back to the full command).
        """
        mod_dirs: set[Path] = set()
        packages: set[str] = set()
        for tf in test_files:
            pkg_dir = Path(tf).parent
            mod_dir = pkg_dir
            while str(mod_dir) != "." and not (
                self.repo / mod_dir / "go.mod"
            ).is_file():
                mod_dir = mod_dir.parent
            if str(mod_dir) == "." and not (self.repo / "go.mod").is_file():
                return ""
            mod_dirs.add(mod_dir)
            rel = pkg_dir.relative_to(mod_dir).as_posix()
            packages.add("./" if rel == "." else f"./{rel}/")
        if len(mod_dirs) > 1:
            return ""
        mod_dir = next(iter(mod_dirs))
        cd = "" if str(mod_dir) == "." else f"cd {mod_dir} && "
        return f"{cd}go test {' '.join(sorted(packages))}"

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
        elif "go test" in base:
            cmd = self._go_test_command(test_files) or base
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
