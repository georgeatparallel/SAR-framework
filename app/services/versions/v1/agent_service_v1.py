# app/services/agent_service.py
# VERSION 1.1.0 -- STABLE

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

def parse_reviewer_command(review_text: str) -> tuple[str, str | list[str] | None]:
    """
    Parses the command from the reviewer agent's response.
    Returns a tuple of (command, payload).
    """
    if review_text.startswith("COMPLETE"):
        return "COMPLETE", None
    
    parts = review_text.split(":", 1)
    if len(parts) != 2:
        return "UNKNOWN", review_text

    command, payload_str = parts[0].strip(), parts[1].strip()

    if command == "NEEDS_REWORK":
        return "NEEDS_REWORK", payload_str
    
    if command == "NEEDS_MORE_INFO":
        try:
            # Safely parse the JSON string array into a Python list
            queries = json.loads(payload_str)
            if isinstance(queries, list):
                return "NEEDS_MORE_INFO", queries
            else:
                return "UNKNOWN", f"Payload for NEEDS_MORE_INFO was not a list: {payload_str}"
        except json.JSONDecodeError:
            return "UNKNOWN", f"Invalid JSON in NEEDS_MORE_INFO payload: {payload_str}"
    
    return "UNKNOWN", review_text

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
    
    prompt = f"""
    You are the Analyzer Agent...
    """
    if feedback:
        prompt += f"""...Feedback: "{feedback}"..."""
    prompt += "Based on all the provided information, construct a comprehensive answer."
    
    print("-> [Agent: Analyzer] Generating analysis with Gemini model...")
    response = model.generate_content(prompt)
    print("-> [Agent: Analyzer] Analysis generation complete.")
    logger.info("Analyzer Agent: Analysis complete.")
    return response.text

def reviewer_agent(query: str, analysis: str) -> str:
    """Reviews the analysis and provides feedback for completion or iteration."""
    print("\n-> [Agent: Reviewer] Received task to review generated analysis.")
    logger.info("Reviewer Agent: Reviewing the analysis.")
    prompt = f"""
    You are the Reviewer Agent. Your role is to assess the quality of the generated answer based on the user's original query.
    User Query: "{query}"
    Generated Answer: "{analysis}"

    Carefully review the answer for accuracy, completeness, and relevance.

    Respond with ONE of the following commands and ONLY the command:
    - `COMPLETE`: If the answer is satisfactory and fully addresses the user's query.
    - `NEEDS_REWORK: [Provide specific, actionable feedback for the Analyzer Agent. Explain what is missing or incorrect in the analysis.]`: If the information seems sufficient but the analysis is flawed or incomplete.
    - `NEEDS_MORE_INFO: [Provide a JSON-formatted list of new, specific search query strings. Example: ["query 1", "query 2"]]`: If the answer is good but requires additional information that was not in the initial search results.
    """
    print("-> [Agent: Reviewer] Evaluating analysis with Gemini model...")
    response = model.generate_content(prompt)
    review_command = response.text.strip()
    print(f"-> [Agent: Reviewer] Review complete. Issued command: {review_command}")
    logger.info(f"Reviewer Agent: Review complete. Command: {review_command}")
    return review_command


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

        print(" M: Calling Analyzer Agent to synthesize an answer...")
        logger.info("Pipeline: Calling Analyzer Agent.")
        analysis = analyzer_agent(initial_query, search_results, feedback=feedback)
        conversation_history[-1]["steps"].append({"agent": "Analyzer", "feedback_received": feedback, "analysis": analysis})
        feedback = None

        print(" M: Calling Reviewer Agent to assess the answer...")
        logger.info("Pipeline: Calling Reviewer Agent.")
        review = reviewer_agent(initial_query, analysis)
        command, payload = parse_reviewer_command(review)
        conversation_history[-1]["steps"].append({"agent": "Reviewer", "review": review})

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
            print(f" M: CRITICAL - Unknown command from Reviewer: {review}. Aborting.")
            logger.error(f"Pipeline: Unknown command from Reviewer: {review}")
            return {"error": f"Unknown command from Reviewer: {review}", "history": conversation_history}

    print("\n M: Maximum iterations reached. Ending pipeline.")
    logger.warning("Pipeline: Max iterations reached.")
    print(" M: Calling Analyzer one last time to generate the best possible response.")
    final_analysis = analyzer_agent(initial_query, search_results)
    return {
        "response": "Max iterations reached. Returning the best analysis so far.", 
        "analysis": final_analysis,
        "history": conversation_history
    }