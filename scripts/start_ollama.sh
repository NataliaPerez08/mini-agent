#!/usr/bin/env bash
# Start a persistent Ollama server and ensure the test model is pulled.
# Usage: scripts/start_ollama.sh   (override with OLLAMA_HOST / OLLAMA_TEST_MODEL)
set -euo pipefail

export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
MODEL="${OLLAMA_TEST_MODEL:-qwen2.5-coder:14b}"
LOG="${OLLAMA_LOG:-/tmp/ollama-serve.log}"
URL="http://${OLLAMA_HOST}/v1/models"

command -v ollama >/dev/null || { echo "ERROR: ollama is not installed" >&2; exit 1; }

if curl -sf "$URL" >/dev/null 2>&1; then
    echo "Ollama already running at $URL"
else
    echo "Starting ollama serve (log: $LOG) ..."
    nohup ollama serve >"$LOG" 2>&1 &
    for _ in $(seq 1 60); do
        curl -sf "$URL" >/dev/null 2>&1 && break
        sleep 0.5
    done
    curl -sf "$URL" >/dev/null 2>&1 || {
        echo "ERROR: ollama did not become ready at $URL; see $LOG" >&2
        exit 1
    }
    echo "Ollama ready at $URL"
fi

if ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "Model $MODEL already pulled"
else
    echo "Pulling $MODEL ..."
    ollama pull "$MODEL"
fi

echo "Done. Stop with: pkill -f 'ollama serve'"
