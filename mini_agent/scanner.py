from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "target",
    "dist",
    "build",
    ".next",
    ".cache",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
}

LANG_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rs": "Rust",
    ".go": "Go",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
    ".bash": "Shell",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".css": "CSS",
    ".scss": "SCSS",
    ".html": "HTML",
    ".sql": "SQL",
    ".lua": "Lua",
    ".dart": "Dart",
    ".nim": "Nim",
    ".zig": "Zig",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".clj": "Clojure",
    ".hs": "Haskell",
    ".ml": "OCaml",
    ".fs": "F#",
    ".cs": "C#",
}

IMPORTANT_FILES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Pipfile",
    "poetry.lock",
    "Cargo.toml",
    "go.mod",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "CMakeLists.txt",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    "tsconfig.json",
    "jest.config.js",
    "vite.config.js",
    "webpack.config.js",
    "rollup.config.js",
    "README.md",
    "README.rst",
    "README.txt",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "LICENSE",
    "tox.ini",
    "noxfile.py",
    "ruff.toml",
    ".flake8",
    "mypy.ini",
    ".pre-commit-config.yaml",
}


@dataclass
class RepoScan:
    files: list[str]
    languages: dict[str, int]
    important_files: list[str]
    test_files: list[str]
    git_status: str | None
    truncated: bool


def _detect_language(path: Path) -> str | None:
    return LANG_BY_EXT.get(path.suffix.lower())


def _is_test_file(rel: str) -> bool:
    name = Path(rel).name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    return name.endswith(("_test.py", ".test.js", ".test.ts", "_test.go"))


def _is_important(rel: str) -> bool:
    return Path(rel).name in IMPORTANT_FILES


def _iter_files(repo: Path) -> Iterator[Path]:
    for entry in sorted(repo.iterdir(), key=lambda p: p.name):
        if entry.name in IGNORED_DIRS:
            continue
        if entry.is_dir() and not entry.is_symlink():
            yield from _iter_files(entry)
        elif entry.is_file():
            yield entry


def git_status(repo: Path) -> str | None:
    if not (repo / ".git").exists():
        return None
    proc = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def scan_repository(repo: Path, max_files: int = 500) -> RepoScan:
    repo = repo.resolve()
    files: list[str] = []
    languages: dict[str, int] = {}
    important_files: list[str] = []
    test_files: list[str] = []
    truncated = False

    for full in _iter_files(repo):
        if len(files) >= max_files:
            truncated = True
            break
        try:
            rel = str(full.relative_to(repo))
        except ValueError:
            continue
        files.append(rel)
        lang = _detect_language(full)
        if lang:
            languages[lang] = languages.get(lang, 0) + 1
        if _is_important(rel):
            important_files.append(rel)
        if _is_test_file(rel):
            test_files.append(rel)

    languages = dict(sorted(languages.items(), key=lambda kv: (-kv[1], kv[0])))
    return RepoScan(
        files=sorted(files),
        languages=languages,
        important_files=sorted(important_files),
        test_files=sorted(test_files),
        git_status=git_status(repo),
        truncated=truncated,
    )
