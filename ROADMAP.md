# ROADMAP.md

## Mission

Build a lightweight coding-agent harness that allows small language models, especially locally hosted 3B–14B coding models, to perform useful repository-level software engineering with substantially less agentic overhead.

The central hypothesis is:

> A small language model paired with deterministic repository navigation, context selection, state management, and verification can outperform the same model using a generic agent loop and may approach larger models on selected software-engineering tasks at significantly lower inference cost.

This project treats the language model as a semantic reasoning component rather than the operating system of the agent.

## Research Questions

The project is intended to eventually support empirical investigation of questions such as:

1. How much coding-agent performance comes from the model versus the harness?
2. How much can deterministic orchestration reduce the model size required for repository-level tasks?
3. Which agent responsibilities should remain model-driven and which are better implemented deterministically?
4. How much do symbol-level context selection and semantic code navigation reduce token consumption?
5. Does restricting the action space improve small-model reliability?
6. What is the relationship between harness determinism and task success?
7. Can a 7B–14B model with a specialized harness compete with substantially larger models using generic agent loops?
8. What is the compute cost per successfully resolved task?

## Evaluation Philosophy

Every major architecture phase should be measurable.

Primary metrics:

```text
solve rate
LLM calls
input tokens
output tokens
elapsed time
tool calls
file reads
symbol reads
patch attempts
repair cycles
test executions
```

Derived metrics of particular interest:

```text
input tokens / successful task
LLM calls / successful task
seconds / successful task
compute / successful task
```

Optional later metrics:

```text
peak VRAM
energy consumption
tokens/sec
GPU-seconds / successful task
```

Correctness takes priority over token reduction.

## Completed Foundations

### v0 — Minimal Agent Loop

Initial harness capabilities:

* OpenAI-compatible model endpoint;
* repository-scoped file access;
* code search;
* patch application;
* test execution;
* bounded iteration loop;
* basic loop detection;
* compact working memory.

Primary purpose:

Establish the smallest functional coding-agent baseline.

### v1 — Repository Preflight

Move deterministic repository discovery out of the LLM loop.

Capabilities:

* filesystem scan;
* ignored-directory handling;
* language detection;
* important-file detection;
* test-file detection;
* Git status;
* basic repository map;
* Python symbol discovery;
* initial context construction.

Result:

The model no longer needs to spend inference calls discovering information the harness can obtain directly.

### v2 — Explicit State Machine

Introduce explicit task types and control flow.

Task types:

```text
INSPECT
LOCATE
MODIFY
DEBUG
```

States:

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

Capabilities:

* state-specific actions;
* dynamic budgets;
* automatic verification after edits;
* duplicate-action prevention;
* constrained repair cycles;
* deterministic fast paths for simple tasks.

Observed result:

A simple inspection task dropped from approximately five LLM calls to two.

### Experiment Logging v0

Add structured experimental observability.

Artifacts:

```text
experiments/runs.jsonl
experiments/traces/<run_id>.jsonl
```

Capabilities:

* run IDs;
* harness version;
* Git metadata;
* token usage;
* latency;
* tool metrics;
* state transitions;
* stop reasons;
* context-size measurements;
* trace events.

This becomes the measurement foundation for subsequent versions.

## Current Phase

### v3.1 — Symbol-Aware Context

Status: implemented and under validation.

Goal:

Replace broad source-file reads with precise symbol-level context.

Capabilities:

* Tree-sitter-based Go indexing;
* Python symbol indexing;
* functions;
* methods;
* structs;
* interfaces;
* compact symbol-aware repository map;
* task-to-symbol relevance;
* `read_symbol`;
* symbol-context metrics;
* full-file fallback.

Observed successful localized inspection:

```text
indexed_symbols: 6
symbol_reads: 2
full_file_reads: 0
prompt_tokens: 1791
symbol_context_chars: 579
```

Symbol-level context is functioning for inspection tasks.

### v3.1.1 — MODIFY Path Hardening

Status: implemented (HARNESS_VERSION v3.1.1).

