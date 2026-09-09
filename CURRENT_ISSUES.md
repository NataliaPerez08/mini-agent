# CURRENT_ISSUES.md

Known current problems, limitations, and open questions for the harness.

This file is a working catalog. Items are removed when resolved and marked as
covered by a regression test. See `ROADMAP.md` for future architecture and
`AGENTS.md` for the design principles the issues are judged against.

Last updated against: HARNESS_VERSION `v3.1.1` (commits through `22f0849`).

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

### 1.2 Successful patch can be reported without any test evidence

When `test_command` is empty, `_run_verification` (`orchestrator.py:235`) returns
`True, "NO TESTS CONFIGURED"` without executing anything. A MODIFY task can
therefore reach `DONE` with a patch that was never verified. This matches the
documented rule ("never claim success without test evidence when tests exist"),
but the harness has no way to require evidence.

- Severity: medium.
- Open question: should unverified modifications be allowed to finish, or
  should they degrade to `DONE_WITH_UNVERIFIED_PATCH` / a warning in metrics?

### 1.3 Patch verification depends on fragile output sniffing

Success is detected by substring `"EXIT_CODE=0"` in the captured test output
(`_run_verification`, `orchestrator.py:246`). A test command that prints
`EXIT_CODE=1` as part of its own output would be misread.

- Severity: low.

## 2. Verification coverage

### 2.1 Targeted tests only match pytest file naming

`_find_related_tests` (`orchestrator.py:222`) matches `test_<stem>.py` and
`<stem>_test.py`. Go repos (`service.go` + `service_test.go`), and any project
with non-pytest names, always fall back to the full configured test command, so
the v6 "targeted verification" benefit does not apply.

### 2.2 Non-pytest frameworks lose the targeted benefit

`run_targeted_tests` (`tools.py:131`) only narrows to specific files when the
configured command is pytest-flavored. For other frameworks the full
`test_command` runs as-is (targeted file list ignored). Correct but not cheap.

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

`find_exact_task_symbol` (`symbols.py:195`) returns the single longest exact
match. Tasks that name several relevant symbols (e.g. `ApplyPolicy` *and*
`validatePorts`) pre-load just one; the rest must be requested by the model.
Same-name symbols across files resolve to index order, not task relevance.

### 4.2 Ambiguous same-name symbols not disambiguated

`SymbolIndex.by_name` / the auto-loader pick the first entry for a name. No
caller/import/package signal is used to disambiguate (that is the v4/v5 LSP and
impact-graph territory in `ROADMAP.md`).

### 4.3 Repository map is still repository-wide

`build_repo_map` prioritizes files by task tokens but is not aggressively
narrowed for localized MODIFY/DEBUG tasks. For a tiny localized change the
model still receives a full-map view plus the auto-loaded symbol.

### 4.4 Long-run bookkeeping lists are not bounded

`RunState.repeated` (`orchestrator.py:184`) appends every action string for the
whole run; `WorkingContext.facts` is capped at 12 but `repeated` is unbounded.
Trivial memory, but the 3x check only needs the last three entries.

## 5. Observability

### 5.1 Deterministic pre-loads have no dedicated trace events

Neither the INSPECT auto-reads nor the v3.1.1 auto-symbol read emit an
`_log_event`; only vprint output and the final aggregate metrics
(`symbol_reads`, `files_read`) record them. A trace cannot reconstruct what was
loaded deterministically or why.

### 5.2 Run summary lacks pre-load detail

`AgentResult`/`runs.jsonl` carry aggregates but not `auto_read_files` nor the
auto-loaded symbol name/path.

## 6. Versioning and experimentation

### 6.1 Fase 4 verification change was not attributed to a version bump

`run_targeted_tests` (commit `22f0849`) changes verification behavior for
non-pytest configured commands and for empty `test_command`. It was committed
under `v3.1.1` without a bump. Per `AGENTS.md`, behavior that affects
experiments should move `HARNESS_VERSION`. Decide: accept as a bugfix within
v3.1.1, or tag v3.1.2.

### 6.2 v3.1.1 exit criteria not yet validated on a real model

The ApplyPolicy scenario passes under `StubModel` integration tests
(`tests/test_v311_hardening.py`) but there is no recorded benchmark run of a
real 3B–14B model before/after v3.1.1 on the same repo, task, model, and test
command (`experiments/benchmark.py` exists; `experiments/runs.jsonl` is
gitignored). Benchmark discipline requires a comparable baseline before claiming
the optimization.

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
| Correctness        | 1.3        | 1.1, 1.2 | — |
| Verification       | —          | 2.1, 2.2 | — |
| Invalid output     | 3.2        | 3.1       | — |
| Context selection  | 4.4        | 4.1, 4.2, 4.3 | — |
| Observability      | —          | 5.1, 5.2 | — |
| Versioning/exp     | 6.1, 6.3   | 6.2       | — |

High-severity items: none currently open. The two most impactful open items are
**1.2** (unverified patch can reach DONE) and **4.1/4.2** (single-symbol,
unambiguous auto-load).