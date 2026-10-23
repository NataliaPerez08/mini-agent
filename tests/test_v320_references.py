"""Regression tests for v3.2.0 — reference index (v5-lite).

* extract_file_references records call sites per file (Python AST,
  Go tree-sitter): callee name -> caller key ("rel::Symbol" or "rel").
* build_symbol_index aggregates references; reindex_files refreshes
  them for patched files.
* find_exact_task_symbols disambiguates same-name symbols by
  same-directory call sites when the task names no file
  (CURRENT_ISSUES 4.2).
* The focused repo map annotates focus symbols with their callers.
"""

import json
import subprocess
from pathlib import Path

import pytest

from mini_agent.agent import CodingAgent
from mini_agent.references import aggregate_references, extract_file_references
from mini_agent.repo_map import build_repo_map
from mini_agent.scanner import scan_repository
from mini_agent.symbols import (
    _go_available,
    build_symbol_index,
    find_exact_task_symbols,
    reindex_files,
)
from tests.conftest import StubModel

MODIFY_TASK = "Corrige Validate para que devuelva un error cuando rules esté vacío"


def _repo_two_validates(git_repo: Path) -> Path:
    """engine.Validate is called from its own package; handler.Validate
    is not called from its directory."""
    repo = git_repo
    (repo / "internal").mkdir()
    engine = repo / "internal" / "engine"
    engine.mkdir()
    (engine / "validator.go").write_text(
        "package engine\n"
        "\n"
        "func Validate(rules []int) error {\n"
        "    return nil\n"
        "}\n"
        "\n"
        "func CheckAll(rules []int) error {\n"
        "    return Validate(rules)\n"
        "}\n"
    )
    handlers = repo / "internal" / "handlers"
    handlers.mkdir()
    (handlers / "policy.go").write_text(
        "package handlers\n"
        "\n"
        "func Validate(rules []int) error {\n"
        "    return nil\n"
        "}\n"
    )
    (repo / "go.mod").write_text("module demo\n\ngo 1.22\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


class TestReferenceExtraction:
    def test_python_references(self, tmp_path: Path) -> None:
        f = tmp_path / "app.py"
        f.write_text(
            "def target():\n"
            "    return 1\n"
            "\n"
            "def caller():\n"
            "    return target() + helper.method()\n"
            "\n"
            "setup()\n"
        )
        refs = extract_file_references(f, "app.py")
        assert ("target", "app.py::caller") in refs
        assert ("method", "app.py::caller") in refs
        assert ("setup", "app.py") in refs

    @pytest.mark.skipif(not _go_available, reason="tree-sitter-go not available")
    def test_go_references(self, tmp_path: Path) -> None:
        f = tmp_path / "validator.go"
        f.write_text(
            "package engine\n"
            "\n"
            "func Validate(rules []int) error {\n"
            "    return nil\n"
            "}\n"
            "\n"
            "func CheckAll(rules []int) error {\n"
            "    return Validate(rules)\n"
            "}\n"
        )
        refs = extract_file_references(f, "validator.go")
        assert ("Validate", "validator.go::CheckAll") in refs

    def test_aggregate_dedupe_and_cap(self) -> None:
        per_file = [
            [("a", "f1::one"), ("a", "f1::one"), ("a", "f2::two")],
            [("a", f"f3::c{i}") for i in range(30)],
        ]
        agg = aggregate_references(per_file)
        assert agg["a"][:3] == ["f1::one", "f2::two", "f3::c0"]
        assert len(agg["a"]) == 20


class TestIndexReferences:
    def test_build_populates_references(self, git_repo: Path) -> None:
        repo = _repo_two_validates(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        assert index.references.get("Validate") == [
            "internal/engine/validator.go::CheckAll"
        ]

    def test_reindex_refreshes_patched_file_refs(
        self, git_repo: Path
    ) -> None:
        repo = _repo_two_validates(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        assert index.references.get("Validate") == [
            "internal/engine/validator.go::CheckAll"
        ]

        # patch: CheckAll no longer calls Validate; a new caller does
        (repo / "internal" / "engine" / "validator.go").write_text(
            "package engine\n"
            "\n"
            "func Validate(rules []int) error {\n"
            "    return nil\n"
            "}\n"
            "\n"
            "func NewCaller(rules []int) error {\n"
            "    return Validate(rules)\n"
            "}\n"
        )
        index = reindex_files(
            repo, index, ["internal/engine/validator.go"]
        )
        assert index.references.get("Validate") == [
            "internal/engine/validator.go::NewCaller"
        ]

    def test_callers_of_ambiguity_aware(self, git_repo: Path) -> None:
        repo = _repo_two_validates(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        # two Validate symbols exist: only same-dir callers count
        assert index.callers_of("internal/engine/validator.go", "Validate") == [
            "internal/engine/validator.go::CheckAll"
        ]
        assert index.callers_of("internal/handlers/policy.go", "Validate") == []
        # unique name: all references count
        assert index.callers_of(
            "internal/engine/validator.go", "CheckAll"
        ) == []


class TestDisambiguationTiebreak:
    def test_same_dir_callers_win_without_path_mention(
        self, git_repo: Path
    ) -> None:
        repo = _repo_two_validates(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        # no file named in the task: the referenced Validate wins over
        # index order
        resolved = find_exact_task_symbols(
            "Corrige Validate para validar rules", index
        )
        assert len(resolved) == 1
        assert resolved[0].path == "internal/engine/validator.go"

    def test_path_mention_still_wins(self, git_repo: Path) -> None:
        repo = _repo_two_validates(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        resolved = find_exact_task_symbols(
            "Corrige Validate en internal/handlers/policy.go", index
        )
        assert resolved[0].path == "internal/handlers/policy.go"


class TestFocusedMapCallerAnnotation:
    def test_focus_symbol_annotated_with_callers(
        self, git_repo: Path
    ) -> None:
        repo = _repo_two_validates(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        repo_map = build_repo_map(
            repo,
            scan,
            symbol_index=index,
            focus_files=["internal/engine/validator.go"],
            focus_symbols=["internal/engine/validator.go::Validate"],
        )
        assert "<- called by: CheckAll (internal/engine/validator.go)" in repo_map

    def test_ambiguous_focus_symbol_shows_same_dir_only(
        self, git_repo: Path
    ) -> None:
        repo = _repo_two_validates(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        repo_map = build_repo_map(
            repo,
            scan,
            symbol_index=index,
            focus_files=["internal/handlers/policy.go"],
            focus_symbols=["internal/handlers/policy.go::Validate"],
        )
        # handler's Validate has no same-dir callers: no annotation,
        # and never the engine's caller
        assert "<- called by" not in repo_map


PATCH_VALIDATE = (
    "--- a/internal/engine/validator.go\n"
    "+++ b/internal/engine/validator.go\n"
    "@@ -1,5 +1,6 @@\n"
    " package engine\n"
    " \n"
    " func Validate(rules []int) error {\n"
    "+    _ = rules\n"
    "     return nil\n"
    " }\n"
)


class TestIntegrationAutoLoadResolvesReferenced:
    def test_modify_run_loads_referenced_symbol(self, git_repo: Path) -> None:
        repo = _repo_two_validates(git_repo)
        model = StubModel(
            [
                json.dumps(
                    {"action": "plan", "files": [], "summary": "s"}
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_VALIDATE}),
                json.dumps({"action": "finish", "summary": "ok"}),
            ]
        )
        agent = CodingAgent(repo, model, "false")
        agent.run(MODIFY_TASK)
        assert agent.symbol_index.references.get("Validate") == [
            "internal/engine/validator.go::CheckAll"
        ]
        # the auto-loaded symbol is the referenced one, not the
        # handler's index-order Validate
        assert (
            "read_symbol internal/engine/validator.go::Validate"
            in agent.working_context.symbol_snippets
        )
        assert "<- called by: CheckAll" in agent.repo_map
