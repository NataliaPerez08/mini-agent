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

## Completed Through v3.1.3

### v3.1 — Symbol-Aware Context

Status: implemented and validated.

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

Validation: `pytest -q` passes with 392 tests passed and 1 skipped.

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

### v3.1.3 — Unverified Patches Cannot Reach DONE

Status: implemented (HARNESS_VERSION v3.1.3).

Motivation:

CURRENT_ISSUES 1.2: with an empty `test_command`, `_run_verification` returned
success without executing anything, so a MODIFY task could reach `DONE` with a
patch that was never verified, and `tests_run` counted a test that never ran.

Behavior:

* `_run_verification` now reports whether verification actually ran;
* an unverified patch transitions VERIFY -> FAILED with
  `stop_reason="unverified_patch"` — no repair cycle and no extra LLM call,
  because there is no failure output to repair from;
* `tests_run` stays 0 and the trace records a `test` event with
  `status="unverified"`;
* runs with a configured test command are unchanged.

Regression tests live in `tests/test_unverified_patch.py`.

### v3.1.4 — Live-Run Hardening: Patch-less Finishes and Receiver-Qualified Symbols

Status: implemented (HARNESS_VERSION v3.1.4).

Motivation:

The first live end-to-end run (`tests/test_live_ollama.py`, qwen2.5-coder:7b
against iptables-visualizer) exposed two harness gaps:

* the model escaped a MODIFY task with a false `finish` ("task completed")
  and zero patches, which the harness reported as `completed`;
* the repo map renders Go methods as `(*Receiver).Name()`, the model copied
  that format into `read_symbol`, and the harness failed to resolve it —
  burning read budget on a failed read.

Behavior:

* `finish` on a MODIFY/DEBUG task with zero applied patches is rejected once
  with imperative feedback; a repeated finish is accepted as an honest
  no-change-needed outcome with `stop_reason="completed_without_patch"`, so
  patch-less completions no longer inflate solve rates;
* `read_symbol` resolves receiver-qualified names (`(*Service).Status` ->
  `Status`), and the plain and qualified forms count as the same symbol for
  duplicate detection;
* trace events `patchless_finish` (accepted true/false) record both paths.

Regression tests live in `tests/test_v314_hardening.py`.

### v3.1.5 — Live-Run Hardening: Small-Model Patch Tolerance

Status: implemented (HARNESS_VERSION v3.1.5).

Motivation:

Iterating the live end-to-end test (`tests/test_live_ollama.py`,
qwen2.5-coder:7b/14b against iptables-visualizer) showed that a 7B–14B
model repeatedly produces *mechanically defective but semantically
reasonable* patches, and the harness rejected them instead of repairing
them deterministically. Each defect consumed the whole LLM-call budget:

* patch JSON with literal newlines inside the string value (invalid
  strict JSON) — repeated identically until budget death;
* unified diffs missing the leading space marker on context lines
  (`git apply`: "corrupt patch");
* context lines re-typed with degraded indentation, including
  whitespace dropped entirely (`}` vs `\t}`), which
  `git apply --ignore-whitespace` alone still rejects;
* repair patches with no `--- a/` / `+++ b/` file headers at all;
* `apply_patch` actions with a missing `patch` field, which reached
  `git apply` as an empty patch, counted as a failed patch, and bounced
  REPAIR -> READ where patching is illegal.

Behavior:

* `extract_json_object` parses with `json.loads(..., strict=False)`,
  accepting control characters inside JSON strings;
* `normalize_patch` (tools.py) repairs missing context-line markers and
  empty in-hunk lines before `git apply`; well-formed patches pass
  through unchanged;
* `git apply` now runs with `--ignore-whitespace -C1 --recount`,
  tolerating whitespace-degraded context and miscounted hunks;
* `apply_patch` with an empty/missing `patch` field is rejected as a
  model-output contract violation (`invalid_action`,
  `reason="missing_patch_field"`) and the run stays in the current
  state instead of transitioning on a failed patch;
* a headerless patch starting at `@@` gets `--- a/<path>` / `+++ b/<path>`
  headers synthesized from the action's `files`/`path` field;
* when a patch fails to apply but its added lines are already present
  in the working tree, the failure feedback says so explicitly and
  demands a different patch (the observed loop was the model resending
  an already-applied patch after failed verification);