Motivation:

A localized modification task exposed several orchestration failures despite successful symbol indexing:

```text
indexed_symbols: 169
symbol_reads: 0
full_file_reads: 1
repo_map_chars: 8391
```

The model also emitted an invalid action structure:

```json
{
  "next_action": "none"
}
```

Goals:

* deterministic exact-symbol matching from task text;
* auto-load exact target symbols for localized MODIFY/DEBUG tasks;
* task-focused repository maps;
* enforce repository-map hard limits;
* canonical `action` output contract;
* local structured-output validation;
* state-specific action enums;
* bounded invalid-output recovery;
* transition toward PLAN after sufficient target context;
* prevent empty or impossible actions from reaching the state machine.

Exit criteria:

A task such as:

```text
Corrige ApplyPolicy para que devuelva un error cuando Ports esté vacío
```

should approximately follow:

```text
DISCOVER
    |
    v
exact ApplyPolicy match
    |
    v
read_symbol
    |
    v
PLAN
    |
    v
EDIT
    |
    v
VERIFY
    |
    v
DONE
```

Target:

```text
success: true
LLM calls: <= 4
symbol reads: >= 1
initial target full-file reads: 0
patches: 1
tests: >= 1
```

Do not proceed to major new architecture until the MODIFY path is reliable.

Implementation notes for v3.1.1:

* `find_exact_task_symbol` resolves the longest task identifier that exactly
  matches an indexed symbol name (accent-insensitive); localized
  MODIFY/DEBUG runs auto-load that symbol via `read_symbol` in `_setup`
  before the first LLM call.
* `validate_action_object` / `parse_model_output` enforce the canonical
  `action` field locally: `next_action`, missing or empty actions are
  structured model-output failures, never silently coerced to an empty
  string.
* Invalid outputs produce bounded recovery feedback (with a 3x identical
  repeat warning) and log `invalid_action` events with reason
  `model_output_contract`.
* The ApplyPolicy scenario now measures `symbol_reads >= 1`,
  `full_file_reads == 0`, `llm_calls <= 4`, `patches == 1`, `tests >= 1`
  under the integration tests in `tests/test_v311_hardening.py`.

### v3.1.2 — Observability of Deterministic Pre-Loads

Status: implemented (HARNESS_VERSION v3.1.2).

Motivation:

The INSPECT fast path and the v3.1.1 auto-symbol load ran deterministically
but emitted no trace events and carried no run-summary detail, so an experiment
trace could not reconstruct what the harness loaded or why.

Capabilities:

* `auto_read` trace events for INSPECT auto-reads and `auto_symbol_read` trace
  events for the MODIFY/DEBUG exact-symbol load, each with a `reason` field;
* `run_start`/`symbol_index` events reordered before pre-loads so traces start
  coherently;
* `auto_read_files` and `auto_symbols` (list of `"path::Name"`) carried through
  `AgentResult` into `runs.jsonl`.

Regression tests live in `tests/test_observability.py`.

## Current Known Issues

Open problems, limitations, and open questions are tracked in
`CURRENT_ISSUES.md`, not inline in this roadmap.

Two items shape the priority of the planned phases below:

* **Unverified patches can reach DONE** (CURRENT_ISSUES 1.2): with an empty
  `test_command`, the harness has no way to prove a modification works.
  Resolving this is a precondition for trusting MODIFY success rates.
* **Targeted verification is pytest-shaped** (CURRENT_ISSUES 2.1, 2.2): the
  v6 Targeted Verification phase must generalize beyond `test_*.py` naming and
  pytest-based commands.

The v4 (LSP) and v5 (impact graph) phases directly address symbol
disambiguation (CURRENT_ISSUES 4.2).

## Planned Phases

### v4 — LSP Integration

Goal:

Add semantic code intelligence that does not require an LLM call.

Initial capabilities:

* go-to-definition;
* find references;
* diagnostics;
* type/hover information where useful.

Initial language target:

Go first.

Potential later targets:

