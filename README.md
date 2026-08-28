# mini-opencode-mvp

A deliberately small coding-agent harness aimed at local 3B-14B models.

It exposes only a few operations to the model:

- `read_symbol`
- `read_file`
- `search_code`
- `apply_patch`
- `run_tests`
- `finish`

The controller allows one action per model turn and automatically validates edits by running a configurable test command.

## Quick start

### Nix

```bash
nix develop
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Regular Python

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Ollama

Start Ollama and pull a coding model, for example:

```bash
ollama pull qwen2.5-coder:7b
```

Then run:

```bash
mini-agent \
  --repo /path/to/repo \
  --task "Fix the failing login validation test" \
  --base-url http://localhost:11434/v1 \
  --model qwen2.5-coder:7b \
  --test-command "pytest -q"
```

## llama.cpp

Start `llama-server` with its OpenAI-compatible endpoint, then point `--base-url` at it, commonly:

```bash
--base-url http://localhost:8080/v1
```

## Safety model

Shell execution is intentionally constrained:

- model-generated arbitrary shell commands are not supported
- only the configured test command is executed
- file access is restricted to the selected repository root
- patches are applied with `git apply`
- the agent has a hard iteration limit

This is an MVP, not a secure sandbox. Run it on a disposable branch/container for untrusted tasks.

## Expected model response format

The model must emit one JSON object per turn.

Read symbol (preferred when indexed symbols exist):

```json
{"action":"read_symbol","path":"internal/firewall/service.go","symbol":"ApplyPolicy"}
```

Read:

```json
{"action":"read_file","path":"src/app.py","start":1,"end":220}
```

Search:

```json
{"action":"search_code","query":"validate_email","path":"."}
```

Patch:

```json
{"action":"apply_patch","patch":"*** unified diff here ***"}
```

Tests:

```json
{"action":"run_tests"}
```

Finish:

```json
{"action":"finish","summary":"Fixed validation and tests pass."}
```

## Architecture

```text
Task
  -> repo map (with symbol index + line ranges)
  -> relevant symbol candidates (auto-selected from task keywords)
  -> compact working context (pruned by char budget)
  -> model
  -> one validated tool action (read_symbol / read_file / search_code / apply_patch)
  -> verification
  -> repeat
```

The implementation is intentionally compact so it is easy to modify.

## Symbol indexing

The agent builds a `SymbolIndex` at startup using:

- **Tree-sitter** for Go (functions, methods, structs, interfaces, const/var groups)
- **Python stdlib `ast`** for Python (functions, async functions, classes)

If a parser fails for a file, the agent continues without symbols for that file.

The repo map includes compact symbol references with line ranges:

```text
internal/firewall/service.go
  Service struct L10-L16
  NewService() L18-L27
  ApplyPolicy() L31-L74
  (*Service) Status() L80-L102
```

`read_symbol` resolves a symbol name to its line range and reads only
`start_line - 5` through `end_line + 5`, avoiding sending entire large files
to the model.

## Context budget

Per task type:

| Type     | Max code context chars |
|----------|----------------------|
| INSPECT  | 4000                 |
| LOCATE   | 5000                 |
| MODIFY   | 8000                 |
| DEBUG    | 10000                |

When the budget is exceeded, older code snippets are pruned from the working
context and replaced with a short summary.
