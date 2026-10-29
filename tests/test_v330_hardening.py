"""Regression tests for v3.3.0 (CURRENT_ISSUES 8, zero-change patches).

Live models (H1/H2 sweep) emit patches that git accepts but that change
nothing. The harness used to accept them as successful apply_patch
actions, consuming a verification cycle and budget while giving the
model no useful feedback.

v3.3.0 rejects no-op patches deterministically. Detection compares the
working-tree diff before and after the apply: a clean-tree no-op has
the observed live signature (files=[], added=0, removed=0), but on a
dirty tree -- e.g. REPAIR after a real patch -- the accumulated diff
masked the signature, so the before/after comparison is the robust
check. The same comparison covers the anchor fallback, and the anchor
no longer reports vacuous success for patches with no change clusters.

Rejection means: no VERIFY transition, no patches_applied increment, a
structured noop_patch trace event, and a dedicated consecutive-no-op
budget that fails the run with stop_reason="noop_patch" after
MAX_CONSECUTIVE_NOOP rejections.
"""

from __future__ import annotations

import json
from pathlib import Path

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from mini_agent.tools import RepoTools
from tests.conftest import StubModel

MODIFY_TASK = "Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío"

REAL_PATCH = (
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,2 +1,4 @@\n"
    " def ApplyPolicy(ports):\n"
    "-    return 'ok'\n"
    "+    if not ports:\n"
    "+        return 'error: ports required'\n"
    "+    return 'ok'\n"
)

# Three distinct context-only hunks git accepts unchanged. Distinctness
# matters: identical actions would trip the 3x-identical guard before
# reaching the no-op counter. N2/N3 keep matching after REAL_PATCH, so
# they stay no-ops on a dirty tree too.
NOOP_1 = (
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def ApplyPolicy(ports):\n"
    "     return 'ok'\n"
)
NOOP_2 = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,1 +1,1 @@\n def ApplyPolicy(ports):\n"
NOOP_3 = "--- a/src/utils.py\n+++ b/src/utils.py\n@@ -1,1 +1,1 @@\n PI = 3.14\n"
HEADERS_ONLY = "--- a/src/app.py\n+++ b/src/app.py\n"


def _patch_action(patch: str) -> str:
    return json.dumps({"action": "apply_patch", "patch": patch})


def _plan_action() -> str:
    return json.dumps(
        {
            "action": "plan",
            "files": ["src/app.py"],
            "summary": "Validate ports before accepting",
        }
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class TestNoopDetection:
    def test_context_only_hunks_are_noops(self, git_repo_with_symbol: Path) -> None:
        tools = RepoTools(git_repo_with_symbol, "true")
        for patch in (NOOP_1, NOOP_2, NOOP_3):
            assert tools.apply_patch(patch) == "PATCH NO-OP\n"

    def test_noop_detected_on_dirty_tree(self, git_repo_with_symbol: Path) -> None:
        tools = RepoTools(git_repo_with_symbol, "true")
        assert tools.apply_patch(REAL_PATCH).startswith("PATCH APPLIED")
        assert tools.apply_patch(NOOP_2) == "PATCH NO-OP\n" + tools._git_diff()

    def test_headers_only_fails_loudly(self, git_repo_with_symbol: Path) -> None:
        # The anchor fallback used to report vacuous success for
        # patches with no change clusters.
        tools = RepoTools(git_repo_with_symbol, "true")
        assert tools.apply_patch(HEADERS_ONLY).startswith("PATCH FAILED")


class TestNoopPatchRejected:
    def test_noop_then_real_patch_completes(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                _plan_action(),
                _patch_action(NOOP_1),
                _patch_action(REAL_PATCH),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.stop_reason == "completed"
        assert result.patches == 1
        assert result.noop_patches == 1
        assert result.tests_run == 1
        assert result.llm_calls == 3
        assert "NO-OP" in model.calls[2][1]

    def test_noop_does_not_enter_verify(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                _plan_action(),
                _patch_action(NOOP_1),
                _patch_action(NOOP_2),
                _patch_action(NOOP_3),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "noop_patch"
        assert result.patches == 0
        assert result.noop_patches == 3
        assert result.tests_run == 0
        assert result.llm_calls == 4


class TestNoopBudget:
    def test_counter_resets_on_successful_patch(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                _plan_action(),
                _patch_action(NOOP_1),
                _patch_action(NOOP_2),
                _patch_action(REAL_PATCH),
                _patch_action(NOOP_2),
                _patch_action(NOOP_3),
                _patch_action(NOOP_2),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "false")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "noop_patch"
        assert result.patches == 1
        assert result.noop_patches == 5
        assert result.llm_calls == 7

    def test_budget_exhausted_event_emitted(
        self, git_repo_with_symbol: Path
    ) -> None:
        out = git_repo_with_symbol.parent / "experiments"
        logger = ExperimentLogger(generate_run_id(), out, enabled=True)
        model = StubModel(
            [
                _plan_action(),
                _patch_action(NOOP_1),
                _patch_action(NOOP_2),
                _patch_action(NOOP_3),
            ]
        )
        agent = CodingAgent(
            git_repo_with_symbol, model, "true", experiment_logger=logger
        )
        result = agent.run(MODIFY_TASK)

        trace = _read_jsonl(logger.trace_path)
        noop_events = [e for e in trace if e["type"] == "noop_patch"]
        assert len(noop_events) == 3
        assert all(e["files"] == [] for e in noop_events)
        exhausted = [
            e
            for e in trace
            if e["type"] == "budget_exhausted" and e.get("budget") == "noop_patch"
        ]
        assert len(exhausted) == 1
        assert exhausted[0]["used"] == 3
        assert exhausted[0]["limit"] == 3

        rec = _read_jsonl(out / "runs.jsonl")[0]
        assert rec["stop_reason"] == "noop_patch"
        assert rec["patches"] == 0
        assert rec["noop_patches"] == 3
        from mini_agent.version import HARNESS_VERSION

        assert rec["harness_version"] == HARNESS_VERSION
        assert rec["llm_calls"] == result.llm_calls


class TestRealPatchUnaffected:
    def test_real_patch_still_verifies(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel([_plan_action(), _patch_action(REAL_PATCH)])
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.patches == 1
        assert result.noop_patches == 0
        assert result.tests_run == 1
