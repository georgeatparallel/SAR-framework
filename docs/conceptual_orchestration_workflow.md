```pseudo
PROCEDURE run_pipeline(initial_query):

  // Initialization
  SET max_iterations = 3
  SET conversation_history = an empty list
  SET search_results = an empty data structure
  SET current_search_queries = initial_query
  SET feedback_for_rework = null

  // Start the main iteration loop
  FOR iteration FROM 1 TO max_iterations:

    LOG "Starting Iteration {iteration}/{max_iterations}"
    ADD new entry for this iteration to conversation_history

    // === 1. SEARCH PHASE ===
    // This phase runs only if there is no feedback for rework.
    IF feedback_for_rework is null:
      // If there are multiple new queries, aggregate them.
      IF current_search_queries is a list:
        FETCH new results for each query in current_search_queries
        ADD unique new results to search_results
      ELSE:
        // Otherwise, perform a single search.
        FETCH new results for current_search_queries
        REPLACE search_results with the new results
      END IF

      // Abort if no search results were found at all.
      IF search_results is empty:
        LOG "CRITICAL: Search failed."
        RETURN with ERROR
      END IF
      LOG search results to conversation_history
    ELSE:
      LOG "Skipping Search Phase for rework." to conversation_history
    END IF


    // === 2. ANALYSIS PHASE ===
    LOG "Calling Analyzer Agent."
    SET analysis = CALL analyzer_agent WITH initial_query, search_results, and feedback_for_rework
    LOG analysis and feedback to conversation_history
    RESET feedback_for_rework to null


    // === 3. REVIEW PHASE ===
    LOG "Calling Reviewer Agent."

    // Check if this is the final iteration.
    SET is_final_round = (iteration == max_iterations)

    // Call the reviewer, telling it if it's the final round.
    SET review = CALL reviewer_agent WITH initial_query, analysis, and is_final_round

    // The reviewer's output is a structured command.
    SET command, payload = PARSE review

    LOG review command to conversation_history


    // === 4. DECISION AND LOOP CONTROL ===
    IF command is "COMPLETE":
      LOG "Pipeline complete."
      RETURN final analysis and conversation_history
    ELSE IF command is "NEEDS_REWORK":
      LOG "Looping for rework."
      SET feedback_for_rework = payload
      RESET current_search_queries to null
    ELSE IF command is "NEEDS_MORE_INFO":
      LOG "Looping for more information."
      SET current_search_queries = payload
    ELSE:
      LOG "CRITICAL: Unknown command."
      RETURN with ERROR
    END IF

  END FOR


  // === POST-LOOP: MAX ITERATIONS REACHED ===
  // This is reached if the loop finishes without a "COMPLETE" command.
  LOG "Max iterations reached."
  
  // The definitive final analysis is the one generated in the very last iteration.
  SET final_analysis = GET the analysis from the last entry in conversation_history

  RETURN final_analysis and conversation_history

END PROCEDURE
```