Python and other languages supported by the harness.

Principle:

Tree-sitter answers:

> What syntactic symbols exist?

LSP should help answer:

> What does this symbol refer to and what refers to it?

Expected architecture:

```text
Task
 |
 v
Symbol Index
 |
 v
LSP
 +-- definition
 +-- references
 +-- diagnostics
 |
 v
Context Selector
 |
 v
SLM
```

Do not expose the complete LSP protocol directly to the model.

The harness should convert LSP results into small, task-relevant structures.

### v5 — Dependency and Impact Relevance

Goal:

Determine what code is likely affected by a requested change.

Use information such as:

* imports;
* symbol references;
* callers;
* callees where available;
* package relationships;
* test relationships.

Do not initially build a complex persistent graph database.

Prefer an on-demand impact graph.

Example:

```text
ApplyPolicy
├── called by PolicyHandler
├── uses validatePorts
├── writes through PolicyStore
└── related to service_test.go
```

Use the graph for context selection rather than dumping it into the prompt.

### v6 — Targeted Verification

Goal:

Reduce verification cost while preserving confidence.

Current baseline (CURRENT_ISSUES 2.1, 2.2): `_find_related_tests` only matches
`test_*.py` / `*_test.py`, and `run_targeted_tests` only narrows pytest-style
commands. Go repos and non-pytest frameworks run the full configured command.

Instead of always starting with:

```text
go test ./...
```

prefer:

```text
affected symbol
    |
    v
related test
    |
    v
package tests
    |
    v
broader suite if needed
```

Capabilities may include:

* test-file relevance;
* symbol-to-test association;
* package-level verification;
* escalation from narrow to broad tests.

Verification remains deterministic.

### v7 — Diagnostic-Driven Repair

Goal:

Turn failures into minimal repair context.

Flow:

```text
PATCH
 |
 v
VERIFY
 |
 +-- PASS -> DONE
 |
 +-- FAIL
       |
       v
parse diagnostic
       |
       v
map failure to symbol
       |
       v
fetch relevant definition/references
       |
       v
small repair context
       |
       v
SLM
```

Do not resend the entire previous working context for every repair.

Track repair cycles explicitly.

### v8 — Context Compaction

Goal:

Support longer tasks without turning conversation history into an ever-growing prompt.

Maintain structured working state such as:

```text
FACTS
CHANGED FILES
ACTIVE SYMBOLS
LATEST FAILURE
CURRENT PLAN
```

Old raw tool output should be summarized or discarded when it no longer needs to remain active.

Distinguish:

* repository context selection;
* active code;
* durable working facts;
* conversation history.

The model should not receive the full execution transcript by default.

### v9 — Planner / Reviewer Experiment

Status: experimental, not assumed to be beneficial.

Only after deterministic orchestration is mature, evaluate whether an additional reasoning pass improves success enough to justify its cost.

Possible configurations:

```text
single SLM
vs
planner + coder
vs
coder + reviewer
```

Measure:

* solve rate;
* additional tokens;
* additional latency;
* prevented bad patches;
* unnecessary reviewer interventions.

Do not assume more agents means a better agent.

### v10 — Multi-Model / Router Experiment

Evaluate whether different model sizes should handle different semantic workloads.

Possible strategy:

```text
simple reasoning -> 3B
normal coding    -> 7B
difficult repair -> 14B+
```

Routing must be evaluated against the simpler single-model baseline.

Do not introduce routing solely because multiple models are available.

## Explicit Non-Goals for Early Versions

Until evidence suggests otherwise, avoid introducing:

* vector databases;
* repository embeddings;
* generic RAG infrastructure;
* unrestricted shell access;
* large subagent hierarchies;
* autonomous web browsing;
* persistent graph databases;
* complex distributed-agent protocols.

These features may eventually be useful, but they increase the number of variables in the experiment.

The project should first determine how far deterministic local tooling can take a small coding model.

## Benchmark Plan

Once v3–v8 are sufficiently stable, establish a fixed benchmark suite.

