from __future__ import annotations

import argparse
from pathlib import Path

from .agent import CodingAgent
from .model import OpenAICompatModel


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

    model = OpenAICompatModel(
        args.base_url, args.model, args.api_key, verbose=args.verbose
    )
    agent = CodingAgent(
        repo, model, args.test_command, args.max_iterations, verbose=args.verbose
    )
    result = agent.run(args.task)
    print(f"\n[{result.iterations} iterations] {result.summary}")


if __name__ == "__main__":
    main()
