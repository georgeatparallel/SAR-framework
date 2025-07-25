# app/services/agent_service.py
# VERSION 2.1.0 -- STABLE

import os
import json
import logging
import asyncio
import google.generativeai as genai
from . import search_service

# --- Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env file")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')
logger = logging.getLogger(__name__)


# --- HELPER FUNCTIONS ---
# These must be defined before the main pipeline function that uses them.

# PROPOSED ADDITIONS to app/services/agent_service.py
# To be placed in the "HELPER FUNCTIONS" section

def mark_as_complete() -> None:
    """
    Call this function when the provided analysis is satisfactory, accurate,
    and fully addresses the user's original query.
    """
    pass

def request_rework(feedback: str) -> None:
    """
    Call this function when the analysis is flawed, inaccurate, or incomplete,
    but the existing information seems sufficient to correct it.

    Args:
        feedback (str): Specific, actionable feedback for the Analyzer Agent.
                        Explain what is missing, incorrect, or needs improvement
                        in the analysis.
    """
    pass

def request_more_info(queries: list[str]) -> None:
    """
    Call this function when the analysis is good but requires additional
    information that was not available in the initial search results.

    Args:
        queries (list[str]): A list of new, specific search query strings
                             to be executed by the Searcher Agent.
    """
    pass

# PROPOSED NEW FUNCTION for app/services/agent_service.py
# To be placed in the "HELPER FUNCTIONS" section

def tool_parser_agent(response: genai.types.GenerateContentResponse) -> tuple[str, str | list[str] | None]:
    """
    Parses the function call from a Gemini model response and translates it
    into a command and payload for the orchestrator.
    """
    try:
        function_call = response.candidates[0].content.parts[0].function_call
    except (AttributeError, IndexError):
        # This occurs if the model response does not contain a function call.
        logger.warning("Tool Parser: No function call found in the response.")
        return "UNKNOWN", "No function call was returned by the Reviewer Agent."

    command_name = function_call.name
    args = function_call.args

    print(f" M: Tool Parser decoded function call: {command_name}")
    logger.info(f"Tool Parser: Decoded function call '{command_name}' from Reviewer.")

    if command_name == "mark_as_complete":
        return "COMPLETE", None
    
    if command_name == "request_rework":
        # The payload is the 'feedback' argument.
        feedback = args.get('feedback', 'No feedback provided.')
        return "NEEDS_REWORK", feedback
    
    if command_name == "request_more_info":
        # The payload is the 'queries' argument. We must cast it to a native Python list
        # to ensure it is JSON serializable.
        queries_proto = args.get('queries', [])
        queries_list = list(queries_proto)
        return "NEEDS_MORE_INFO", queries_list
    
    logger.error(f"Tool Parser: Encountered an unknown function name: {command_name}")
    return "UNKNOWN", f"Unknown function call: {command_name}"

async def aggregate_search_results(queries: list[str], existing_results: dict) -> dict:
    """
    Runs a list of new search queries and combines them with existing results,
    avoiding duplicate URLs.
    """
    print(f" M: Aggregating search results for {len(queries)} new queries...")
    logger.info(f"Aggregating search results for {len(queries)} new queries.")

    seen_urls = {item['link'] for item in existing_results.get('results', [])}
    all_items = list(existing_results.get('results', []))

    search_tasks = [searcher_agent(q) for q in queries]
    new_results_list = await asyncio.gather(*search_tasks)

    for new_result in new_results_list:
        for item in new_result.get('results', []):
            if item['link'] not in seen_urls:
                all_items.append(item)
                seen_urls.add(item['link'])
                print(f"   -> Added new unique result: {item['link']}")
                logger.debug(f"Added new unique search result: {item['link']}")

    print(f" M: Aggregation complete. Total unique results: {len(all_items)}")
    logger.info(f"Aggregation complete. Total unique results: {len(all_items)}")
    return {"results": all_items}


# --- AGENT FUNCTIONS ---

async def searcher_agent(query: str) -> dict:
    """Calls the search service to get search results with content."""
    print(f"\n-> [Agent: Searcher] Received task to search for: '{query}'")
    logger.info(f"Searcher Agent: Starting search for query: '{query}'")
    results = await search_service.search_and_fetch_content(query)
    num_results = len(results.get('results', []))
    print(f"-> [Agent: Searcher] Search complete. Found {num_results} results.")
    logger.info(f"Searcher Agent: Found {num_results} results.")
    return results

