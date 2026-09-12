# Pipeline orchestration

This document describes the v2 orchestration state machine as implemented. Ground truth is the code:

- `src/sar/orchestrator/machine.py` - the state machine (`PipelineMachine`)
- `src/sar/orchestrator/runner.py` - the driver loop (`run_pipeline`)
- `src/sar/orchestrator/gates.py` - gate functions
- `src/sar/contracts/pipeline.py` - phases, state, and result contracts

Every claim below carries a file:line reference. Line numbers refer to the files at the time of writing.

---

## Table of contents

- [Overview](#overview)
- [Main pipeline procedure](#main-pipeline-procedure)
- [Transition table](#transition-table)
- [Gates](#gates)
- [State variables](#state-variables)
- [Decision logic](#decision-logic)
- [Error handling](#error-handling)
- [Appendix: known divergences and planned repairs](#appendix-known-divergences-and-planned-repairs)

---

## Overview

The pipeline is an explicit state machine with 8 phases, defined by the `PipelinePhase` enum (`contracts/pipeline.py:20-34`):

| Phase | Value | Role |
|-------|-------|------|
| `EXPAND` | `"expand"` | Generate search queries from the user question |
| `SEARCH` | `"search"` | Run every query through the search engine |
| `JUDGE` | `"judge"` | Filter results by source quality |
| `READ` | `"read"` | Fetch content and extract structured summaries |
| `ANALYZE` | `"analyze"` | Synthesize the report |
| `REVIEW` | `"review"` | Evaluate the report and route the next step |
| `COMPLETE` | `"complete"` | Terminal: success |
| `FAILED` | `"failed"` | Terminal: unrecoverable failure |

Terminal phases are exactly `{COMPLETE, FAILED}` (`machine.py:85-91`). Attempting `transition()` from a terminal phase raises `InvalidTransitionError` (`machine.py:113-115`).

```
        +--------------------------------------------------------------+
        |                                                              |
        v                                                              |
  [EXPAND] --> [SEARCH] --> [JUDGE] --> [READ] --> [ANALYZE] --> [REVIEW]
      ^                                                ^           |
      |                                                |           +--> APPROVED / final round --> [COMPLETE]
      |                                                |           |
      |                             NEEDS_REWORK ------+           |
      |                                                            |
      +------------------------------- NEEDS_MORE_INFO ------------+

  Any phase --> [FAILED] when its gate fails or the budget gate trips.
```

Two transition mechanisms exist:

1. **Gated transitions** (`machine.py:93-163`). `PipelineMachine.transition()` looks up the current phase in the transition table, evaluates candidate gates in order, and takes the first transition whose gate passes (`machine.py:126-155`). Every gate decision is appended to `state.gate_log` (`machine.py:138`). If all candidate gates fail, the machine moves to `FAILED` (`machine.py:157-163`).
2. **Forced transitions** (`machine.py:165-180`). `force_phase()` sets the phase directly, bypassing gates. The runner uses it for all REVIEW routing and for the budget abort.

The linear phases (EXPAND through ANALYZE) each use mechanism 1. REVIEW uses mechanism 2 exclusively: it has no entry in the transition table (`runner.py:109`, docstring at `runner.py:71-74`) and the runner routes it by reviewer verdict (`runner.py:345-411`).

---

## Main pipeline procedure

Pseudocode mirroring `run_pipeline()` (`runner.py:113-433`):

```pseudo
PROCEDURE run_pipeline(query, config, search_engine, llm, audit_log, event_bus):

  // INITIALIZATION (runner.py:139-147)
  max_iterations = config.MAX_ITERATIONS        (default 3, runner.py:44,139)
  max_read       = config.MAX_READ_PER_ITERATION (default 10, runner.py:47,140)
  state   = PipelineState()                      // phase=EXPAND, iteration=0
  machine = PipelineMachine(state, build_transitions(config))
  audit_log.record("pipeline_start", ...)

  // MAIN LOOP (runner.py:149)
  WHILE machine is not terminal:
    phase = machine.current_phase

    // Cross-cutting budget check at every loop head (runner.py:152-164)
    IF NOT check_budget_remaining(state).passed:
      log gate decision; force_phase(FAILED); BREAK

    SWITCH phase:

      CASE EXPAND:                              // runner.py:169-188
        state.queries = query_expander.run(query)   // fallback: [query] on error
        state.iteration += 1                        // ONLY place iteration advances (runner.py:182)
        machine.transition()                        // gate: queries_generated

      CASE SEARCH:                              // runner.py:193-222
        state.results = search all state.queries, extend, deduplicate
        machine.transition()                        // gate: results_obtained

      CASE JUDGE:                               // runner.py:227-245
        state.results = source_judge.run(state.results)  // on error: keep unfiltered
        machine.transition()                        // gate: quality_threshold

      CASE READ:                                // runner.py:250-312
        top = state.results[:max_read]              // runner.py:252
        state.summaries = reader.run_batch(top) -> PaperSummary list
        machine.transition()                        // gate: summaries_ready

      CASE ANALYZE:                             // runner.py:317-340
        state.report = analyzer.run(query, state.summaries, feedback=state.feedback)
        machine.transition()                        // gate: report_submitted

      CASE REVIEW:                              // runner.py:345-411
        is_final = NOT check_max_iterations(state, max_iterations).passed
        (verdict_cmd, payload) = reviewer.run(query, state.report, is_final_round=is_final)
                                                    // on error: verdict_cmd="complete"
        verdict = _parse_verdict(verdict_cmd)
        append IterationRecord to state.iterations  // runner.py:368-380
        route by verdict (see Decision logic)       // runner.py:390-411

      DEFAULT:                                  // runner.py:413-416
        force_phase(FAILED)

  // FINAL RESULT (runner.py:418-433)
  RETURN PipelineResult(
    success = (state.phase == COMPLETE),
    report, summaries, iterations_used, gate_log, budget)
```

---

## Transition table

Built by `build_transitions()` (`runner.py:64-110`). Each of the five linear phases has exactly one `(target, gate)` candidate:

| Source | Target | Gate | Config knob | Reference |
|--------|--------|------|-------------|-----------|
| `EXPAND` | `SEARCH` | `check_queries_generated` | none | `runner.py:85-87` |
| `SEARCH` | `JUDGE` | `check_results_obtained` | `MIN_RESULTS_TO_PROCEED` (default 1, `runner.py:45,81`) | `runner.py:88-93` |
| `JUDGE` | `READ` | `check_quality_threshold` | `QUALITY_THRESHOLD` (default 4.0, `runner.py:46,82`) | `runner.py:94-102` |
| `READ` | `ANALYZE` | `check_summaries_ready` | none | `runner.py:103-105` |
| `ANALYZE` | `REVIEW` | `check_report_submitted` | none | `runner.py:106-108` |

`REVIEW` is deliberately absent from the table (`runner.py:109`). Its routing is runner-driven via `machine.force_phase()` (`machine.py:165-180`), because the next phase depends on the reviewer verdict, not on a state predicate.

Because each linear phase has a single candidate, a failed gate means "all gates failed" and the machine moves to `FAILED` (`machine.py:157-163`).

---

## Gates

Ten gate functions live in `gates.py`. All are pure: they inspect state and return a `GateDecision` (`contracts/pipeline.py:83-101`) without mutating anything (`gates.py:7-8`).

| Gate | Location | Checks | Called from |
|------|----------|--------|-------------|
| `check_queries_generated` | `gates.py:32` | `len(state.queries) > 0` | transition table (EXPAND -> SEARCH) |
| `check_results_obtained` | `gates.py:51` | `len(state.results) >= min_results` | transition table (SEARCH -> JUDGE) |
| `check_quality_threshold` | `gates.py:77` | at least `min_kept` results with `relevance_score >= min_score`; unscored results count as passing (`gates.py:97-105`) | transition table (JUDGE -> READ) |
| `check_summaries_ready` | `gates.py:125` | `len(state.summaries) > 0` | transition table (READ -> ANALYZE) |
| `check_report_submitted` | `gates.py:144` | `state.report` is non-empty after strip | transition table (ANALYZE -> REVIEW) |
| `check_reviewer_approved` | `gates.py:167` | latest recorded `reviewer_verdict == APPROVED` | **not called by the runner** |
| `check_needs_rework` | `gates.py:192` | latest recorded `reviewer_verdict == NEEDS_REWORK` | **not called by the runner** |
| `check_needs_more_info` | `gates.py:215` | latest verdict `NEEDS_MORE_INFO` and `state.iteration < max_iterations` | **not called by the runner** |
| `check_budget_remaining` | `gates.py:260` | `state.budget.is_over_budget` is false (`pipeline.py:73-80`) | runner, at every loop head (`runner.py:153`) |
| `check_max_iterations` | `gates.py:291` | `state.iteration < max_iterations` | runner, at REVIEW entry (`runner.py:346`) |

The three reviewer-outcome gates (`check_reviewer_approved`, `check_needs_rework`, `check_needs_more_info`) exist for future wiring and tests; the runner currently routes REVIEW by parsing the verdict directly (`runner.py:390-411`) and never calls them. They also could not work today, because they read `IterationRecord.reviewer_verdict` via `_latest_verdict` (`gates.py:321-332`) and that field is never populated (see appendix, item a).

---

## State variables

`PipelineState` (`contracts/pipeline.py:151-179`) is the single mutable state object. The orchestrator reads and writes it; agents receive read-only slices (`pipeline.py:154-155`).

| Field | Type | Default | Mutated by |
|-------|------|---------|------------|
| `phase` | `PipelinePhase` | `EXPAND` | `machine.transition()` (`machine.py:134,152`), all-gates-failed path (`machine.py:162`), `machine.force_phase()` (`machine.py:180`) |
| `iteration` | `int` | `0` | runner, EXPAND phase only: `state.iteration += 1` (`runner.py:182`). No other site increments it. |
| `queries` | `list[str]` | `[]` | EXPAND (`runner.py:171`, fallback `runner.py:180`); REVIEW overwrites when a NEEDS_MORE_INFO payload is a non-empty list (`runner.py:400-401`) |
| `results` | `list[SearchResult]` | `[]` | SEARCH (`runner.py:211`, error fallback `runner.py:216`); JUDGE replaces with the filtered list (`runner.py:229`) |
| `summaries` | `list[PaperSummary]` | `[]` | READ (`runner.py:260,293`, error fallback `runner.py:306`) |
| `report` | `str \| None` | `None` | ANALYZE (`runner.py:327-329`, error fallback `runner.py:334`) |
| `feedback` | `str \| None` | `None` | REVIEW, on NEEDS_REWORK (`runner.py:394`) and NEEDS_MORE_INFO (`runner.py:398`). It is read by ANALYZE (`runner.py:323`) and is never reset to `None` afterward; no line in `runner.py` clears it. |
| `gate_log` | `list[GateDecision]` | `[]` | `machine.transition()` appends every evaluated gate (`machine.py:138`); the runner appends the failed budget gate (`runner.py:155`) |
| `budget` | `TokenBudget` | `TokenBudget()` (`pipeline.py:178`) | nothing. No code in the orchestrator or LLM client writes to it (see appendix, item c). |
| `iterations` | `list[IterationRecord]` | `[]` | REVIEW appends one record per review (`runner.py:380`) |

Note on `iteration`: it counts passes through EXPAND, not passes through the loop. A `NEEDS_REWORK` cycle (REVIEW -> ANALYZE -> REVIEW) does not touch EXPAND and therefore does not advance it (see appendix, item b).

---

## Decision logic

### The reviewer loop (`runner.py:345-411`)

1. **Final-round detection** (`runner.py:346-347`): `check_max_iterations(state, max_iterations)` is evaluated at REVIEW entry; `is_final = not gate.passed`, i.e. the round is final once `state.iteration >= max_iterations` (`gates.py:303`).
2. **Toolset restriction**: `is_final_round` is passed to the reviewer agent (`runner.py:354`), which builds its toolset with `get_reviewer_tools(is_final_round)` (`agents/reviewer.py:122`). On the final round the `request_more_info` tool is omitted; only `mark_as_complete` and `request_rework` remain (`llm/tools.py:401-404`).
3. **Reviewer call** (`runner.py:350-356`): returns `(verdict_cmd, payload)`. On exception, the runner records the error and substitutes `verdict_cmd = "complete"`, `payload = None` (`runner.py:357-362`), accepting the current report.
4. **Verdict parsing**: `_parse_verdict()` (`runner.py:555-588`) maps the command string to a `ReviewerVerdict` (`pipeline.py:37-50`):

| Input | Verdict | Reference |
|-------|---------|-----------|
| `None` | `APPROVED` | `runner.py:566-567` |
| `"mark_as_complete"`, `"complete"` | `APPROVED` | `runner.py:573-574` |
| `"request_rework"`, `"needs_rework"` | `NEEDS_REWORK` | `runner.py:575-576` |
| `"request_more_info"`, `"needs_more_info"` | `NEEDS_MORE_INFO` | `runner.py:577-578` |
| any exact enum value (e.g. `"approved"`, `"failed"`) | that verdict | `runner.py:583-585` |
| anything else | `APPROVED` (with warning) | `runner.py:587-588` |

5. **Iteration record** (`runner.py:368-380`): an `IterationRecord` is appended with the iteration number, phase, queries, counts, this iteration's gate decisions, and the feedback payload. The record's `reviewer_verdict` field is not passed (see appendix, item a).
6. **Routing** (`runner.py:390-411`), all via `force_phase()`:

| Verdict | Condition | Action |
|---------|-----------|--------|
| `APPROVED` | always | force `COMPLETE` (`runner.py:390-391`) |
| `NEEDS_REWORK` | not final round | `state.feedback = payload` (if str), force `ANALYZE` (`runner.py:393-395`) |
| `NEEDS_MORE_INFO` | not final round | `state.feedback = payload` (if str); if payload is a non-empty list, `state.queries = payload`; force `EXPAND` (`runner.py:397-402`) |
| anything else (final round, or verdict `FAILED`) | fallthrough | force `COMPLETE`, accepting the current report (`runner.py:404-411`) |

### Reviewer decision tree

```
                     +--------------------+
                     | Evaluate report    |
                     | (reviewer agent)   |
                     +---------+----------+
                               |
          +--------------------+--------------------+
          v                    v                    v
   mark_as_complete      request_rework       request_more_info
          |              (feedback text)      (feedback / queries)
          v                    |                    |
      APPROVED           NEEDS_REWORK         NEEDS_MORE_INFO
          |                    |                    |
          v              final? yes -> COMPLETE     |
      COMPLETE           final? no  -> ANALYZE   final? yes -> COMPLETE
                          (with state.feedback)  final? no  -> EXPAND
                                                  (feedback, optional
                                                   query overwrite)
```

### Final round behavior

When `state.iteration >= max_iterations` at REVIEW entry (`runner.py:346-347`, `gates.py:303`):

- `request_more_info` is removed from the reviewer's toolset (`llm/tools.py:401-404` via `agents/reviewer.py:122`).
- Whatever the verdict, routing ends in `COMPLETE`: `APPROVED` hits the first branch (`runner.py:390-391`), and `NEEDS_REWORK` / `NEEDS_MORE_INFO` fail their `not is_final` conditions (`runner.py:393,397`) and fall to the else branch, which accepts the current report (`runner.py:404-411`).
- There is no post-loop "best effort" return path as in v1; acceptance happens inside the REVIEW branch itself, and the loop then exits because `COMPLETE` is terminal (`runner.py:149`, `machine.py:85-91`).

---

## Error handling

### Per-phase degradation

Each phase body wraps its agent call in try/except, records the failure with `audit_log.record_error(component, message, context)`, and applies a phase-specific fallback:

| Phase | Error record | Fallback | Downstream effect |
|-------|--------------|----------|-------------------|
| EXPAND | `record_error("query_expander", ...)` (`runner.py:179`) | `state.queries = [query]` (`runner.py:180`) | pipeline proceeds with the original query; `queries_generated` gate passes |
| SEARCH | `record_error("search", ...)` (`runner.py:215`) | `state.results = []` (`runner.py:216`) | `results_obtained` gate fails -> machine moves to `FAILED` (`machine.py:157-163`) |
| JUDGE | `record_error("source_judge", ...)` (`runner.py:238`) | keep unfiltered results (`runner.py:239`, comment) | pipeline proceeds with unjudged results; unscored results pass `quality_threshold` by default (`gates.py:97-105`) |
| READ | `record_error("reader", ...)` (`runner.py:305`) | `state.summaries = []` (`runner.py:306`) | `summaries_ready` gate fails -> `FAILED` |
| ANALYZE | `record_error("analyzer", ...)` (`runner.py:333`) | `state.report = ""` (`runner.py:334`) | `report_submitted` gate fails -> `FAILED` |
| REVIEW | `record_error("reviewer", ...)` (`runner.py:359`) | `verdict_cmd = "complete"` (`runner.py:361-362`) | forced `COMPLETE`; the current report is accepted |

### Machine-level failure paths

- **Gate failure**: with single-candidate transitions, one failed gate exhausts the candidate list and the machine moves to `FAILED` (`machine.py:157-163`).
- **Budget abort**: `check_budget_remaining` runs at every loop head (`runner.py:152-164`); on failure the decision is logged, `record_gate_decision` is written to the audit log, and the machine is forced to `FAILED` before any phase body runs. (Currently unreachable; see appendix, item c.)
- **Unknown phase**: the defensive else branch forces `FAILED` (`runner.py:413-416`).
- **Terminal misuse**: calling `transition()` from `COMPLETE` or `FAILED`, or from a phase with no table entry (e.g. `REVIEW`), raises `InvalidTransitionError` (`machine.py:113-122`).

### Result envelope

Success is defined as ending in `COMPLETE`: `PipelineResult.success = (state.phase == PipelinePhase.COMPLETE)` (`runner.py:427`). A `FAILED` run still returns a `PipelineResult` carrying whatever report, summaries, gate log, and budget the state accumulated (`runner.py:426-433`, `pipeline.py:182-203`).

---

## Appendix: known divergences and planned repairs

These are verified behaviors of the current code that diverge from the design intent. Each is repair scheduled in the hardening stage.

**(a) `IterationRecord` is built without `reviewer_verdict`, so `_latest_verdict` cannot see verdicts.** The record constructed at `runner.py:368-379` passes `iteration`, `phase`, `queries`, `results_count`, `summaries_count`, `gate_decisions`, and `feedback`, but not `reviewer_verdict`, which therefore stays at its `None` default (`pipeline.py:148`). `_latest_verdict` (`gates.py:321-332`) reads exactly that field, so `check_reviewer_approved` (`gates.py:167`), `check_needs_rework` (`gates.py:192`), and `check_needs_more_info` (`gates.py:215`) can never observe a real verdict. The runner does not currently call these gates, so runtime routing is unaffected, but any future wiring of them would silently misroute. Repair scheduled.

**(b) The NEEDS_REWORK ANALYZE <-> REVIEW cycle has no independent bound.** `state.iteration` advances only in the EXPAND body (`runner.py:182`). A `NEEDS_REWORK` verdict forces `ANALYZE` (`runner.py:395`), which transitions back to `REVIEW` without passing through EXPAND, so `check_max_iterations` at REVIEW entry (`runner.py:346`, `gates.py:303`) evaluates the same unchanged count every cycle. If the reviewer keeps returning `NEEDS_REWORK` before the iteration limit is reached, nothing bounds the cycle except the budget gate, which cannot trip (item c). Repair scheduled.

**(c) `BudgetTracker` is never constructed, so `check_budget_remaining` can never trip.** The tracker class exists (`llm/budget.py:19`) and is exported (`llm/__init__.py:14,60`), but no code in `src/` instantiates it. `state.budget` is a plain `TokenBudget` created by default factory (`pipeline.py:178`) that nothing ever updates, so `cost_usd` stays `0.0` against a `5.0` ceiling (`pipeline.py:67-71`) and `is_over_budget` (`pipeline.py:73-80`) is always false. The loop-head check at `runner.py:152-164` therefore always passes, and the budget abort path is dead code. Repair scheduled.
