# CURRENT_ISSUES.md

Known current problems, limitations, and open questions for the harness.

This file is a working catalog. Items are removed when resolved and marked as
covered by a regression test. See `ROADMAP.md` for future architecture and
`AGENTS.md` for the design principles the issues are judged against.

Last updated against: HARNESS_VERSION `v3.1.6`.

---

## 1. Correctness

### 1.1 `extract_json_object` merges first-to-last brace block

`mini_agent/model.py:65` locates JSON via `text.find("{")` / `text.rfind("}")`.
If a model emits prose plus a JSON object, or two objects, the merged text is
parsed. The common failure mode is `json.JSONDecodeError`, which is already
handled as a structured invalid-output failure, but the recovery depends on the
malformed text actually failing to parse. A pathological output that merges
into valid JSON would be accepted silently.

- Severity: medium.
- Residual risk contained by `validate_action_object` (`model.py:84`) and the
  bounded invalid-output recovery in the orchestrator.

### 1.2 Patch verification depends on fragile output sniffing

Success is detected by substring `"EXIT_CODE=0"` in the captured test output
(`_run_verification`, `orchestrator.py`). A test command that prints
`EXIT_CODE=1` as part of its own output would be misread.

- Severity: low.

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

### 3.1 Alternating invalid outputs evade the 3x repeat bound

Bounded recovery triggers only on three *identical* invalid outputs
(`orchestrator.py:622`). A model that oscillates between two different malformed
outputs consumes the LLM-call budget without tripping the warning. Bounded by
budgets, but wasteful.

### 3.2 No dedicated invalid-output budget

Invalid outputs count against the general `llm_calls` budget
(`rs.llm_calls += 1` occurs before the call, `orchestrator.py:505`) but there is
no per-run limit specific to malformed output, so the signal is diluted.

## 4. Context selection

### 4.1 Exact-symbol auto-load resolves only one symbol

Resolved in v3.1.6: `find_exact_task_symbols` resolves every task word
that exactly matches an indexed symbol (longest first, capped at 3), and
deterministic pre-loads no longer consume the model's read-action
budget. Regression tests in `tests/test_symbols.py`,
`tests/test_observability.py`, and `tests/test_v316_hardening.py`.

### 4.2 Ambiguous same-name symbols not disambiguated

Partially addressed in v3.1.6: same-name candidates prefer the one whose
file is named in the task text, so "Corrige Validate en
internal/engine/validator.go" resolves the engine's `Validate`. Without
a path mention, the first index entry still wins; caller/import signals
remain v4/v5 LSP and impact-graph territory (`ROADMAP.md`).

### 4.3 Repository map is still repository-wide

`build_repo_map` prioritizes files by task tokens but is not aggressively
narrowed for localized MODIFY/DEBUG tasks. For a tiny localized change the
model still receives a full-map view plus the auto-loaded symbol.

### 4.4 Long-run bookkeeping lists are not bounded

`RunState.repeated` (`orchestrator.py:184`) appends every action string for the
whole run; `WorkingContext.facts` is capped at 12 but `repeated` is unbounded.
Trivial memory, but the 3x check only needs the last three entries.

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
and is the comparison baseline for future harness changes.

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
  (`ALLOWED_ACTIONS`, `state.py:60`). `run_tests` is never advertised in prompts
  because test execution is deterministic (`deterministic before agentic`).

## Severity summary

| Area | Low | Medium | High |
|------|-----|--------|------|
| Correctness        | 1.2        | 1.1 | — |
| Verification       | —          | 2 (non-pytest/Go frameworks) | — |
| Invalid output     | 3.2        | 3.1       | — |
| Context selection  | 4.4        | 4.2, 4.3 | — |
| Versioning/exp     | 6.3        | —       | — |

High-severity items: none currently open. The most impactful open items are
**4.2/4.3** (residual symbol disambiguation and repo-map narrowing) and
**2** (targeted verification beyond pytest and Go).