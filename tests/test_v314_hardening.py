from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from tests.conftest import StubModel

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

MODIFY_TASK = "Corrige ApplyPolicy para que devuelva un error cuando ports esté vacío"

GO_METHOD_SOURCE = """package fw

type Service struct {
	Name string
}

func (s *Service) Status() string {
	return "ok"
}
"""


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _go_method_repo(git_repo: Path) -> Path:
    (git_repo / "fw").mkdir()
    (git_repo / "fw" / "service.go").write_text(GO_METHOD_SOURCE)
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "go"], cwd=git_repo, check=True)
    return git_repo


class TestPatchlessFinishGuard:
    """Regression for the live-run bug: a MODIFY task reached DONE with
    zero patches by claiming completion in the finish summary."""

    def test_modify_patchless_finish_rejected_then_recovers(
        self, git_repo_with_symbol: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps({"action": "finish", "summary": "Task completed."}),
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
        assert result.patches == 1
        assert any("no patch has been applied" in m for m in agent.memory)

    def test_modify_patchless_finish_accepted_on_repeat(
        self, git_repo_with_files: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps({"action": "finish", "summary": "Task completed."}),
                json.dumps(
                    {"action": "finish", "summary": "No change is needed."}
                ),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Fix the return value in src/app.py.")

        assert result.final_state == "done"
        assert result.stop_reason == "completed_without_patch"
        assert result.patches == 0
        assert any("no patch has been applied" in m for m in agent.memory)

    def test_patchless_finish_trace_events(self, git_repo_with_files: Path) -> None:
        out = git_repo_with_files.parent / "experiments"
        logger = ExperimentLogger(generate_run_id(), out, enabled=True)
        model = StubModel(
            [
                json.dumps({"action": "finish", "summary": "Task completed."}),
                json.dumps(
                    {"action": "finish", "summary": "No change is needed."}
                ),
            ]
        )
        agent = CodingAgent(
            git_repo_with_files, model, "", experiment_logger=logger
        )
        agent.run("Fix the return value in src/app.py.")

        events = [
            e
            for e in _read_jsonl(logger.trace_path)
            if e["type"] == "patchless_finish"
        ]
        assert [e["accepted"] for e in events] == [False, True]

    def test_inspect_finish_without_patch_unchanged(
        self, git_repo_with_files: Path
    ) -> None:
        model = StubModel([json.dumps({"action": "finish", "summary": "ok"})])
        agent = CodingAgent(git_repo_with_files, model, "")
        result = agent.run("Revisa qué hay en la carpeta")

        assert result.final_state == "done"
        assert result.stop_reason == "completed"


class TestEmptyPatchFieldGuard:
    """Regression for the live-run bug: apply_patch with a missing
    'patch' field reached git apply as an empty patch, counted as a
    failed patch, and bounced REPAIR -> READ where patching is illegal.
    A missing field is a model-output contract violation: reject it and
    stay in the current state."""

    def test_empty_patch_field_stays_in_repair(
        self, git_repo_with_symbol: Path
    ) -> None:
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
                json.dumps({"action": "apply_patch", "files": ["src/app.py"]}),
                json.dumps({"action": "apply_patch", "patch": patch2}),
                json.dumps({"action": "apply_patch", "patch": patch3}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "false")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "test_failure"
        assert result.patches == 3
        repair_prompts = [
            u for _, u in model.calls if "TEST FAILURE" in u
        ]
        assert repair_prompts
        assert any(
            "requires a non-empty 'patch' field" in u for u in repair_prompts
        )


    def test_headerless_patch_with_files_field_applies(
        self, git_repo_with_symbol: Path
    ) -> None:
        """Regression for the live-run bug: the model's repair patch
        started at @@ with no ---/+++ file headers, but the action
        carried files: [...]. The harness synthesizes the headers from
        the declared file instead of failing with 'unrecognized input'."""
        raw = (
            '{"action": "apply_patch", '
            '"files": ["src/app.py"], '
            '"patch": "@@ -1,2 +1,3 @@\\n'
            " def ApplyPolicy(ports):\\n"
            "-    return 'ok'\\n"
            "+    if not ports:\\n"
            "+        return 'error: ports required'\\n"
            "+    return 'ok'\\n"
            '"}'
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
                raw,
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.patches == 1
        assert "ports required" in (git_repo_with_symbol / "src" / "app.py").read_text()


    def test_resending_applied_patch_gets_explicit_feedback(
        self, git_repo_with_symbol: Path
    ) -> None:
        """Regression for the live-run bug: after a patch applied and
        verification failed, the model resent the same patch; git apply
        rejected it and the model only saw 'patch does not apply'. The
        harness now detects that the added lines are already in the
        working tree and tells the model to write a DIFFERENT patch."""
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
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.patches == 1
        assert any(
            "already present in the working tree" in u for _, u in model.calls
        )


class TestFinishAfterFailedVerification:
    """Regression for the live-run bug: after a patch applied and
    verification failed, the model could wander to READ and finish,
    and the run was recorded as completed — inflating solve rates.
    A finish after failed verification is an honest failure:
    final state FAILED, stop_reason test_failure."""

    def test_finish_after_failed_verification_is_failure(
        self, git_repo_with_symbol: Path
    ) -> None:
        patch_bad = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def Nonexistent(ports):\n"
            "-    return 'nope'\n"
            "+    return 'x'\n"
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
                json.dumps({"action": "apply_patch", "patch": patch_bad}),
                json.dumps({"action": "finish", "summary": "cannot fix"}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "false")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "test_failure"
        assert result.patches == 1

    def test_finish_after_passing_verification_completes(
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
                json.dumps({"action": "finish", "summary": "done"}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.stop_reason == "completed"


class TestControlCharTolerantParsing:
    """Regression for the live-run bug: the model emitted the patch JSON
    with literal newlines inside the string value (invalid strict JSON),
    four times identically, until the budget died. json.loads(strict=False)
    accepts control characters inside strings."""

    def test_extract_json_object_allows_control_chars(self) -> None:
        from mini_agent.model import extract_json_object

        text = (
            '{"action": "apply_patch", '
            '"patch": "--- a/src/app.py\n+++ b/src/app.py\n'
            '@@ -1,2 +1,2 @@\n def ApplyPolicy(ports):\n'
            "-    return 'ok'\n+    return 'better'\n\"}"
        )
        obj = extract_json_object(text)
        assert obj["action"] == "apply_patch"
        assert obj["patch"].startswith("--- a/src/app.py\n")

    def test_raw_multiline_patch_response_completes_run(
        self, git_repo_with_symbol: Path
    ) -> None:
        raw = (
            '{"action": "apply_patch", '
            '"patch": "--- a/src/app.py\n+++ b/src/app.py\n'
            '@@ -1,2 +1,4 @@\n def ApplyPolicy(ports):\n'
            "-    return 'ok'\n"
            "+    if not ports:\n"
            "+        return 'error: ports required'\n"
            "+    return 'ok'\n\"}"
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
                raw,
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.stop_reason == "completed"
        assert result.patches == 1


class TestPatchNormalization:
    """Regression for the live-run bug: the model emitted a patch whose
    context lines lacked the leading space marker (raw tabs / bare lines),
    so git apply reported 'corrupt patch'. normalize_patch repairs this
    deterministically before applying."""

    def test_normalize_adds_context_markers(self) -> None:
        from mini_agent.tools import normalize_patch

        patch = (
            "--- a/main.go\n"
            "+++ b/main.go\n"
            "@@ -1,3 +1,3 @@\n"
            "func main() {\n"
            "\tprint()\n"
            "}\n"
        )
        fixed = normalize_patch(patch)
        assert fixed.split("\n")[3] == " func main() {"
        assert fixed.split("\n")[4] == " \tprint()"
        assert fixed.split("\n")[5] == " }"

    def test_normalize_keeps_wellformed_patch_unchanged(self) -> None:
        from mini_agent.tools import normalize_patch

        patch = (
            "--- a/main.go\n"
            "+++ b/main.go\n"
            "@@ -1,3 +1,3 @@\n"
            " func main() {\n"
            "-\tprint()\n"
            "+\tprintln()\n"
            " }\n"
        )
        assert normalize_patch(patch) == patch

    def test_markerless_patch_applies_end_to_end(
        self, git_repo_with_symbol: Path
    ) -> None:
        raw = (
            '{"action": "apply_patch", '
            '"patch": "--- a/src/app.py\\n+++ b/src/app.py\\n'
            '@@ -1,2 +1,4 @@\\n'
            "def ApplyPolicy(ports):\\n"
            "-    return 'ok'\\n"
            "+    if not ports:\\n"
            "+        return 'error: ports required'\\n"
            "+    return 'ok'\\n"
            '"}'
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
                raw,
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "done"
        assert result.stop_reason == "completed"
        assert result.patches == 1
        assert "ports required" in (git_repo_with_symbol / "src" / "app.py").read_text()


class TestBudgetExhaustedPromptAlignment:
    """Regression for the live-run bug: the budget-exhausted USER prompt
    hardcoded 'Allowed action: finish' for every task type, contradicting
    the MODIFY system prompt ('plan, finish') and steering the model into
    a false finish."""

    def test_modify_exhausted_prompt_offers_plan(
        self, git_repo_with_files: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps({"action": "read_file", "path": "src/app.py"}),
                json.dumps({"action": "read_file", "path": "src/utils.py"}),
                json.dumps({"action": "read_file", "path": "README.md"}),
                json.dumps({"action": "finish", "summary": "No change."}),
                json.dumps({"action": "finish", "summary": "No change."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Fix the return value in src/app.py.")

        exhausted = [u for _, u in model.calls if "READ BUDGET EXHAUSTED" in u]
        assert exhausted
        assert "Allowed actions: plan, finish" in exhausted[0]
        assert "Allowed action: finish" not in exhausted[0]

    def test_inspect_exhausted_prompt_still_finish_only(
        self, git_repo_with_files: Path
    ) -> None:
        model = StubModel(
            [
                json.dumps({"action": "read_file", "path": "src/app.py"}),
                json.dumps({"action": "read_file", "path": "src/utils.py"}),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo_with_files, model, "")
        agent.run("Revisa qué hay en la carpeta")

        exhausted = [u for _, u in model.calls if "READ BUDGET EXHAUSTED" in u]
        assert exhausted
        assert "Allowed action: finish" in exhausted[0]


class TestEditPromptCarriesActiveCode:
    """Regression for the live-run bug: the EDIT prompt showed only
    FILE CONTEXT (the last tool result), which can be an error message.
    The model had to write patches blind, without the code it loaded."""

    def test_edit_prompt_contains_active_code(
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
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        agent.run(MODIFY_TASK)

        edit_prompts = [u for _, u in model.calls if "apply_patch action" in u]
        assert edit_prompts
        assert "ACTIVE CODE" in edit_prompts[0]
        assert "ApplyPolicy" in edit_prompts[0]

    def test_edit_prompt_shows_patch_format(
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
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "true")
        agent.run(MODIFY_TASK)

        edit_calls = [
            (s, u) for s, u in model.calls if "apply_patch action" in u
        ]
        assert edit_calls
        edit_system = edit_calls[0][0]
        assert "--- a/" in edit_system
        assert "+++ b/" in edit_system

    def test_repair_prompt_contains_active_code(
        self, git_repo_with_symbol: Path
    ) -> None:
        patch_app = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def ApplyPolicy(ports):\n"
            "-    return 'ok'\n"
            "+    return 'better'\n"
        )
        patch_utils = (
            "--- a/src/utils.py\n"
            "+++ b/src/utils.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-PI = 3.14\n"
            "+PI = 3.15\n"
            " \n"
            " def add(a, b):\n"
        )
        patch_utils2 = (
            "--- a/src/utils.py\n"
            "+++ b/src/utils.py\n"
            "@@ -2,3 +2,3 @@\n"
            " \n"
            " def add(a, b):\n"
            "-    return a + b\n"
            "+    return a + b + 0\n"
        )
        patch_app2 = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def ApplyPolicy(ports):\n"
            "-    return 'better'\n"
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
                json.dumps({"action": "apply_patch", "patch": patch_app}),
                json.dumps({"action": "apply_patch", "patch": patch_utils}),
                json.dumps({"action": "apply_patch", "patch": patch_utils2}),
                json.dumps({"action": "apply_patch", "patch": patch_app2}),
            ]
        )
        agent = CodingAgent(git_repo_with_symbol, model, "false")
        result = agent.run(MODIFY_TASK)

        assert result.final_state == "failed"
        assert result.stop_reason == "test_failure"
        repair_prompts = [u for _, u in model.calls if "corrective change" in u]
        assert repair_prompts
        assert "ACTIVE CODE" in repair_prompts[0]
        assert "ApplyPolicy" in repair_prompts[0]


class TestReceiverQualifiedSymbolNames:
    """Regression for the live-run bug: the repo map renders Go methods as
    '(*Receiver).Name()' and the model copied that format into read_symbol,
    which failed to resolve."""

    def test_read_symbol_receiver_qualified(self, git_repo: Path) -> None:
        _go_method_repo(git_repo)
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "read_symbol",
                        "path": "fw/service.go",
                        "symbol": "(*Service).Status",
                    }
                ),
                json.dumps({"action": "finish", "summary": "Read Status."}),
            ]
        )
        agent = CodingAgent(git_repo, model, "")
        result = agent.run("Revisa el método Status del firewall.")

        assert result.final_state == "done"
        assert result.symbol_reads == 1
        assert "Status" in agent.last_tool_result
        assert "ERROR" not in agent.last_tool_result

    def test_receiver_qualified_and_plain_names_are_duplicates(
        self, git_repo: Path
    ) -> None:
        _go_method_repo(git_repo)
        model = StubModel(
            [
                json.dumps(
                    {
                        "action": "read_symbol",
                        "path": "fw/service.go",
                        "symbol": "(*Service).Status",
                    }
                ),
                json.dumps(
                    {
                        "action": "read_symbol",
                        "path": "fw/service.go",
                        "symbol": "Status",
                    }
                ),
                json.dumps({"action": "finish", "summary": "Done."}),
            ]
        )
        agent = CodingAgent(git_repo, model, "")
        agent.run("Revisa el método Status del firewall.")

        assert len(agent.progress.symbols_read) == 1
        assert next(iter(agent.progress.symbols_read)) == "fw/service.go::Status"
        assert any("already read" in m for m in agent.memory)
