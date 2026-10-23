"""Regression tests for v3.1.7 behavior.

* extract_json_object extracts the first balanced JSON object instead of
  merging the first brace to the last (CURRENT_ISSUES 1.1).
* Verification reads the EXIT_CODE marker from the first line only, so a
  test command that prints EXIT_CODE=0 in its own output cannot be
  misread as passing (CURRENT_ISSUES 1.2).
* Three consecutive malformed outputs fail deterministically with
  stop_reason="invalid_output" — alternating outputs can no longer evade
  the repeat bound, and malformed output gets a dedicated budget
  (CURRENT_ISSUES 3.1, 3.2).
* RunState.repeated keeps only the last three entries (CURRENT_ISSUES
  4.4).
"""

import json
import subprocess
from pathlib import Path

import pytest

from mini_agent.agent import CodingAgent
from mini_agent.model import extract_json_object
from mini_agent.orchestrator import RunState
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


class TestExtractFirstJsonObject:
    def test_prose_wrapped_object(self):
        obj = extract_json_object(
            'Sure! Here is the action: {"action": "finish", '
            '"summary": "done"} hope that helps'
        )
        assert obj == {"action": "finish", "summary": "done"}

    def test_two_objects_returns_first(self):
        obj = extract_json_object(
            '{"action": "plan"} {"action": "finish", "summary": "x"}'
        )
        assert obj == {"action": "plan"}

    def test_trailing_brace_in_prose(self):
        obj = extract_json_object(
            '{"action": "finish", "summary": "x"} blah }'
        )
        assert obj == {"action": "finish", "summary": "x"}

    def test_braces_inside_string_values(self):
        obj = extract_json_object(
            'here {"action": "finish", "summary": "use { and } carefully"}'
        )
        assert obj["summary"] == "use { and } carefully"

    def test_no_object_raises(self):
        with pytest.raises(ValueError, match="did not return JSON"):
            extract_json_object("no json here at all")


class TestLenientPatchExtraction:
    """Regression for the live-run bug: the model emitted a correct
    unified diff but broke the JSON encoding with unescaped quotes
    inside the patch string, repeated identically until the run died.
    parse_model_output repairs such patch actions leniently instead of
    failing the whole output."""

    def test_unescaped_quotes_in_patch_repaired(self):
        from mini_agent.model import parse_model_output

        raw = (
            '{"action": "apply_patch", '
            '"patch": "--- a/main.go\\n+++ b/main.go\\n@@ -1,3 +1,3 @@\\n'
            ' func validate() {\\n-\\t\\tMessage:  "policy name is required",\\n'
            '+\\t\\tMessage:  "policy name is required",\\n'
            ' }"}'
        )
        action, error = parse_model_output(raw)
        assert error is None
        assert action is not None
        assert action["action"] == "apply_patch"
        assert '"policy name is required"' in action["patch"]
        assert action["patch"].startswith("--- a/main.go\n")

    def test_real_newlines_with_unescaped_quotes_repaired(self):
        from mini_agent.model import parse_model_output

        raw = (
            '{"action": "apply_patch", '
            '"patch": "--- a/main.go\n+++ b/main.go\n@@ -1,2 +1,2 @@\n'
            " ctx line\n-old \"quoted\" line\n"
            '+new "quoted" line"}'
        )
        action, error = parse_model_output(raw)
        assert error is None
        assert action["patch"].startswith("--- a/main.go\n")
        assert 'new "quoted" line' in action["patch"]

    def test_non_patch_broken_json_still_fails(self):
        from mini_agent.model import parse_model_output

        action, error = parse_model_output('{"action": "plan", oops}')
        assert action is None
        assert "not valid JSON" in error


class TestExitCodeFirstLine:
    def test_command_printing_exit_code_zero_but_failing(
        self, git_repo_with_symbol: Path
    ) -> None:
        """A failing command that prints EXIT_CODE=0 in its own output
        must not be misread as passing."""
        patch2 = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,4 +1,4 @@\n"
            " def ApplyPolicy(ports):\n"
            "     if not ports:\n"
            "         return 'error: ports required'\n"
            "-    return 'ok'\n"
            "+    return 'done'\n"
        )
        patch3 = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,4 +1,4 @@\n"
            " def ApplyPolicy(ports):\n"
            "     if not ports:\n"
            "         return 'error: ports required'\n"
            "-    return 'done'\n"
            "+    return 'ok'\n"
        )
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "apply_patch", "patch": patch2}),
                json.dumps({"action": "apply_patch", "patch": patch3}),
            ]
        )
        agent = CodingAgent(
            git_repo_with_symbol, model, "echo EXIT_CODE=0; false"
        )
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "test_failure"
        assert result.tests_run >= 1
        assert result.patches == 3


