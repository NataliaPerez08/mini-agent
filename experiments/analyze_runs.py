#!/usr/bin/env python3
"""Analyze runs.jsonl records: symbol-selection accuracy + efficiency.

Reads one or more runs.jsonl files (each with a sibling traces/ dir)
and prints a markdown table per (harness_version, model, task):

* solves/runs and stop reasons;
* median LLM calls, prompt/completion tokens, elapsed seconds;
* symbol-selection precision/recall against the task's relevant set
  (strict ground truth: the target symbol plus the task's test symbol;
  everything else the harness pre-loaded or the model read counts as a
  distractor).

Usage:
    python experiments/analyze_runs.py experiments/runs.jsonl [...]
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

# Strict ground truth per suite task: the symbols that must be
# understood to solve the task. Everything else selected is a
# distractor (e.g. validateRule on the hard task).
RELEVANT_BY_TEST = {
    "TestValidateEmptyRulesMessage": {
        "backend/internal/engine/validator.go::Validate",
        "backend/internal/engine/validator_test.go::TestValidateEmptyRulesMessage",
    },
    "TestValidateEmptyRulesIsError": {
        "backend/internal/engine/validator.go::Validate",
        "backend/internal/engine/validator_test.go::TestValidateEmptyRulesIsError",
    },
    "TestValidateEmptyNameReportsSingleIssue": {
        "backend/internal/engine/validator.go::Validate",
        "backend/internal/engine/validator_test.go::TestValidateEmptyNameReportsSingleIssue",
    },
}

TASK_ID_BY_TEST = {
    "TestValidateEmptyRulesMessage": "msg",
    "TestValidateEmptyRulesIsError": "mid",
    "TestValidateEmptyNameReportsSingleIssue": "hard",
}


def _test_name(task: str) -> str:
    m = re.search(r"(Test[A-Za-z0-9_]+)", task)
    return m.group(1) if m else ""


def _selected_symbols(run: dict, traces_dir: Path) -> set[str]:
    selected = set(run.get("auto_symbols") or [])
    trace = traces_dir / f"{run['run_id']}.jsonl"
    if trace.is_file():
        for line in trace.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                e.get("type") == "tool_call"
                and e.get("action") == "read_symbol"
                and e.get("success")
            ):
                selected.add(f"{e['path']}::{e['symbol']}")
    return selected


def _median(values: list[float]) -> str:
    return f"{statistics.median(values):.0f}" if values else "-"


def analyze(path: Path) -> list[dict]:
    traces_dir = path.parent / "traces"
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        try:
            run = json.loads(line)
        except json.JSONDecodeError:
            continue
        test = _test_name(run.get("task", ""))
        if test not in RELEVANT_BY_TEST:
            continue
        relevant = RELEVANT_BY_TEST[test]
        selected = _selected_symbols(run, traces_dir)
        hits = selected & relevant
        rows.append(
            {
                "version": run.get("harness_version", "?"),
                "model": run.get("model", "?"),
                "task": TASK_ID_BY_TEST[test],
                "solved": run.get("final_state") == "done",
                "stop": run.get("stop_reason", "?"),
                "llm_calls": run.get("llm_calls", 0),
                "prompt_tokens": run.get("prompt_tokens", 0),
                "completion_tokens": run.get("completion_tokens", 0),
                "elapsed": run.get("elapsed_seconds", 0.0),
                "precision": len(hits) / len(selected) if selected else 0.0,
                "recall": len(hits) / len(relevant),
                "distractors": selected - relevant,
            }
        )
    return rows


def main() -> None:
    rows: list[dict] = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_file():
            rows.extend(analyze(p))
    if not rows:
        print("no suite records found")
        return

    groups: dict[tuple[str, str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["version"], r["model"], r["task"]), []).append(r)

    print("| Version | Model | Task | Solve | Median calls | Med prompt tok | Med compl tok | Median elapsed | Precision | Recall | Top distractors |")
    print("|---------|-------|------|-------|--------------|----------------|---------------|----------------|-----------|--------|----------------|")
    for (version, model, task), rs in sorted(groups.items()):
        solved = sum(r["solved"] for r in rs)
        distractors = Counter()
        for r in rs:
            distractors.update(r["distractors"])
        top = ", ".join(f"{d} ({c})" for d, c in distractors.most_common(2)) or "-"
        print(
            f"| {version} | {model} | {task} | {solved}/{len(rs)} "
            f"| {_median([r['llm_calls'] for r in rs])} "
            f"| {_median([r['prompt_tokens'] for r in rs])} "
            f"| {_median([r['completion_tokens'] for r in rs])} "
            f"| {_median([r['elapsed'] for r in rs])}s "
            f"| {statistics.mean(r['precision'] for r in rs):.2f} "
            f"| {statistics.mean(r['recall'] for r in rs):.2f} "
            f"| {top} |"
        )
    print()
    print("Stop reasons:")
    for (version, model, task), rs in sorted(groups.items()):
        stops = Counter(r["stop"] for r in rs)
        print(f"  {version} {model} {task}: {dict(stops)}")


if __name__ == "__main__":
    main()