def analyzer_agent(query: str, search_results: dict, feedback: str = None) -> str:
    """Analyzes search results using the Gemini model, optionally incorporating feedback."""
    num_results = len(search_results.get("results", []))
    print(f"\n-> [Agent: Analyzer] Received task to analyze {num_results} search results for query: '{query}'")
    if feedback:
        print(f"-> [Agent: Analyzer] Incorporating feedback for rework: \"{feedback}\"")
    logger.info("Analyzer Agent: Analyzing search results.")
    
    # Base prompt
    prompt = f"""
    You are the Analyzer Agent. Your task is to analyze the provided search results and construct a detailed and accurate answer to the user's original query.
    User Query: "{query}"

    Search Results:
    {json.dumps(search_results, indent=2)}

    **Instructions:**
    1.  First, review each search result. If a result's "content" field contains an error message (e.g., "Error fetching or parsing URL", "403 Forbidden"), explicitly state that this source could not be accessed.
    2.  Synthesize a comprehensive answer to the user query based *only* on the content from the successfully retrieved results.
    3.  If critical information is missing due to fetching errors, please note that your analysis may be incomplete.
    """
    if feedback:
        logger.info(f"Analyzer Agent: Incorporating feedback: {feedback}")
        prompt += f"""
        The previous analysis was reviewed and requires rework. Please revise your answer based on the following feedback:
        Feedback: "{feedback}"
        """

    prompt += "Based on all the provided information and the instructions above, construct your analysis."
    
    print("-> [Agent: Analyzer] Generating analysis with Gemini model...")
    response = model.generate_content(prompt)
    print("-> [Agent: Analyzer] Analysis generation complete.")
    logger.info("Analyzer Agent: Analysis complete.")
    return response.text

# PROPOSED REVISION for app/services/agent_service.py

# --- Make sure our new tools are available to be referenced ---
# (Assuming they were added in the HELPER FUNCTIONS section as per Phase 1)

# PROPOSED REVISION for app/services/agent_service.py

def reviewer_agent(query: str, analysis: str, is_final_round: bool = False) -> genai.types.GenerateContentResponse:
    """
    Reviews the analysis and decides which function to call.
    On the final round, it operates with a restricted toolset.
    """
    print(f"\n-> [Agent: Reviewer] Received task to review generated analysis. (Final Round: {is_final_round})")
    logger.info(f"Reviewer Agent: Reviewing the analysis. Final Round: {is_final_round}")

    if is_final_round:
        # Final round: The agent cannot request more info. It must accept or request one last rework.
        available_tools = [
            mark_as_complete,
            request_rework,
        ]
        prompt = f"""
        You are the Reviewer Agent. This is the FINAL iteration.
        Your role is to perform a final quality check. You CANNOT request more information.

        User Query: "{query}"
        Generated Answer: "{analysis}"

        **Instructions for Final Review:**
        1.  Read the Generated Answer carefully. Does it adequately address the User Query with the available information?
        2.  Based on your final assessment, you must call one of the following functions:
            - If the answer is acceptable as the final output, call `mark_as_complete`.
            - If the answer has critical flaws that MUST be fixed before returning to the user, call `request_rework` with specific, actionable feedback on what to correct.
        """
    else:
        # Standard round: The agent has all tools available.
        available_tools = [
            mark_as_complete,
            request_rework,
            request_more_info,
        ]
        prompt = f"""
        You are the Reviewer Agent. Your role is to critically assess the quality and completeness of the generated answer based on the user's original query.
        User Query: "{query}"
        Generated Answer: "{analysis}"

        **Instructions:**
        1.  Read the Generated Answer carefully. Does it fully and accurately address the User Query?
        2.  **CRITICAL:** Check if the answer mentions any limitations or failures in retrieving source data (e.g., "could not be accessed", "error fetching").
        3.  Based on your assessment, call one of the following functions:
            - If the answer is complete, accurate, and based on sufficient data, call `mark_as_complete`.
            - If the answer is flawed or needs correction but the data seems sufficient, call `request_rework` with specific feedback.
            - **If the answer explicitly states that key sources were inaccessible or that the analysis is incomplete due to data fetching errors, call `request_more_info`. Provide new, alternative search queries that might find the missing information elsewhere.**
        """

    print("-> [Agent: Reviewer] Evaluating analysis with Gemini model (function calling enabled)...")
    
    response = model.generate_content(
        prompt,
        tools=available_tools,
    )
    
    print(f"-> [Agent: Reviewer] Review complete. Model response received.")
    logger.info("Reviewer Agent: Review complete. Returning full response object.")
    return response