* `finish` on a MODIFY/DEBUG task whose verification failed is recorded
  as an honest failure (final state FAILED,
  `stop_reason="test_failure"`) instead of `completed` — closing a
  solve-rate-inflation side door reachable via REPAIR -> READ -> finish;
* `OpenAICompatModel` honors its `temperature` attribute (previously
  hardcoded to 0.1 in the request payload); the live test pins 0.0;
* EDIT and REPAIR prompts include ACTIVE CODE and a canonical patch
  format example with `---`/`+++` headers; the REPAIR prompt explicitly
  tells the model the previous approach failed and demands a different
  patch; the budget-exhausted prompt no longer contradicts the system
  prompt for MODIFY/DEBUG tasks.

Result: the live MODIFY test (real `go test ./...` verification) passes
with qwen2.5-coder:14b; qwen2.5-coder:7b now fails only on semantics
(it cannot infer that the second validation issue must not be emitted),
not on patch mechanics.

Regression tests live in `tests/test_v314_hardening.py`
(`TestControlCharTolerantParsing`, `TestPatchNormalization`,
`TestEmptyPatchFieldGuard`, `TestFinishAfterFailedVerification`,
`TestEditPromptCarriesActiveCode`, `TestBudgetExhaustedPromptAlignment`).

### v3.1.6 — Go Targeted Verification and Multi-Symbol Auto-Load

Status: implemented (HARNESS_VERSION v3.1.6).

Motivation:

The live MODIFY benchmark runs showed two deterministic wastes:

* verification always ran the full configured command (`go test ./...`,
  ~40s) even when the patched file had an adjacent `_test.go` — the
  harness only knew pytest naming (CURRENT_ISSUES 2.1/2.2);
* the exact-symbol auto-load resolved a single symbol, so a task naming
  several symbols (the live task names both `Validate` and the test
  function) left the model to spend its read budget discovering what the
  harness already knew (CURRENT_ISSUES 4.1).

Behavior:

* `_find_related_tests` matches Go's same-directory convention
  (`validator.go` -> `validator_test.go`); `run_targeted_tests` builds a
  package-targeted `go test ./<pkg>/` for go-flavored commands, walking
  up to the nearest `go.mod` (with `cd` prefix for non-root modules) and
  falling back to the full command across multiple modules;
* `find_exact_task_symbols` resolves every task word that exactly
  matches an indexed symbol (longest first, deduplicated, capped at 3);
  same-name ambiguity prefers the candidate whose file is named in the
  task text, so "Corrige Validate en internal/engine/validator.go"
  resolves the engine's `Validate`, not the handler's;
* deterministic pre-loads no longer consume the model's read-action
  budget: budgets bound model decisions, not harness decisions;
* `experiments/baseline_modify.py` records the live MODIFY baseline
  into `experiments/runs.jsonl` (CURRENT_ISSUES 6.2).

Post-v3.1.6 baseline (3 runs per model, temperature 0, same repo, task,
and test command):

| Model                 | Solve | Stop reason               | LLM calls | Patches | Elapsed (median) |
|-----------------------|-------|---------------------------|-----------|---------|------------------|
| qwen2.5-coder:7b      | 0/3   | llm_call_budget_exhausted | 8         | 0       | ~51s             |
| qwen2.5-coder:14b     | 3/3   | completed                 | 7         | 1       | ~294s            |

The 7B failure is semantic, not mechanical: the auto-load provides the
correct symbols, but the model re-requests them, exhausts its read
budget, and never patches. Failed runs are experimental data; this
table is the comparison baseline for v4+.

Regression tests: `tests/test_tools.py` (Go targeting),
`tests/test_symbols.py` (multi-symbol + path disambiguation),
`tests/test_observability.py` (multi-symbol trace),
`tests/test_v316_hardening.py` (pre-load budget).

### v3.1.7 — Model-Output and Patch Mechanics Hardening

Status: implemented (HARNESS_VERSION v3.1.7).

Motivation:

A full day of live 14B runs against the MODIFY benchmark exposed a
chain of deterministic defects. Each fix below corresponds to an
observed failing transcript and has a regression test; none was
speculative.

Model-output fixes:

* JSON extraction now returns the first *balanced* object instead of
  merging the first brace to the last (prose with trailing braces no
  longer corrupts the payload) — CURRENT_ISSUES 1.1;