class TestConsecutiveInvalidBudget:
    def test_three_consecutive_invalid_outputs_fail(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(["not json", "also not json", "still not json"])
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "invalid_output"
        assert result.llm_calls == 3

    def test_alternating_invalid_outputs_fail(
        self, git_repo_with_symbol: Path
    ) -> None:
        """The 3x-identical bound is evaded by alternating outputs; the
        consecutive counter must not be."""
        model = StubModel(["garbage A", "garbage B", "garbage A"])
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "invalid_output"

    def test_valid_action_resets_streak(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                "garbage A",
                "garbage B",
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.stop_reason == "completed"


class TestBoundedRepeated:
    def test_repeated_keeps_last_three(self):
        rs = RunState()
        for i in range(10):
            rs.record_repeat(f"action-{i}")
        assert rs.repeated == ["action-7", "action-8", "action-9"]


class TestAlreadyAppliedDetection:
    """Regression for the live-run bug: the already-applied check used
    substring matching of stripped added lines against the whole diff,
    so a DIFFERENT repair patch whose short lines (`return result`, `}`)
    appeared anywhere in the diff was falsely rejected as "already
    present". The check is now `git apply --reverse --check` — exact."""

    PATCH_SEVERITY = (
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,4 +1,5 @@\n"
        " def ApplyPolicy(ports):\n"
        "     if not ports:\n"
        "         return 'error: ports required'\n"
        "     return 'ok'\n"
        "+\n"
        "+# severity tracking\n"
    )

    def test_different_patch_not_reported_as_already_applied(
        self, git_repo_with_symbol: Path
    ) -> None:
        patch3 = self.PATCH_SEVERITY.replace(
            "+# severity tracking", "+# severity tracking v2"
        )
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "apply_patch", "patch": self.PATCH_SEVERITY}),
                json.dumps({"action": "apply_patch", "patch": patch3}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "false")
        result = agent.run(MODIFY_TASK)

        assert result.patches == 3
        assert not any(
            "already present in the working tree" in u for _, u in model.calls
        )


class TestHeaderlessPatchTargetInference:
    """Regression for the live-run bug: the model emitted a correct
    headerless patch with no files/path in the action, so the header
    synthesis had no target and the patch was lost."""

    HEADERLESS = (
        "@@ -1,3 +1,4 @@\n"
        " def ApplyPolicy(ports):\n"
        "-    return 'ok'\n"
        "+    if not ports:\n"
        "+        return 'error: ports required'\n"
        "+    return 'ok'\n"
    )

    def test_target_inferred_from_context_line(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": self.HEADERLESS}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        content = (git_repo_with_symbol / "src" / "app.py").read_text()
        assert "error: ports required" in content

    def test_ambiguous_target_is_structured_failure(
        self, git_repo_with_symbol: Path
    ) -> None:
        ambiguous = (
            "@@ -1,2 +1,2 @@\n"
            " def ApplyPolicy(ports):\n"
            "-    return 'ok'\n"
            "+    return 'fine'\n"
        )
        (git_repo_with_symbol / "src" / "copy.py").write_text(
            "def ApplyPolicy(ports):\n    return 'ok'\n"
        )
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": ambiguous}),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert "files" in model.calls[2][1] or "files" in model.calls[2][0]


class TestPatchFailureStaysInEdit:
    """Regression for the live-run bug: after a failed patch the state
    moved to READ, so the model's corrected patch was rejected as
    not-allowed-in-state and the fix was lost. A failed patch now keeps
    the model in EDIT/REPAIR to retry immediately with the failure
    feedback."""

    def test_corrected_patch_retries_in_edit(
        self, git_repo_with_symbol: Path
    ) -> None:
        bad_patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def NonexistentSymbol():\n"
            "-    return 'ok'\n"
            "+    return 'nope'\n"
        )
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": bad_patch}),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.patches == 1
        assert result.llm_calls == 3


