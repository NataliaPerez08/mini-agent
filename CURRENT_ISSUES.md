# CURRENT_ISSUES.md

Known current problems, limitations, and open questions for the harness.

This file is a working catalog. Items are removed when resolved and marked as
covered by a regression test. See `ROADMAP.md` for future architecture and
`AGENTS.md` for the design principles the issues are judged against.

Last updated against: HARNESS_VERSION `v3.1.7`.

---

## 1. Correctness

Resolved in v3.1.7 (regression tests in `tests/test_v317_hardening.py`):

- **1.1 JSON brace merging**: `extract_json_object` now returns the first
  *balanced* object (string-aware scanner) instead of merging the first
  brace to the last. Prose with trailing braces, two objects, and braces
  inside string values are all handled.
- **1.2 EXIT_CODE sniffing**: verification reads the `EXIT_CODE` marker
  from the first output line only; a test binary printing `EXIT_CODE=0`
  in its own output can no longer be misread as passing.

Former 1.2 (successful patch reported without test evidence) was resolved in
v3.1.3: with an empty `test_command` the run now ends `FAILED` with
`stop_reason="unverified_patch"` and `tests_run == 0`. Regression tests in
`tests/test_unverified_patch.py`.

## 2. Verification coverage

Resolved in v3.1.6 for Go (regression tests in `tests/test_tools.py`):

- `_find_related_tests` matches Go's same-directory `foo.go` ->
  `foo_test.go` convention, and `run_targeted_tests` builds
  package-targeted `go test ./<pkg>/` for go-flavored commands (walking
  to the nearest `go.mod`, falling back to the full command across
  multiple modules).
- Remaining gap: frameworks other than pytest and Go (e.g. Jest, cargo
  test) still fall back to the full configured command. Correct but not
  cheap; revisit with real tasks in those languages.

## 3. Invalid-output handling

Resolved in v3.1.7 (regression tests in `tests/test_v317_hardening.py` and
`tests/test_v311_hardening.py`):

- **3.1 alternating invalid outputs**: a `consecutive_invalid` counter
  fails the run deterministically after `MAX_CONSECUTIVE_INVALID = 3`
  malformed outputs in a row (`stop_reason="invalid_output"`), so
  alternating outputs can no longer evade the old 3x-identical soft
  bound. The superseded soft-bound branch was removed.
- **3.2 dedicated budget**: the consecutive-invalid limit is its own
  control-flow budget (a `budget_exhausted` trace event fires with
  `budget="consecutive_invalid"`), not a silent consumer of the general
  LLM-call budget.

## 4. Context selection

### 4.1 Exact-symbol auto-load resolves only one symbol

Resolved in v3.1.6: `find_exact_task_symbols` resolves every task word
that exactly matches an indexed symbol (longest first, capped at 3), and
deterministic pre-loads no longer consume the model's read-action
budget. Regression tests in `tests/test_symbols.py`,
`tests/test_observability.py`, and `tests/test_v316_hardening.py`.

### 4.2 Ambiguous same-name symbols not disambiguated

Resolved in v3.2.0 (heuristically): a name-level reference index
(v5-lite, `mini_agent/references.py`) records call sites per file
during symbol indexing. Same-name candidates prefer, in order: a file
named in the task text (v3.1.6), then the candidate with more
same-directory call sites, then index order. The focused repo map
annotates task symbols with their callers (`<- called by:`), showing
all references for unique names and same-directory references only
for ambiguous ones. This is name-level matching without type or
qualifier resolution — true reference resolution remains v4 (LSP)
territory. Regression tests in `tests/test_v320_references.py`.

### 4.3 Repository map is still repository-wide

Resolved in v3.1.9: for localized MODIFY/DEBUG runs (exact-symbol
auto-load resolved a target), the map is focused — the auto-loaded
symbol's file and directory lead every section, and the SYMBOLS
section is capped at 10 files instead of 60. Runs without a resolved
target keep the repository-wide prioritized map for discovery, and
INSPECT is unchanged. The map is built after the auto-load in setup,
and the post-patch rebuild keeps the focus (auto symbols + patched
files). Regression tests in `tests/test_v319_hardening.py`.

### 4.4 Long-run bookkeeping lists are not bounded

