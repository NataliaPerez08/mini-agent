from __future__ import annotations

import argparse
from pathlib import Path

from .agent import CodingAgent
from .experiments import ExperimentLogger, generate_run_id
from .model import OpenAICompatModel
from .version import HARNESS_VERSION


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Minimal coding-agent harness for local SLMs"
    )
    p.add_argument("--repo", required=True, help="Repository root")
    p.add_argument("--task", required=True, help="Coding task")
    p.add_argument("--base-url", default="http://localhost:11434/v1")
    p.add_argument("--model", default="qwen2.5-coder:7b")
    p.add_argument("--api-key", default="local")
    p.add_argument("--test-command", default="")
    p.add_argument("--max-iterations", type=int, default=12)
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Print detailed per-iteration logs"
    )
    p.add_argument(
        "--experiment-log",
        dest="experiment_log",
        action="store_true",
        default=True,
        help="Enable experiment logging (default)",
    )
    p.add_argument(
        "--no-experiment-log",
        dest="experiment_log",
        action="store_false",
        help="Disable experiment logging",
    )
    p.add_argument(
        "--experiments-dir",
        default="./experiments",
        help="Directory for experiment logs (default: ./experiments)",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    repo = Path(args.repo)
    if not repo.is_dir():
        raise SystemExit(f"Not a directory: {repo}")
    if not (repo / ".git").exists():
        raise SystemExit(
            "Repository must be a git worktree (git apply is used for patches)."
        )

    run_id = generate_run_id()
    experiments_dir = Path(args.experiments_dir)
    logger = ExperimentLogger(
        run_id, experiments_dir, enabled=args.experiment_log
    )

    if args.verbose:
        print("[experiment]")
        print(f"run_id: {run_id}")
        print(f"harness_version: {HARNESS_VERSION}")
        print(f"trace: {logger.trace_path}")

    model = OpenAICompatModel(
        args.base_url, args.model, args.api_key, verbose=args.verbose
    )
    agent = CodingAgent(
        repo,
        model,
        args.test_command,
        args.max_iterations,
        verbose=args.verbose,
        experiment_logger=logger,
    )
    result = agent.run(args.task)

    if args.verbose and args.experiment_log:
        print("[experiment]")
        print(f"saved: {logger.runs_path}")

    print(f"\n[{result.iterations} iterations] {result.summary}")


if __name__ == "__main__":
    main()