* verification reads the `EXIT_CODE` marker from the first output line
  only, so a test binary that prints `EXIT_CODE=0` in its own output
  cannot be misread as passing — CURRENT_ISSUES 1.2;
* three consecutive malformed outputs fail deterministically with
  `stop_reason="invalid_output"` (new `MAX_CONSECUTIVE_INVALID` budget);
  alternating malformed outputs can no longer evade the old
  3x-identical soft bound, and the dead soft-bound branch was removed —
  CURRENT_ISSUES 3.1/3.2;
* `RunState.repeated` keeps only the last three entries — CURRENT_ISSUES
  4.4;
* `parse_model_output` repairs patch actions whose JSON string encoding
  is broken (typically unescaped quotes inside the diff) by extracting
  the `patch` value directly; fence stripping is shared with
  `extract_json_object`.

Patch-mechanics fixes:

* the already-applied check is `git apply --reverse --check` (strict
  context), replacing a substring heuristic that falsely rejected
  *different* repair patches whose short added lines (`return result`,
  `}`) appeared anywhere in the diff;
* failed patches report the current file content around the failure
  line (`CURRENT CONTENT ...`), giving the model actionable feedback
  instead of a bare "does not apply";
* working-context snippets for patched files are re-read after a
  successful patch, so repair prompts show post-patch code instead of
  the stale pre-patch fragments the model was hallucinating against;
* `normalize_patch` recomputes hunk line counts from actual content
  (models miscount; git needs `--recount`, GNU patch has no
  equivalent);
* headerless patches with no `files`/`path` infer the target file by
  searching the repo for the hunk's longest context line
  (whitespace-flexible); an ambiguous or missing target is a structured
  `missing_patch_target` failure instead of a confusing git error;
* a removal-anchored fallback applies patches whose context has
  mid-hunk gaps (the model omits lines between its anchor and the
  change; git apply rejects false adjacency claims). Each change
  cluster's removal lines must match at exactly one
  whitespace-insensitive file position, with the nearest context line
  present within a ±10-line window; otherwise the patch fails loudly.
  This fallback replaced a GNU `patch --fuzz` tier that was observed
  applying patches at wrong positions.

State-machine fixes:

* a failed patch keeps the model in EDIT/REPAIR to retry immediately
  with the failure feedback (previously the transition to READ made the
  model's corrected patch illegal and the fix was lost);
* `finish` is legal in EDIT/REPAIR as an escape hatch: the existing
  patchless-finish guard rejects the first attempt with a state-aware
  message and accepts the second as `completed_without_patch`;
* discover/read prompts state the modification-task contract up front
  ("finish will be REJECTED until a patch is applied") and list
  already-loaded symbols ("do NOT read again"), eliminating the two
  systematic wasted calls (duplicate read + premature finish) observed
  in every pre-v3.1.7 live run.

Live-test task change:

The live test task moved from the if/else restructuring (empty-name
validation) to a single-string change (empty-rules message). The old
task sits at the 14B's semantic edge: identical code and prompts
produced different first patches across ollama sessions (the v3.1.6
3/3 baseline was one lucky session; the same commit failed 0/3 in
another). The old task is preserved verbatim in
`experiments/baseline_modify.py` (`HARD_TASK`) for honest capability
tracking; the live test needs a task the model passes reliably so it
can gate harness plumbing. Post-v3.1.7 the live run completes in 3 LLM
calls (~130s): narrative diff -> actionable failure feedback ->
corrected patch -> anchor-apply -> targeted verify -> done.

Regression tests: `tests/test_v317_hardening.py` (JSON extraction,
lenient patch parsing, EXIT_CODE first-line, consecutive-invalid budget,
bounded repeats, already-applied strictness, snippet refresh,
headerless-target inference, position/anchor fallbacks, stay-in-EDIT
retry), plus updated contracts in `tests/test_state.py` and
`tests/test_v311_hardening.py`.

Post-v3.1.7 baseline on the HARD task (3 runs per model, temperature 0,
same repo/task/test command as the v3.1.6 table):

| Model                 | Solve | Stop reason               | LLM calls | Patches | Tests | Elapsed (median) |
|-----------------------|-------|---------------------------|-----------|---------|-------|------------------|
| qwen2.5-coder:7b      | 0/3   | test_failure              | 5–7       | 3       | 3     | ~231s            |
| qwen2.5-coder:14b     | 0/3   | llm_call_budget_exhausted | 8         | 0       | 0     | ~499s            |

Reading the table honestly:

* The 7B moved from mechanical death (v3.1.6: 0 patches,
  `llm_call_budget_exhausted` in ~51s) to semantic failure: it now
  applies 3 patches and runs the tests 3 times per run. The harness
  unblocked the mechanics; what remains is model capability.
* The 14B's v3.1.6 3/3 was one lucky ollama session. In the v3.1.7
  session the same code and task produced narrative diffs that never
  converged (0 patches). The hard task sits at the 14B's edge; it stays
  in `experiments/baseline_modify.py` for capability tracking, while
  the live test gates on the reliably-solvable task above.
* Failed runs are experimental data; both tables stay as recorded.

### v3.1.8 — Diagnostic-Driven Repair Context (v7 core, no LSP)

Status: implemented (HARNESS_VERSION v3.1.8).

Motivation:

The v7 phase planned LSP-backed diagnostic repair, but its core loop —
failure output already names where the problem is — is deterministic
and does not need LSP:

```text
VERIFY fails
  |
  v
parse file:line from test output   (no LLM call)
  |
  v
resolve path (exact / go.mod-relative / basename)
  |
  v
SymbolIndex.at_line -> innermost symbol
  |
  v
auto-load snippet into repair context
```

Changes:

* `SymbolIndex.at_line(path, line)`: innermost indexed symbol
  containing a line. The index is already refreshed post-patch
  (v3.1.6 `reindex_files`), so diagnostic line numbers match the
  post-patch index.
* `_parse_failure_locations` (orchestrator): extracts `file:line`
  pairs from go test output (`validator_test.go:22:`), go compile
  errors (`./pkg/file.go:68:5:`), pytest short summaries
  (`tests/x.py:15:`), and pytest tracebacks
  (`File "src/x.py", line 42`). Deduped, capped at 12 locations.
  Non-path colon-number pairs (`example.com:8080`) are rejected by
  path resolution: a candidate must exist in the repository scan.
* `_resolve_failure_path`: maps diagnostic paths to repo-relative
  files — exact match, go.mod-prefix (tests run from a module
  subdirectory), then basename match preferring patched files'
  directories.
* `_load_diagnostic_symbols`: on verification failure, before
  entering REPAIR, auto-loads up to 3 diagnostic symbols that are not
  already in the working context (v3.1.7 snippet refresh keeps loaded
  symbols current, so re-loading would be duplication). Emits
  `auto_symbol_read` with `reason="test_failure_diagnostic"` and the
  source `file:line`. Pre-loads do not consume the model's read-action
  budget (v3.1.6 precedent: pre-loads are not model decisions).

Out of scope (kept for later phases): LSP references/definitions (v4+),
impact graph (v5), and the v8 context-compaction half of v7 ("do not
resend the entire working context per repair").

Regression tests: `tests/test_v318_hardening.py` (parser formats,
noise rejection, dedupe/cap, `at_line` innermost/boundaries/misses,
path resolution variants, repair-prompt content, already-loaded
dedup, line-without-symbol skip, repeated-failure dedup, read-budget
untouched).

Post-v3.1.8 baseline on the HARD task (3 runs per model, temperature
0, same repo/task/test command as the v3.1.7 table):

| Model                 | Solve | Stop reason               | LLM calls | Patches | Tests | Elapsed (median) |
|-----------------------|-------|---------------------------|-----------|---------|-------|------------------|
| qwen2.5-coder:7b      | 0/3   | test_failure              | 5–7       | 3       | 3     | ~228s            |
| qwen2.5-coder:14b     | 0/3   | llm_call_budget_exhausted | 8         | 0       | 0     | ~496s            |

Reading the table honestly: outcomes are unchanged from v3.1.7, as
expected. On this task the failure diagnostics point at `Validate`
and the test symbols, which are already auto-loaded at setup, so the
diagnostic loader is a no-op here (the already-loaded dedup path).
The change is additive repair context — capped, deduped, budget-free
— and its value appears when failures point at collaborators that
are not yet loaded, which this benchmark task does not exercise.

### v3.1.9 — Focused Repository Map (CURRENT_ISSUES 4.3)

Status: implemented (HARNESS_VERSION v3.1.9).

Motivation:

For a localized MODIFY/DEBUG task the model received a repository-wide
map (up to 60 symbol files) plus the auto-loaded target symbol. The
map's breadth is a discovery mechanism for untargeted tasks; once the
exact-symbol auto-load has resolved a target, that breadth is noise.

Changes:

* `build_repo_map(..., focus_files=...)`: when focus files are given,
  every section is ordered focus-first (focus file, then its
  directory, then the previous tiers: task-token match, important,
  non-test, test), and the SYMBOLS section is capped at
  `MAX_FOCUSED_FILES_IN_MAP = 10` files instead of 60. The FILES
  section keeps its character budget but is ordered by the same
  tiers.
* The orchestrator builds the map after the exact-symbol auto-load
  and passes the auto-loaded symbol paths as focus. Runs without a
  resolved target (no exact symbol match) keep the repository-wide
  map — narrowing must not remove the discovery mechanism when the
  harness could not localize the task. INSPECT is unchanged.
* The post-patch map rebuild (action handler) keeps the focus:
  auto-loaded symbol paths plus patched files, so repair-cycle
  prompts stay narrow.
* `repo_map_chars` (already recorded per run) captures the effect.

Regression tests: `tests/test_v319_hardening.py` (focus-first
ordering, neighborhood before unrelated, focused cap vs wide map,
FILES ordering, no-focus unchanged, orchestrator passes focus,
post-patch rebuild keeps focus).

### v3.2.0 — Reference Index (v5-lite, no LSP)

Status: implemented (HARNESS_VERSION v3.2.0).

Motivation:

The v5 phase planned an impact graph, but its cheapest useful slice
is deterministic and needs no graph database: record which top-level
symbols call which names, and use that signal where same-name
ambiguity or impact awareness matters.

Changes:

* `mini_agent/references.py`: per-file call-site extraction — Go via
  tree-sitter (`call_expression`, identifier and selector callees),
  Python via stdlib `ast` (`Name` and `Attribute` callees). Caller
  keys are `path::Symbol` for calls inside a top-level symbol,
  `path` for module-level calls. Name-level by design: no type or
  qualifier resolution (LSP stays the v4 upgrade path).
* `SymbolIndex.references` (name -> caller keys, deduped, capped at
  20 per name) is populated by `build_symbol_index` in the same file
  loop and refreshed by `reindex_files` for patched files.
* `SymbolIndex.callers_of(path, name)`: ambiguity-aware lookup — all
  references for a unique name, same-directory references only when
  same-name symbols exist (same-directory call sites are
  unambiguous for the definition side).
* CURRENT_ISSUES 4.2 tiebreak: `find_exact_task_symbols` prefers,
  among same-name candidates, the one with more same-directory call
  sites (after the task-text path mention, before index order).
* Focused repo map annotation: task symbols are annotated with their
  callers (`<- called by: Name (path)`, max 3), giving MODIFY/DEBUG
  prompts impact awareness without loading caller code (reference
  lines are cheap; fragments stay one `read_symbol` away).

Out of scope: LSP definitions/references (v4), a persistent graph,
callee expansion, and automatic caller-code loading (context budget
discipline).

Regression tests: `tests/test_v320_references.py` (Python and Go
extraction, aggregate dedupe/cap, index population, reindex refresh,
ambiguity-aware callers, disambiguation tiebreak with and without
path mention, map annotation, integration through a MODIFY run).

### v3.2.1 — Insertion-Cluster Anchoring (CURRENT_ISSUES 8.3)

Status: implemented (HARNESS_VERSION v3.2.1).

Motivation:

The removal-anchored fallback aborted entirely when a hunk contained
an insertion-only cluster (pure + lines). Measurement first: git
apply -C1 already rescues most pure-insertion hunks via context
reduction and offset search, so the anchor's residual value is hunks
git rejects atomically — a hallucinated context line breaks the whole
hunk while individual clusters are sound, a shape the 14B's narrative
diffs produce.

Changes:

* `_anchor_apply`: insertion-only clusters anchor on their preceding
  context line when it matches exactly one whitespace-insensitive
  file position; the additions insert right after it. No context
  line, or an ambiguous one, fails loudly (a wrong-position insert is
  worse than a loud failure). Removal clusters keep their existing
  removal-anchored logic; mixed hunks apply cluster by cluster.

Regression tests: `tests/test_v321_hardening.py` (git-rejects sanity
check, mixed-cluster apply, no-context loud failure, unique and
whitespace-flexible leading anchors, ambiguous refusal).

### v3.2.2 — Graded Benchmark Suite (tooling, no harness bump)

Status: implemented. No harness behavior change; runs.jsonl records
harness version + git commit, and the commit attributes the tooling
change.

Motivation:

Three harness versions were measured against a single MODIFY task.
One task is a transcript, not a benchmark (AGENTS.md benchmark
discipline). The observed 7B failure mode (message-string swaps that
never touch control flow) is exactly the kind of signal a graded
suite resolves into per-difficulty data.

Changes:

* `experiments/baseline_modify.py` now runs a three-task suite
  against a fresh repo copy per run:

  | id   | difficulty | required change |
  |------|------------|-----------------|
  | msg  | easy       | string change in an issue message (the live-test task) |
  | mid  | medium     | severity semantics: the empty-rules issue goes warning -> error |
  | hard | hard       | control flow: empty name produces exactly one issue |

* The `mid` test (`TestValidateEmptyRulesIsError`) is deterministically
  verified: it fails on the pristine repo and passes with its one-line
  severity fix.
* Per-run output carries the task id; a per-(model, task) solve
  summary closes each sweep. `runs.jsonl` records the full task text,
  so records are groupable without new fields.

The post-v3.2.2 sweep table lives below, after the v3.1.8/v3.2.x
tables.

### v3.2.2 — Own-File Reference Exclusion (HARNESS_VERSION v3.2.2)

Status: implemented. (The graded-suite tooling above also carries the
v3.2.2 label but explicitly bumped no harness version; this section is
the first harness behavior change after v3.2.1.)

Motivation:

The pre-sweep deterministic selector check for the H1/H2 experiments
(run in the exact sweep condition: the amb task's test file in place)
caught the 4.2 tiebreak selecting the WRONG same-name symbol. Task
text without a path mention: three `Validate` definitions exist
(engine, HTTP handler, config); the tiebreak picked the handler's.

Root cause: `same_dir_callers` counted references from the candidate's
own file as its callers. The handler file references the name
`Validate` twice — `Apply` calls the ENGINE's validator
(`h.Validator.Validate(...)`), and route registration references the
method value — outscoring the engine's single genuine same-directory
caller (the task's test file, cross-file). A reference from a
definition's own file is not evidence of being called: it frequently
calls a namesake defined elsewhere.

Changes:

* `SymbolIndex.same_dir_callers` counts only cross-file
  same-directory references (`caller_file != path`). Affects the 4.2
  tiebreak and ambiguous-name map annotations (a same-file "caller"
  annotation could be false for the same reason).
* Selector check tooling: `experiments/selector_check.py` reproduces
  the sweep condition and prints the selection for three task
  variants (no path, explicit engine path, explicit handler path);
  run in every checkout before a sweep.

Regression tests: `TestOwnFileReferencesExcluded` (the defect shape:
own-file namesake references must not win) and `TestRealRepoSelector`
(the three real `Validate` variants in the sweep condition) in
`tests/test_v320_references.py`.

Verified selector behavior after the fix (sweep condition):

| variant         | selected |
|-----------------|----------|
| no path         | engine `Validate` (via the test's same-dir call) |
| engine path     | engine `Validate` (path mention wins) |
| handler path    | handler `Validate` (no arbitrary redirect) |

### v3.2 Verified Baseline (closure)

Status: closed on the v3.2.1 harness + v3.2.2 suite tooling.

P0 — dependencies and full suite:

* `pip install -e ".[dev]"` against `pyproject.toml` (httpx, pydantic,
  tree-sitter, tree-sitter-go; pytest dev extra).
* Fake-model suite: 488 passed, 1 skipped (the skip is the live
  ollama test without `RUN_OLLAMA_TESTS=1` — the only optional test).
* Live test (`RUN_OLLAMA_TESTS=1`): pass on the closing commits.
* Attribution: every `runs.jsonl` record carries `harness_version` +
  `git_commit` + `model` + full task text.

P0 — real-model experiments (3 runs per model per task, temperature 0,
same repo/test command/budgets), v3.2.1 harness:

| Model            | msg (easy) | mid (medium) | hard (hard) |
|------------------|------------|--------------|-------------|
| qwen2.5-coder:7b | 3/3        | 0/3          | 0/3         |
| qwen2.5-coder:14b| 3/3        | 3/3          | 0/3         |

P1 — v3.1.9 vs v3.2.1 (same tasks, models, budgets; v3.1.9 run from a
git worktree at `92cd9d1` with the same suite runner):

| Model | Task | v3.1.9 | v3.2.1 | Notes |
|-------|------|--------|--------|-------|
| 7b    | msg  | 2/3    | 3/3    | within session variance |
| 7b    | mid  | 0/3    | 0/3    | different stop reasons, same outcome |
| 7b    | hard | 0/3    | 0/3    | identical: test_failure, 5 calls, 3 patches |
| 14b   | msg  | 3/3    | 3/3    | identical medians: 3 calls, ~5.7k prompt tok |
| 14b   | mid  | 3/3    | 3/3    | identical medians: 3 calls, 77s |
| 14b   | hard | 0/3    | 0/3    | 14B occasionally unblocks (1 test_failure run in the v3.2.1 sweep), still 0/3 |

Honest reading: the v3.2.0/v3.2.1 features (reference index,
insertion-cluster anchoring) produced no measurable solve-rate or
efficiency change on this suite — these tasks have unambiguous symbols
and git-applicable patches, so the new machinery is not exercised. No
regression either: identical efficiency on solved tasks, identical
selection. The features remain infrastructure for the cases they were
built for (same-name ambiguity, broken hunks).

P1 — symbol-selection accuracy (strict ground truth: the target
symbol plus the task's test symbol; everything else selected is a
distractor; `experiments/analyze_runs.py` over `runs.jsonl` + traces):

* Recall: 1.00 on every task, model, and version — the deterministic
  auto-load always puts the relevant symbols in context; no run ever
  had to hunt for the target.
* Precision: 0.67 on nearly every group. The persistent "distractor"
  is `Validator` (the struct, L23-L23): the task text mentions
  "validator.go", the path word "validator" exactly matches the struct
  name, so it is auto-loaded. Benign one-line over-selection, uniform
  across versions. The only lower value is 7b/mid on v3.2.1 (0.40:
  the model additionally read `validateRule` and `ValidationIssue`
  while failing to plan).
* Model-initiated distractor reads correlate with failure: solvers
  read nothing beyond the auto-load; the 7B's failing mid runs read
  two extra symbols and never produced a patch.

P1 — efficiency (medians per solved run, v3.2.1):

| Model | Task | LLM calls | Prompt tok | Completion tok | Elapsed |
|-------|------|-----------|------------|----------------|---------|
| 7b    | msg  | 5-6       | ~13.2k     | ~344           | ~120s   |
| 14b   | msg  | 3         | ~5.7k      | ~479           | ~164s   |
| 14b   | mid  | 3         | ~5.8k      | ~279           | ~77s    |

Failed runs cost their full budget: 7b mid/hard exhaust 5-8 calls
(~11-25k prompt tokens); 14b hard exhausts 8 calls (~14.5k prompt
tokens, ~500s) producing narrative diffs that never converge.

Capability gradient (the suite's purpose): easy string changes are
solved by both models; severity semantics are solved by the 14B in
the minimum 3 calls while the 7B dies exploring (8 calls, no patch);
control-flow restructuring is solved by neither. This is the baseline
that future harness changes (v9 reviewer, v8 compaction) must beat.

## Current Known Issues

Open problems, limitations, and open questions are tracked in
`CURRENT_ISSUES.md`, not inline in this roadmap.

One item shapes the priority of the planned phases below:

* **Targeted verification is pytest-shaped** (CURRENT_ISSUES 2.1, 2.2): the
  v6 Targeted Verification phase must generalize beyond `test_*.py` naming and
  pytest-based commands.

Unverified patches reaching DONE (former CURRENT_ISSUES 1.2) was resolved in
v3.1.3: a patch that cannot be verified now ends FAILED with
`stop_reason="unverified_patch"`, so MODIFY success rates are trustworthy
without a test command.

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

Status: core loop implemented deterministically in v3.1.8 (parse
diagnostic -> map to symbol -> auto-load repair context, no LSP). What
remains for this phase: LSP-backed definitions/references (v4+) and
the context-compaction half ("do not resend the entire working
context"), which moves to v8.

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