Task classes should include:

### INSPECT

Examples:

```text
Explain what this package does.
Explain how ApplyPolicy works.
```

### LOCATE

Examples:

```text
Find where policies are validated.
Find the code responsible for firewall status.
```

### MODIFY

Examples:

```text
Reject empty ports in ApplyPolicy.
Add validation for an invalid policy.
```

### DEBUG

Examples:

```text
Fix the failing policy validation test.
Diagnose and repair this regression.
```

Each task should have objective success criteria where possible.

## Ablation Study

The architecture naturally supports an ablation study.

Candidate configurations:

| Configuration | Features                |
| ------------- | ----------------------- |
| A             | Minimal agent loop      |
| B             | + repository preflight  |
| C             | + state machine         |
| D             | + symbol context        |
| E             | + LSP                   |
| F             | + dependency relevance  |
| G             | + targeted verification |
| H             | + diagnostic repair     |
| I             | + context compaction    |

This should help answer which harness components actually improve performance.

## Model Scaling Experiment

A later experiment should compare several model sizes under the same harness.

For example:

```text
3B
7B
14B
32B
```

Compare each under:

```text
generic agent loop
specialized deterministic harness
```

Conceptual experiment matrix:

| Model | Generic Harness | Specialized Harness |
| ----- | --------------: | ------------------: |
| 3B    |               ? |                   ? |
| 7B    |               ? |                   ? |
| 14B   |               ? |                   ? |
| 32B   |               ? |                   ? |

The important question is not merely which configuration solves the most tasks.

The project should also measure:

> How much compute is required per successfully resolved task?

## Thesis Direction

A possible research framing is:

**Reducing Agentic Complexity: Enabling Small Language Models for Software Engineering through Deterministic Tool Orchestration**

Potential hypotheses:

**H1 — Context efficiency**

Structure-aware deterministic context selection significantly reduces input-token usage compared with generic file-level agent exploration.

**H2 — Orchestration reliability**

Explicit state machines and constrained action spaces reduce redundant tool calls and failed agent loops for small models.

**H3 — Verification efficiency**

Targeted deterministic verification reduces repair cost while preserving task correctness.

**H4 — Model/harness tradeoff**

For selected repository-level software-engineering tasks, a small model with a specialized deterministic harness can approach or exceed the performance of a larger model operating through a generic agent loop at lower computational cost.

These are hypotheses to test, not conclusions the project assumes to be true.

## Long-Term Architecture

The intended mature architecture is approximately:

```text
                     USER TASK
                         |
                         v
                  TASK CLASSIFIER
                         |
                         v
                   REPO PREFLIGHT
                         |
             +-----------+-----------+
             |                       |
             v                       v
        SYMBOL INDEX                LSP
             |                       |
             +-----------+-----------+
                         |
                         v
              RELEVANCE / IMPACT GRAPH
                         |
                         v
                 CONTEXT SELECTOR
                         |
                         v
                   STATE MACHINE
                         |
               +---------+---------+
               |                   |
               v                   v
        deterministic tools       SLM
               |                   |
               +---------+---------+
                         |
                         v
                       PATCH
                         |
                         v
               TARGETED VERIFICATION
                         |
                  +------+------+
                  |             |
                 PASS          FAIL
                  |             |
                  v             v
                 DONE      DIAGNOSTIC
                                |
                                v
                              REPAIR
```

The intended division of responsibility is:

```text
PROGRAM
├── repository navigation
├── symbol indexing
├── semantic lookup
├── state management
├── context selection
├── budgets
├── verification
├── experiment logging
└── failure routing

SLM
├── semantic code understanding
├── implementation reasoning
├── patch generation
└── ambiguous failure reasoning
```

## Guiding Constraint

Every proposed feature should answer:

> Does this require language-model reasoning?

If the answer is no, strongly prefer implementing it deterministically.

Every proposed LLM call should answer:

> What semantic decision are we paying the model to make?

If there is no good answer, the call probably should not exist.
