#!/usr/bin/env python3
"""Record the live MODIFY baseline into experiments/runs.jsonl.

Runs a graded suite of MODIFY tasks against a fresh copy of
iptables-visualizer N times per model at temperature 0, appending every
run to experiments/runs.jsonl (gitignored by design). These records are
the comparison baseline required by benchmark discipline (CURRENT_ISSUES
6.2): one task is a transcript, not a benchmark.

Suite (id, difficulty, required change):
    msg  easy   string change in an issue message
    mid  medium severity semantics: warning -> error
    hard hard   control flow: skip the format check for empty names

Usage:
    python experiments/baseline_modify.py [runs_per_model] [model ...]
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mini_agent.agent import CodingAgent
from mini_agent.experiments import ExperimentLogger, generate_run_id
from mini_agent.model import OpenAICompatModel
from tests.test_live_ollama import MODIFY_TASK, SOURCE_REPO, VALIDATOR_TEST

HARD_TEST = """package engine

import (
    "testing"

    "iptables-visualizer/internal/models"
)

func TestValidateEmptyNameReportsSingleIssue(t *testing.T) {
    v := NewValidator()
    result := v.Validate(&models.Policy{
        Name: "",
        Rules: []models.Rule{{
            Name:     "web",
            Action:   models.ActionAccept,
            Protocol: models.ProtocolTCP,
            Enabled:  true,
        }},
    })

    if result.Valid {
        t.Error("policy with empty name must be invalid")
    }

    nameIssues := 0
    for _, iss := range result.Issues {
        if iss.Field == "name" {
            nameIssues++
            if iss.Message != "policy name is required" {
                t.Errorf("unexpected name issue: %s", iss.Message)
            }
        }
    }
    if nameIssues != 1 {
        t.Errorf(
            "expected exactly 1 name issue for empty policy name, got %d: %+v",
            nameIssues, result.Issues,
        )
    }
}
"""

HARD_TASK = (
    "Corrige Validate en internal/engine/validator.go para que pase el test "
    "TestValidateEmptyNameReportsSingleIssue: un nombre de politica vacio "
    "debe producir unicamente el error 'policy name is required' y no "
    "tambien el error de formato del nombre"
)

MID_TEST = """package engine

import (
    "testing"

    "iptables-visualizer/internal/models"
)

func TestValidateEmptyRulesIsError(t *testing.T) {
    v := NewValidator()
    result := v.Validate(&models.Policy{
        Name:  "web-policy",
        Rules: []models.Rule{},
    })

    if result.Valid {
        t.Error("policy without rules must be invalid")
    }

    rulesIssues := 0
    for _, iss := range result.Issues {
        if iss.Field == "rules" {
            rulesIssues++
            if iss.Severity != "error" {
                t.Errorf("rules issue must be an error, got %s", iss.Severity)
            }
        }
    }
    if rulesIssues != 1 {
        t.Errorf(
            "expected exactly 1 rules issue for empty rules, got %d: %+v",
            rulesIssues, result.Issues,
        )
    }
}
"""

MID_TASK = (
    "Corrige Validate en internal/engine/validator.go para que pase el test "
    "TestValidateEmptyRulesIsError: una politica sin reglas debe producir un "
    "issue de campo rules con severidad 'error' y no 'warning'"
)

TASKS = [
    {"id": "msg", "task": MODIFY_TASK, "test": VALIDATOR_TEST},
    {"id": "mid", "task": MID_TASK, "test": MID_TEST},
    {"id": "hard", "task": HARD_TASK, "test": HARD_TEST},
]

BASE_URL = "http://localhost:11434/v1"
OUT = Path(__file__).resolve().parent


def make_repo(tmp: Path, test_content: str) -> Path:
    repo = tmp / "iptables-visualizer"
    shutil.copytree(SOURCE_REPO, repo)
    (repo / "backend" / "internal" / "engine" / "validator_test.go").write_text(
        test_content
    )
    subprocess.run(["git", "config", "user.email", "t@e.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed validator regression test"], cwd=repo, check=True)
    return repo


def main() -> None:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    models = sys.argv[2:] or ["qwen2.5-coder:7b", "qwen2.5-coder:14b"]
    summary: dict[tuple[str, str], list[bool]] = {}
    for model_name in models:
        for task in TASKS:
            for i in range(runs):
                tmp = Path(tempfile.mkdtemp(prefix="baseline-"))
                repo = make_repo(tmp, task["test"])
                logger = ExperimentLogger(generate_run_id(), OUT, enabled=True)
                llm = OpenAICompatModel(BASE_URL, model_name, "local", timeout=300.0)
                llm.temperature = 0.0
                agent = CodingAgent(
                    repo, llm, "cd backend && go test ./...", experiment_logger=logger
                )
                t0 = time.time()
                result = agent.run(task["task"])
                elapsed = time.time() - t0
                print(
                    f"{model_name} {task['id']} run {i + 1}/{runs}: "
                    f"state={result.final_state} stop={result.stop_reason} "
                    f"llm_calls={result.llm_calls} patches={result.patches} "
                    f"tests={result.tests_run} elapsed={elapsed:.0f}s",
                    flush=True,
                )
                summary.setdefault((model_name, task["id"]), []).append(
                    result.final_state == "done"
                )
                shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== suite summary (solves/runs) ===")
    for (model_name, task_id), outcomes in summary.items():
        print(f"{model_name} {task_id}: {sum(outcomes)}/{len(outcomes)}")


if __name__ == "__main__":
    main()
