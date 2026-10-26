#!/usr/bin/env python3
"""Deterministic selector check for the three Validate definitions.

Reproduces the sweep condition: a temp copy of the repo with the amb
task's test file in place (that test is engine.Validate's only
same-directory caller at run time). Prints find_exact_task_symbols
for three task variants:

    no path      -> the tiebreak under test
    engine path  -> path mention must win in any version
    handler path -> path mention must win in any version

Run in BOTH checkouts before the sweep and record the output.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mini_agent.scanner import scan_repository
from mini_agent.symbols import build_symbol_index, find_exact_task_symbols
from experiments.baseline_modify import AMB_TASK
from tests.test_live_ollama import SOURCE_REPO, VALIDATOR_TEST

ENGINE_VALIDATE = "backend/internal/engine/validator.go::Validate"
HANDLER_VALIDATE = "backend/internal/api/handlers/policy_handler.go::Validate"

VARIANTS = [
    ("no-path", AMB_TASK, ENGINE_VALIDATE),
    (
        "engine-path",
        AMB_TASK.replace(
            "Corrige Validate para",
            "Corrige Validate en internal/engine/validator.go para",
        ),
        ENGINE_VALIDATE,
    ),
    (
        "handler-path",
        AMB_TASK.replace(
            "Corrige Validate para",
            "Corrige Validate en internal/api/handlers/policy_handler.go para",
        ),
        HANDLER_VALIDATE,
    ),
]


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="selector-check-"))
    repo = tmp / "iptables-visualizer"
    shutil.copytree(SOURCE_REPO, repo)
    (repo / "backend" / "internal" / "engine" / "validator_test.go").write_text(
        VALIDATOR_TEST
    )
    scan = scan_repository(repo)
    index = build_symbol_index(repo, scan)
    try:
        for name, task, expected in VARIANTS:
            resolved = find_exact_task_symbols(task, index)
            keys = [f"{s.path}::{s.name}" for s in resolved]
            ok = expected in keys
            print(f"{name}: selected={keys} expected={expected} -> {'OK' if ok else 'WRONG'}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
