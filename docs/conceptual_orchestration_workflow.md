# Pipeline Orchestration Workflow

This document describes the conceptual workflow of the SAR agentic pipeline in pseudocode format.

---

## Table of Contents

- [Overview](#overview)
- [Main Pipeline Procedure](#main-pipeline-procedure)
- [State Variables](#state-variables)
- [Decision Logic](#decision-logic)
- [Error Handling](#error-handling)

---

## Overview

The pipeline orchestrates three agents (Searcher, Analyzer, Reviewer) in an iterative loop. The Reviewer's decision determines whether to:

1. **Complete** — Return the final analysis
2. **Rework** — Send feedback to the Analyzer
3. **More Info** — Request additional searches

```
┌─────────────────────────────────────────────────────────┐
│                   Pipeline States                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [INIT] ──▶ [SEARCH] ──▶ [ANALYZE] ──▶ [REVIEW]        │
│                ▲              ▲             │           │
│                │              │             ▼           │
│                │              │      ┌──────────────┐   │
│                │              └──────│ NEEDS_REWORK │   │
│                │                     └──────────────┘   │
│                │                            │           │
│                │                     ┌──────────────┐   │
│                └─────────────────────│NEEDS_MORE_INFO│  │
│                                      └──────────────┘   │
│                                             │           │
│                                      ┌──────────────┐   │
│                                      │   COMPLETE   │──▶ [END]
│                                      └──────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Main Pipeline Procedure

```pseudo
PROCEDURE run_pipeline(initial_query):

  // ═══════════════════════════════════════════════════════
  // INITIALIZATION
  // ═══════════════════════════════════════════════════════
  
  SET max_iterations = 3
  SET conversation_history = empty list
  SET search_results = empty data structure
  SET current_search_queries = initial_query
  SET feedback_for_rework = null

  // ═══════════════════════════════════════════════════════
  // MAIN ITERATION LOOP
  // ═══════════════════════════════════════════════════════
  
  FOR iteration FROM 1 TO max_iterations:

    LOG "Starting Iteration {iteration}/{max_iterations}"
    APPEND new entry for this iteration to conversation_history

    // ─────────────────────────────────────────────────────
    // PHASE 1: SEARCH
    // ─────────────────────────────────────────────────────
    // This phase executes only if NOT in rework mode
    
    IF feedback_for_rework IS null:
      
      IF current_search_queries IS a list:
        // Multiple new queries from Reviewer
        FOR EACH query IN current_search_queries:
          FETCH new results for query
          MERGE unique new results into search_results
      ELSE:
        // Single query (initial or new)
        SET search_results = FETCH results for current_search_queries
      END IF

      // Abort if no results found
      IF search_results IS empty:
        LOG "CRITICAL: Search failed."
        RETURN with ERROR
      END IF
      
      LOG search results to conversation_history
      
    ELSE:
      LOG "Skipping Search Phase — using existing results for rework"
    END IF


    // ─────────────────────────────────────────────────────
    // PHASE 2: ANALYSIS
    // ─────────────────────────────────────────────────────
    
    LOG "Calling Analyzer Agent"
    
    SET analysis = CALL analyzer_agent WITH:
      - initial_query
      - search_results
      - feedback_for_rework (may be null)
    
    LOG analysis to conversation_history
    
    // Clear feedback after use
    RESET feedback_for_rework = null


    // ─────────────────────────────────────────────────────
    // PHASE 3: REVIEW
    // ─────────────────────────────────────────────────────
    
    LOG "Calling Reviewer Agent"
    
    SET is_final_round = (iteration == max_iterations)
    
    SET review = CALL reviewer_agent WITH:
      - initial_query
      - analysis
      - is_final_round
    
    // Parse the reviewer's function call response
    SET command, payload = PARSE review
    
    LOG review command to conversation_history


    // ─────────────────────────────────────────────────────
    // PHASE 4: DECISION AND LOOP CONTROL
    // ─────────────────────────────────────────────────────
    
    SWITCH command:
      
      CASE "COMPLETE":
        LOG "Pipeline complete — Reviewer approved"
        RETURN:
          - final analysis
          - citations
          - conversation_history
      
      CASE "NEEDS_REWORK":
        LOG "Looping for rework with feedback"
        SET feedback_for_rework = payload
        SET current_search_queries = null  // Don't re-search
      
      CASE "NEEDS_MORE_INFO":
        LOG "Looping for more information"
        SET current_search_queries = payload  // New search queries
        SET feedback_for_rework = null        // Clear any old feedback
      
      DEFAULT:
        LOG "CRITICAL: Unknown command from Reviewer"
        RETURN with ERROR
    
    END SWITCH

  END FOR


  // ═══════════════════════════════════════════════════════
  // POST-LOOP: MAX ITERATIONS REACHED
  // ═══════════════════════════════════════════════════════
  
  LOG "Max iterations reached — returning best available output"
  
  SET final_analysis = GET analysis from last iteration
  SET final_citations = GET citations from last iteration
  
  RETURN:
    - final_analysis
    - final_citations
    - conversation_history

END PROCEDURE
```

---

## State Variables

| Variable | Type | Description |
|----------|------|-------------|
| `max_iterations` | int | Maximum loop iterations (default: 3) |
| `conversation_history` | list | Full execution trace for debugging |
| `search_results` | dict | Accumulated search data with content |
| `current_search_queries` | string \| list | Query(ies) for next search phase |
| `feedback_for_rework` | string \| null | Reviewer feedback for Analyzer |
| `is_final_round` | bool | True on last iteration |

---

## Decision Logic

### Reviewer Decision Tree

```
                    ┌─────────────────────┐
                    │  Evaluate Analysis  │
                    └──────────┬──────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
    ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
    │   Accurate &  │  │   Fixable     │  │   Missing     │
    │   Complete    │  │   Issues      │  │   Information │
    └───────┬───────┘  └───────┬───────┘  └───────┬───────┘
            │                  │                  │
            ▼                  ▼                  ▼
    ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
    │mark_as_complete│  │request_rework │  │request_more_  │
    │     ()        │  │  (feedback)   │  │ info(queries) │
    └───────────────┘  └───────────────┘  └───────────────┘
```

### Final Round Behavior

On the final iteration (`is_final_round = true`):

- `request_more_info` is **disabled**
- Only `mark_as_complete` or `request_rework` are available
- Forces a decision with existing data

---

## Error Handling

### Search Failures

```pseudo
IF search_results IS empty:
  RETURN {
    error: "Could not fetch search results",
    history: conversation_history
  }
```

### Unknown Reviewer Commands

```pseudo
IF command NOT IN ["COMPLETE", "NEEDS_REWORK", "NEEDS_MORE_INFO"]:
  LOG error details
  RETURN {
    error: "Unknown command from Reviewer: {command}",
    history: conversation_history
  }
```

### Graceful Degradation

When max iterations are reached without `COMPLETE`:

```pseudo
// Return the best available output
RETURN {
  response: last generated analysis,
  references: last collected citations,
  history: conversation_history
}
```

---

## Implementation Notes

1. **Async Operations**: Search and content fetching are async for parallelism
2. **State Persistence**: `conversation_history` captures full trace
3. **Tool Parsing**: `tool_parser_agent()` extracts decisions from Gemini responses
4. **Deduplication**: `aggregate_search_results()` prevents duplicate URL fetching