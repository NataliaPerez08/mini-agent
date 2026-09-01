#!/usr/bin/env python3
"""Benchmark v3.0.1 vs v3.1 (spec symbol-context-v3-1.txt, section 37).

Runs the exact baseline INSPECT task against a repo with a live model and
prints the comparison table against the recorded v3.0.1 baseline.

Usage:
    python experiments/benchmark.py [--repo PATH] [--base-url URL] [--model NAME]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mini_agent.agent import CodingAgent
from mini_agent.experiments import (
    ExperimentLogger,
    generate_run_id,
)
from mini_agent.model import OpenAICompatModel

BASELINE_TASK = "Revisa que hay en la carpeta y dime que hace el proyecto"

V301_BASELINE = {
    "success": True,
    "llm_calls": 2,
    "prompt_tokens": 4180,
    "completion_tokens": 83,
    "max_prompt_chars": 10309,
    "total_prompt_chars_sent": 12457,
    "elapsed_seconds": 64.4,
    "files_read": 1,
    "symbol_reads": 0,
    "full_file_reads": 1,
    "symbol_context_chars": 0,
}

METRICS = [
    "success",
    "llm_calls",
    "prompt_tokens",
    "completion_tokens",
    "max_prompt_chars",
    "total_prompt_chars_sent",
    "elapsed_seconds",
    "files_read",
    "symbol_reads",
    "full_file_reads",
    "symbol_context_chars",
]


def find_baseline_run(runs_path: Path) -> dict | None:
    if not runs_path.exists():
        return None
    for line in reversed(runs_path.read_text().splitlines()):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            rec.get("run_id", "").startswith("3b51787e")
            and rec.get("prompt_tokens") == 4180
        ):
            return rec
    return None


def run_task(
    repo: Path,
    task: str,
    base_url: str,
    model_name: str,
    experiments_dir: Path,
) -> dict:
    run_id = generate_run_id()
    logger = ExperimentLogger(run_id, experiments_dir, enabled=True)
    model = OpenAICompatModel(base_url, model_name, "local", verbose=False)
    agent = CodingAgent(repo, model, "", verbose=False, experiment_logger=logger)
    result = agent.run(task)
    return {
        "run_id": run_id,
        "success": result.final_state == "done",
        "stop_reason": result.stop_reason,
        "llm_calls": result.llm_calls,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "max_prompt_chars": result.max_prompt_chars,
        "total_prompt_chars_sent": result.total_prompt_chars_sent,
        "elapsed_seconds": round(result.elapsed, 1),
        "files_read": result.files_read,
        "symbol_reads": result.symbol_reads,
        "full_file_reads": result.full_file_reads,
        "symbol_context_chars": result.symbol_context_chars,
        "repo_map_chars": result.repo_map_chars,
        "symbol_index_symbols": result.symbol_index_symbols,
    }


def print_table(v301: dict, v31: dict) -> None:
    width = max(len(m) for m in METRICS) + 2
    print(f"\n{'metric':<{width}}{'v3.0.1':>12}{'v3.1':>12}")
    print("-" * (width + 24))
    for m in METRICS:
        old = v301.get(m)
        new = v31.get(m)
        old_s = str(old) if old is not None else "-"
        new_s = str(new) if new is not None else "-"
        print(f"{m:<{width}}{old_s:>12}{new_s:>12}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--repo", default=str(root / "iptables-visualizer"))
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument("--experiments-dir", default=str(root / "experiments"))
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise SystemExit(f"Not a directory: {repo}")

    import httpx

    try:
        httpx.get(f"{args.base_url}/models", timeout=5)
    except Exception as exc:
        raise SystemExit(
            f"Model not reachable at {args.base_url} ({exc}).\n"
            "Start the model server (e.g. ollama serve) and re-run:\n"
            f"  python experiments/benchmark.py --repo {repo}"
        ) from exc

    experiments_dir = Path(args.experiments_dir).resolve()

    recorded = find_baseline_run(experiments_dir / "runs.jsonl")
    v301 = dict(V301_BASELINE)
    if recorded:
        for m in METRICS:
            if m in recorded and recorded[m] is not None:
                v301[m] = recorded[m]
        print(f"Using recorded v3.0.1 baseline run: {recorded['run_id'][:8]}")
    else:
        print("Using v3.0.1 baseline metrics from spec (run 3b51787e)")

    print(f"\nRunning v3.1 baseline task against: {repo}")
    print(f"Task: {BASELINE_TASK!r}")
    t0 = time.time()
    v31 = run_task(repo, BASELINE_TASK, args.base_url, args.model, experiments_dir)
    wall = time.time() - t0
    print(f"Completed in {wall:.1f}s (run_id {v31['run_id'][:8]})")
    print(f"stop_reason: {v31['stop_reason']}")

    print_table(v301, v31)

    print(f"\nrepo_map_chars: {v31['repo_map_chars']} (limit 5000)")
    print(f"symbol_index_symbols: {v31['symbol_index_symbols']}")

    goals = {
        "prompt_tokens < 2500": v31["prompt_tokens"] < 2500,
        "max_prompt_chars < 7000": v31["max_prompt_chars"] < 7000,
        "llm_calls <= 2": v31["llm_calls"] <= 2,
        "success": v31["success"],
    }
    print("\nGoals (soft, not hard fail):")
    for goal, ok in goals.items():
        print(f"  {'PASS' if ok else 'MISS'} {goal}")


if __name__ == "__main__":
    main()