class TestPatchPositionFallback:
    """Regression for the live-run bug: the model wrote a semantically
    correct diff whose @@ hunk start line did not match the real context
    position (and used a space where the file has a tab). git apply is
    position-strict and rejected it; patch(1) searches for the context
    and applies it as a fallback."""

    def test_wrong_hunk_start_applies_via_fallback(
        self, git_repo_with_symbol: Path
    ) -> None:
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -40,4 +40,5 @@\n"  # real position is line 1, not 40
            " def ApplyPolicy(ports):\n"
            "-    return 'ok'\n"
            "+    if not ports:\n"
            "+        return 'error: ports required'\n"
            "+    return 'ok'\n"
        )
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": patch}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.patches == 1
        content = (git_repo_with_symbol / "src" / "app.py").read_text()
        assert "error: ports required" in content

    def test_space_instead_of_tab_in_context_applies(
        self, git_repo_with_symbol: Path
    ) -> None:
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def ApplyPolicy(ports):\n"
            "-    return 'ok'\n"
            "+    if not ports:\n"
            "+        return 'error: ports required'\n"
            "+    return 'ok'\n"
        )
        from mini_agent.tools import RepoTools

        out = RepoTools(git_repo_with_symbol, "true").apply_patch(patch)
        assert out.startswith("PATCH APPLIED")
        content = (git_repo_with_symbol / "src" / "app.py").read_text()
        assert "error: ports required" in content

    def test_unmatched_patch_still_fails(
        self, git_repo_with_symbol: Path
    ) -> None:
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def FunctionThatDoesNotExist():\n"
            "-    return 'ok'\n"
            "+    return 'nope'\n"
        )
        from mini_agent.tools import RepoTools

        out = RepoTools(git_repo_with_symbol, "true").apply_patch(patch)
        assert out.startswith("PATCH FAILED")


    def test_mid_hunk_context_gap_applies_via_anchor(
        self, git_repo_with_symbol: Path
    ) -> None:
        """The model omits context lines in the middle of the hunk (here
        the blank line and closing brace between the anchor and the
        change); git and patch(1) reject it, the removal-anchored
        fallback applies it."""
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,4 +1,5 @@\n"
            " def ApplyPolicy(ports):\n"
            "-    return 'ok'\n"
            "+    if not ports:\n"
            "+        return 'error: ports required'\n"
            "+    return 'ok'\n"
            " extra_context_that_is_not_in_the_file\n"
        )
        from mini_agent.tools import RepoTools

        out = RepoTools(git_repo_with_symbol, "true").apply_patch(patch)
        assert out.startswith("PATCH APPLIED")
        content = (git_repo_with_symbol / "src" / "app.py").read_text()
        assert "error: ports required" in content

    def test_anchor_requires_unique_match(
        self, git_repo_with_symbol: Path
    ) -> None:
        (git_repo_with_symbol / "src" / "app.py").write_text(
            "def ApplyPolicy(ports):\n    return 'ok'\n\n\n"
            "def other():\n    return 'ok'\n"
        )
        patch = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -9,4 +9,4 @@\n"  # wrong position and counts: reaches anchor tier
            " def ApplyPolicy(ports):\n"
            "-    return 'ok'\n"
            "+    return 'fine'\n"
        )
        from mini_agent.tools import RepoTools

        out = RepoTools(git_repo_with_symbol, "true").apply_patch(patch)
        assert out.startswith("PATCH FAILED")


class TestPatchedSnippetRefresh:
    """Regression for the live-run bug: after a patch applied and
    verification failed, the repair prompt still showed the pre-patch
    symbol content, so the model patched against stale code."""

    def test_repair_prompt_shows_post_patch_content(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "plan",
                        "files": ["src/app.py"],
                        "summary": "Validate ports before accepting",
                    }
                ),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "apply_patch", "patch": PATCH_APP}),
                json.dumps({"action": "finish", "summary": "cannot fix"}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "false")
        agent.run(MODIFY_TASK)

        # call 3 is the first repair prompt (plan, patch, verify-fail)
        assert "error: ports required" in model.calls[2][1]
