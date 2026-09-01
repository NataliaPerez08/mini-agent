# AGENTS.md

## Project Overview

This repository contains a lightweight coding-agent harness designed primarily for small language models (SLMs), especially models in the 3B–14B parameter range running locally through OpenAI-compatible endpoints such as Ollama or llama.cpp.

The core design principle is:

> Use deterministic software for tasks that do not require semantic reasoning, and reserve the language model for decisions that genuinely require it.

This project is intentionally not a general-purpose clone of OpenCode, Aider, or other coding agents.

The research and engineering goal is to determine how much repository-level software engineering capability can be extracted from relatively small models when navigation, state management, context selection, verification, and failure handling are moved into the harness.

## Design Principles

Preserve these principles when modifying the project.

### 1. Deterministic before agentic

Do not ask the model to perform work that the harness can perform reliably and cheaply.

Prefer deterministic mechanisms for:

* repository scanning;
* task classification when rules are sufficient;
* symbol discovery;
* exact symbol matching;
* state transitions that do not require semantic judgment;
* context budgeting;
* duplicate-action detection;
* test execution;
* validation;
* Git operations;
* metrics and experiment logging.

Use the LLM primarily for:

* understanding code behavior;
* deciding how code should change;
* producing plans when necessary;
* generating patches;
* interpreting ambiguous failures when deterministic diagnostics are insufficient.

### 2. Small-model-first design

Assume the primary model may be a 3B–14B local model.

Do not assume frontier-model levels of:

* instruction following;
* long-context reasoning;
* tool selection;
* JSON reliability;
* multi-step planning;
* recovery from ambiguous states.

Reduce decision entropy whenever possible.

Prefer a small set of legal actions over a large general-purpose tool surface.

### 3. Context is a budget

Do not treat the model context window as storage.

The harness should send the smallest useful working set.

Prefer:

symbol reference
→ symbol fragment
→ related fragment if required
→ full file only as fallback

Avoid:

repository
→ many files
→ full transcript
→ model

Do not duplicate code between active context and working memory.

### 4. Symbol-level context before full-file context

For source code with an indexed relevant symbol, prefer `read_symbol`.

Use `read_file` primarily for:

* README files;
* small configuration files;
* files without useful indexed symbols;
* parser failures;
* genuinely file-level tasks.

An exact symbol explicitly mentioned in the task should normally be resolved deterministically before asking the model what to read.

### 5. Verification is mandatory for modifications

A successful patch is not equivalent to a successful task.

Modification and debugging tasks must transition through verification.

Prefer targeted verification first when available.

Do not report a modification as successful when required verification failed.

### 6. Budgets are control-flow constraints

Iteration, read, repair, context, and LLM-call budgets are part of the state machine.

Do not implement budget exhaustion as an ordinary retryable tool error.

When a budget is exhausted, transition, finalize, or fail deterministically.

Do not solve orchestration bugs by simply increasing budgets.

## Current Architecture

The intended high-level architecture is:

```text
User Task
    |
    v
Repository Preflight
    |
    +-- filesystem scan
    +-- language detection
    +-- Git state
    +-- task classification
    |
    v
Symbol Index
    |
    +-- Go: Tree-sitter
    +-- Python: AST / supported parser
    |
    v
Task-Relevant Context Selection
    |
    v
State Machine
    |
    +-- DISCOVER
    +-- READ
    +-- PLAN
    +-- EDIT
    +-- VERIFY
    +-- REPAIR
    +-- DONE / FAILED
    |
    v
Small Language Model
    |
    v
Patch
    |
    v
Deterministic Verification
```

See `ROADMAP.md` for planned architecture beyond the current implementation.

## State Machine

Current states are conceptually:

```text
DISCOVER
READ
PLAN
EDIT
VERIFY
REPAIR
DONE
FAILED
```

Read-only tasks should take short paths.

Typical inspect path:

```text
DISCOVER -> READ -> DONE
```

Typical modification path:

```text
DISCOVER
-> READ
-> PLAN
-> EDIT
-> VERIFY
-> DONE
```

Failed verification may transition through:

```text
VERIFY -> REPAIR -> EDIT
```

Do not introduce arbitrary state transitions.

When adding actions, explicitly define:

* states where the action is legal;
* budgets it consumes;
* trace events it produces;
* failure behavior;
* tests for invalid use.

## Model Action Protocol

Model output must use the canonical `action` field.

Example:

```json
{
  "action": "read_symbol",
  "path": "internal/firewall/service.go",
  "symbol": "ApplyPolicy"
}
```

