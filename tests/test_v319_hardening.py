"""Regression tests for v3.1.9 behavior (CURRENT_ISSUES 4.3).

* build_repo_map accepts focus_files: for localized MODIFY/DEBUG runs
  the map is narrowed to the focused file's neighborhood (max 10
  symbol files instead of 60, focus-first ordering, FILES section
  ordered by the same tiers).
* Without focus the map keeps the previous repository-wide behavior.
* The orchestrator builds the map after the exact-symbol auto-load and
  passes the auto-loaded symbol paths as focus.
* The post-patch map rebuild keeps the focus (auto symbols + patched
  files).
"""

import json
import subprocess
from pathlib import Path

from mini_agent.agent import CodingAgent
from mini_agent.repo_map import MAX_FOCUSED_FILES_IN_MAP, build_repo_map
from mini_agent.scanner import scan_repository
from mini_agent.symbols import build_symbol_index
from tests.conftest import StubModel

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


def _repo_with_neighborhood(git_repo: Path) -> Path:
    repo = git_repo
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text(
        "def ApplyPolicy(ports):\n    return 'ok'\n"
    )
    (repo / "src" / "utils.py").write_text(
        "PI = 3.14\n\ndef add(a, b):\n    return a + b\n"
    )
    (repo / "pkg").mkdir()
    for i in range(12):
        (repo / "pkg" / f"f{i}.py").write_text(
            f"def Unrelated{i}():\n    return {i}\n"
        )
    (repo / "README.md").write_text("# demo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _symbol_blocks(repo_map: str) -> list[str]:
    """File headers of the SYMBOLS section, in order."""
    symbols = repo_map.split("SYMBOLS\n", 1)[1]
    return [
        line
        for line in symbols.splitlines()
        if line and not line.startswith("  ")
    ]


class TestFocusedRepoMap:
    def test_focus_file_first_and_neighborhood_before_unrelated(
        self, git_repo: Path
    ) -> None:
        repo = _repo_with_neighborhood(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        repo_map = build_repo_map(
            repo, scan, symbol_index=index, focus_files=["src/app.py"]
        )
        blocks = _symbol_blocks(repo_map)
        assert blocks[0] == "src/app.py"
        assert blocks[1] == "src/utils.py"
        assert "pkg/f0.py" in blocks

    def test_focus_caps_symbol_files(
        self, git_repo: Path
    ) -> None:
        repo = _repo_with_neighborhood(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        focused = build_repo_map(
            repo, scan, symbol_index=index, focus_files=["src/app.py"]
        )
        unfocused = build_repo_map(repo, scan, symbol_index=index)
        assert len(_symbol_blocks(focused)) == MAX_FOCUSED_FILES_IN_MAP
        assert len(_symbol_blocks(unfocused)) == 14
        # alphabetical fillers are f0, f1, f10, f11, f2..f5; f8 falls
        # beyond the focused cap (FILES section still lists all paths)
        assert "pkg/f8.py" not in _symbol_blocks(focused)
        assert "pkg/f8.py" in _symbol_blocks(unfocused)

    def test_focus_orders_files_section(
        self, git_repo: Path
    ) -> None:
        repo = _repo_with_neighborhood(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        repo_map = build_repo_map(
            repo, scan, symbol_index=index, focus_files=["src/app.py"]
        )
        files_lines = repo_map.split("\nFILES\n", 1)[1].splitlines()
        assert files_lines[0] == "src/app.py"
        assert files_lines[1] == "src/utils.py"

    def test_no_focus_keeps_wide_map(
        self, git_repo: Path
    ) -> None:
        repo = _repo_with_neighborhood(git_repo)
        scan = scan_repository(repo)
        index = build_symbol_index(repo, scan)
        default = build_repo_map(repo, scan, symbol_index=index)
        explicit = build_repo_map(
            repo, scan, symbol_index=index, focus_files=[]
        )
        assert default == explicit


class TestOrchestratorFocusedMap:
    def test_modify_run_map_is_focused_on_task_symbol(
        self, git_repo: Path
    ) -> None:
        repo = _repo_with_neighborhood(git_repo)
        model = StubModel(
            [
                json.dumps(
                    {"action": "plan", "files": ["src/app.py"], "summary": "s"}
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "finish", "summary": "done"}),
            ]
        )
        agent = CodingAgent(repo, model, "false")
        agent.run(MODIFY_TASK)

        # first prompt carries the focused map: the task symbol's file
        # leads the SYMBOLS section
        first_prompt = model.calls[0][1]
        assert "src/app.py" in first_prompt
        blocks = _symbol_blocks(first_prompt.split("REPOSITORY MAP\n")[-1])
        assert blocks[0] == "src/app.py"

    def test_post_patch_map_keeps_focus(self, git_repo: Path) -> None:
        repo = _repo_with_neighborhood(git_repo)
        model = StubModel(
            [
                json.dumps(
                    {"action": "plan", "files": ["src/app.py"], "summary": "s"}
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "finish", "summary": "done"}),
            ]
        )
        agent = CodingAgent(repo, model, "false")
        agent.run(MODIFY_TASK)

        # post-patch rebuild: patched file keeps the focus
        blocks = _symbol_blocks(agent.repo_map)
        assert blocks[0] == "src/app.py"
        assert len(blocks) == MAX_FOCUSED_FILES_IN_MAP