Resolved in v3.1.7: `RunState.record_repeat` keeps only the last three
entries. Regression test in `tests/test_v317_hardening.py`.

### 4.5 Repair context ignores failure diagnostics

Resolved in v3.1.8: on verification failure the harness parses
`file:line` pairs from the test output (go test, go compile, pytest),
maps them to symbols via `SymbolIndex.at_line`, and auto-loads up to 3
not-yet-loaded symbols into the repair context before entering REPAIR
(`auto_symbol_read` with `reason="test_failure_diagnostic"`; no
read-budget consumption). Regression tests in
`tests/test_v318_hardening.py`.

## 5. Observability

Resolved in v3.1.2 (regression tests in `tests/test_observability.py`):

- Deterministic pre-loads now emit dedicated trace events (`auto_read` for the
  INSPECT fast path, `auto_symbol_read` for the MODIFY/DEBUG exact-symbol
  load), each with a `reason` field. The `run_start` and `symbol_index` events
  are emitted before pre-loads so a trace reconstructs ordering.
- `AgentResult`/`runs.jsonl` now carry `auto_read_files` and `auto_symbols`
  (list of `"path::Name"`), so run summaries record what was loaded
  deterministically.

## 6. Versioning and experimentation

### 6.1 Fase 4 verification change was not attributed to a version bump

Resolved: the v3.1.2 bump (introduced for the Observability work above) also
covers the `run_targeted_tests` behavior change for non-pytest configured
commands and empty `test_command`.

### 6.2 v3.1.1 exit criteria not yet validated on a real model

Resolved in v3.1.6: `experiments/baseline_modify.py` records the live
MODIFY baseline (3 runs per model, temperature 0) into
`experiments/runs.jsonl`; the post-v3.1.6 table lives in `ROADMAP.md`
and is the comparison baseline for future harness changes. Since v3.1.7
the runner carries the original hard task (`HARD_TASK`) while the live
test gates on a task within reliable 14B reach; capability tracking
stays with the baseline runner.

### 6.3 `runs.jsonl` and traces are intentionally untracked

Experiment records are gitignored by design (see `AGENTS.md`). Attribution to
harness version + commit is only derivable from the recorded fields at run time,
not from git history.

## 7. Design constraints (intentional, listed for context)

- Test commands run through `shell=True` with a user-supplied command; no
  sandbox. This matches the security principles in `AGENTS.md` (no unrestricted
  model-driven shell). `run_tests`/`run_targeted_tests` remain behind a
  configured `test_command`, never a model action.
- The model action surface stays small and state-scoped
  (`ALLOWED_ACTIONS`, `state.py`). `run_tests` is never advertised in prompts
  because test execution is deterministic (`deterministic before agentic`).
  Since v3.1.7 `finish` is legal in EDIT/REPAIR as an escape hatch: the
  patchless-finish guard still rejects the first attempt, so premature
  finishes remain bounded.

## 8. Live-model limitations (observed, not harness bugs)

- **Session variance at temperature 0**: identical prompts produce
  different completions across ollama server sessions. Single live runs
  are not reproducible evidence; use `experiments/baseline_modify.py`
  with multiple runs for capability claims.
- **Narrative diffs**: the 14B sometimes emits "before/after" hunks
  (old content as leading context, desired end state as trailing
  context) instead of a unified diff. The harness's failure feedback
  (`CURRENT CONTENT`) usually corrects it on the next attempt; the
  removal-anchored fallback absorbs the remaining mechanical defects
  (mid-hunk context gaps, wrong hunk positions, wrong counts).
- **Insertion-only hunks** are not handled by the removal-anchored
  fallback (no removal lines to anchor on); they fail loudly with
  `CURRENT CONTENT` feedback. Revisit if live runs show them.

## Severity summary

| Area | Low | Medium | High |
|------|-----|--------|------|
| Correctness        | — | — | — |
| Verification       | — | 2 (non-pytest/Go frameworks) | — |
| Invalid output     | — | — | — |
| Context selection  | — | 4.2, 4.3 | — |
| Versioning/exp     | 6.3 | — | — |

High-severity items: none currently open. The most impactful open items are
**4.2/4.3** (residual symbol disambiguation and repo-map narrowing) and
**2** (targeted verification beyond pytest and Go).