Do not introduce competing terms such as:

```text
next_action
command
operation
tool
```

Missing or invalid actions must be treated as structured model-output failures.

Never silently convert a missing action into an empty string.

When possible, expose only actions legal in the current state.

Local validation is mandatory even when the inference provider supports structured output or JSON schema.

## Repository Context

Repository maps are selected context, not dumps of the symbol index.

The symbol index may contain hundreds or thousands of symbols.

The model should normally see only a task-relevant subset.

For example:

```text
169 indexed symbols
        |
        v
deterministic relevance ranking
        |
        v
5–10 symbol references
        |
        v
1–3 active code fragments
        |
        v
LLM
```

Respect configured repository-map and active-code limits on the final rendered strings, not merely intermediate structures.

## Experimentation

Every meaningful run should be observable through the experiment logger.

Experiment data is stored conceptually as:

```text
experiments/
├── runs.jsonl
└── traces/
    └── <run_id>.jsonl
```

`runs.jsonl` contains one summary per run.

Trace files contain high-level execution events.

Do not store by default:

* complete source files;
* complete prompts;
* API keys;
* authorization headers;
* environment secrets;
* unnecessarily large model responses.

Metrics and metadata are preferred over raw sensitive content.

When changing agent behavior, preserve or extend experiment instrumentation.

Important metrics include:

* success;
* stop reason;
* LLM calls;
* tool calls;
* prompt tokens;
* completion tokens;
* elapsed time;
* files read;
* symbol reads;
* full-file reads;
* patches;
* tests;
* repository-map size;
* active-code size;
* working-memory size;
* symbol-context size.

## Harness Versions

Behavioral changes that affect experiments should update the harness version.

Examples:

```text
v3.0
v3.0.1
v3.1
v3.1.1
v4
```

Do not derive the experimental version from the Git branch.

Experiments must be attributable to both:

* harness version;
* Git commit.

Do not rewrite historical experiment records to make newer versions look cleaner.

Failed runs are experimental data.

## Development Environment

The project supports Python 3.10+.

Do not introduce syntax or dependencies that silently require Python 3.11+ unless the supported version is deliberately changed.

The harness may communicate with local inference servers through an OpenAI-compatible API.

Typical development targets include:

* Ollama;
* llama.cpp-compatible servers.

Do not couple core architecture unnecessarily to a single provider.

## Testing

Run the project test suite after runtime changes.

At minimum:

```bash
pytest -q
```

New behavior should include regression tests.

Prefer deterministic fake-model tests over live-model tests for unit and integration coverage.

Tests involving agent control flow should verify both final results and relevant intermediate behavior, such as:

* state transitions;
* action availability;
* tool calls;
* budget consumption;
* stop reasons;
* experiment events.

A bug discovered through a real model run should normally become a regression test before or alongside the fix.

## Benchmark Discipline

Do not optimize based on one successful-looking transcript.

When comparing harness versions, preserve:

* repository;
* task;
* model;
* inference configuration;
* test command.

Compare measurable outcomes.

Do not claim an optimization improved the harness solely because it reduced tokens.

Correctness comes first.

A useful optimization should ideally improve one or more of:

* solve rate;
* tokens per successful task;
* latency per successful task;
* LLM calls per successful task;
* unnecessary file reads;
* repair iterations.

without unacceptable correctness regression.

## Change Discipline

Keep changes focused.

Do not combine unrelated architectural phases in one change.

For example, a Symbol Context change should not simultaneously introduce:

* LSP;
* embeddings;
* a reviewer model;
* a router model;
* a vector database;
* unrestricted shell execution.

Prefer one hypothesis per meaningful architectural change so its effect can be measured.

Avoid large refactors unless they are required for the requested behavior.

## Security

Repository paths must remain constrained to the configured repository root.

Do not allow path traversal to escape the repository.

Do not expose arbitrary shell execution to the model unless a future design explicitly introduces a sandboxed mechanism.

Never log secrets.

Treat repository contents as potentially sensitive.

## Definition of Done

Before marking a runtime change complete:

1. The implementation is focused on the requested behavior.
2. Existing tests pass.
3. New behavior has appropriate tests.
4. Relevant regression tests exist.
5. Experiment logging still works.
6. Metrics affected by the change are captured.
7. Python compatibility is preserved.
8. No unrelated subsystem was silently introduced.
9. The final report states what changed and what was verified.

For performance-oriented changes, include before/after experiment results when a comparable baseline exists.
