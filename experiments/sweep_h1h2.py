#!/usr/bin/env python3
"""Interleaved paired sweep for the H1/H2 experiments (amb + valid).

Drives single-run invocations of the frozen runner in BOTH checkouts
(main = current harness, worktree = v3.1.9) in a fixed interleaved
order so paired runs execute back-to-back inside the same ollama
session and no condition's runs cluster together:

    round 1..3 -> task (amb, valid) -> model (7b, 14b) -> version

24 runs total (2 tasks x 2 models x 3 rounds x 2 versions). The
historical control (msg/mid/hard) is NOT re-run; prior records are
reused. Before the first run the driver records environment parity
(python, pip freeze hash, harness commits, runner hash — must match
across checkouts) and aborts on mismatch.

Usage:
    python experiments/sweep_h1h2.py <worktree_root>
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1]
MODELS = ["qwen2.5-coder:7b", "qwen2.5-coder:14b"]
TASKS = ["amb", "valid"]
ROUNDS = 3


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _run(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def env_report(wt: Path) -> dict[str, str]:
    main_commit = _run(["git", "rev-parse", "HEAD"], MAIN)
    wt_commit = _run(["git", "rev-parse", "HEAD"], wt)
    main_runner = MAIN / "experiments" / "baseline_modify.py"
    wt_runner = wt / "experiments" / "baseline_modify.py"
    return {
        "python": sys.version.split()[0],
        "pip_freeze_sha": hashlib.sha256(
            _run([sys.executable, "-m", "pip", "freeze"], MAIN).encode()
        ).hexdigest()[:12],
        "main_commit": main_commit,
        "wt_commit": wt_commit,
        "runner_sha_main": _sha(main_runner),
        "runner_sha_wt": _sha(wt_runner),
        "ollama": _run(["ollama", "--version"], MAIN).split()[-1],
    }


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    wt = Path(sys.argv[1]).resolve()
    env = env_report(wt)
    print("=== environment parity ===")
    for k, v in env.items():
        print(f"{k}: {v}")
    if env["runner_sha_main"] != env["runner_sha_wt"]:
        print("ABORT: runner hash mismatch across checkouts")
        sys.exit(1)
    versions = [("v3.2.2-main", MAIN), ("v3.1.9-wt", wt)]
    print(f"\n=== sweep: {ROUNDS} rounds x {TASKS} x {MODELS} x 2 versions ===")
    for rnd in range(1, ROUNDS + 1):
        for task in TASKS:
            for model in MODELS:
                for label, cwd in versions:
                    print(
                        f"\n--- round {rnd} | {label} | {model} | {task} ---",
                        flush=True,
                    )
                    t0 = time.time()
                    proc = subprocess.run(
                        [
                            sys.executable,
                            "experiments/baseline_modify.py",
                            "1",
                            model,
                            "--tasks",
                            task,
                        ],
                        cwd=cwd,
                    )
                    print(
                        f"--- exit={proc.returncode} elapsed={time.time() - t0:.0f}s ---",
                        flush=True,
                    )


if __name__ == "__main__":
    main()