# --- ORCHESTRATION ---

async def run_agentic_pipeline(initial_query: str):
    """
    The main orchestration for the agentic pipeline, now with state management,
    iteration, and dynamic multi-query searches.
    """
    print(f"\n=========================================================")
    print(f"🚀 STARTING AGENTIC PIPELINE for query: '{initial_query}'")
    print(f"=========================================================")
    logger.info(f"--- Starting Agentic Pipeline for query: '{initial_query}' ---")

    current_queries = initial_query
    feedback = None
    search_results = {"results": []}
    conversation_history = []
    max_iterations = 3

    for i in range(max_iterations):
        print(f"\n M: Starting Pipeline Iteration {i+1}/{max_iterations}")
        print("---------------------------------------------------------")
        logger.info(f"--- Pipeline Iteration {i+1} ---")
        conversation_history.append({"iteration": i + 1, "steps": []})

        # 1. Search Phase
        if feedback is None:
            if isinstance(current_queries, list):
                print(" M: Calling Search Aggregator to find new information...")
                logger.info("Pipeline: Calling Search Aggregator for multiple new queries.")
                search_results = await aggregate_search_results(current_queries, search_results)
            else:
                print(" M: Calling Searcher Agent to find information...")
                logger.info("Pipeline: Calling Searcher Agent.")
                search_results = await searcher_agent(current_queries)
            
            if not search_results.get("results"):
                print(" M: CRITICAL - Searcher Agent(s) failed to return results. Aborting pipeline.")
                logger.error("Pipeline: Could not fetch search results.")
                return {"error": "Could not fetch search results.", "history": conversation_history}
            
            conversation_history[-1]["steps"].append({"agent": "Searcher", "query": current_queries, "results_count": len(search_results.get("results", []))})
        else:
             print(" M: Skipping Searcher Agent. Using existing results for rework.")
             logger.info("Pipeline: Skipping Searcher Agent for rework.")
             conversation_history[-1]["steps"].append({"agent": "Searcher", "action": "Skipped, using previous results for rework."})

        # 2. Analysis Phase
        print(" M: Calling Analyzer Agent to synthesize an answer...")
        logger.info("Pipeline: Calling Analyzer Agent.")
        analysis = analyzer_agent(initial_query, search_results, feedback=feedback)
        conversation_history[-1]["steps"].append({"agent": "Analyzer", "feedback_received": feedback, "analysis": analysis})
        feedback = None

        # 3. Review Phase
        logger.info("Pipeline: Calling Reviewer Agent.")
        
        is_final = (i == max_iterations - 1)
        
        review_response = reviewer_agent(initial_query, analysis, is_final_round=is_final)
        command, payload = tool_parser_agent(review_response)
        conversation_history[-1]["steps"].append({"agent": "Reviewer", "review": str(review_response.candidates[0].content.parts[0])})

        if command == "COMPLETE":
            print("\n M: Reviewer approved the answer. Pipeline complete! ✅")
            logger.info("Pipeline: Reviewer returned COMPLETE. Finalizing.")
            return {"response": analysis, "history": conversation_history}
        elif command == "NEEDS_REWORK":
            feedback = payload
            current_queries = None
            print(f"\n M: Reviewer requested rework. Looping again with feedback.")
            print(f"   [Feedback]: {feedback}")
            logger.info(f"Pipeline: Reviewer requested rework. Feedback: {feedback}")
        elif command == "NEEDS_MORE_INFO":
            current_queries = payload
            print(f"\n M: Reviewer requested more information. Looping again with new searches.")
            print(f"   [New Search Queries]: {current_queries}")
            logger.info(f"Pipeline: Reviewer requested more info. New queries: {current_queries}")
        else:
            print(f" M: CRITICAL - Unknown command from Reviewer: {review_response}. Aborting.")
            logger.error(f"Pipeline: Unknown command from Reviewer: {review_response}")
            return {"error": f"Unknown command from Reviewer: {review_response}", "history": conversation_history}

    print("\n M: Maximum iterations reached. Ending pipeline.")
    logger.warning("Pipeline: Max iterations reached.")

    final_analysis = conversation_history[-1]["steps"][-2]["analysis"]

    return {
        "response": final_analysis,
        "history": conversation_history
    }