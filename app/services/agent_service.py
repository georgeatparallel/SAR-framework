# app/services/agent_service.py
# VERSION 2.3.0 -- STABLE

import os
import json
import logging
import asyncio
import google.generativeai as genai
from . import search_service

# Add required imports at the top of the file
from playwright.async_api import async_playwright
from .search_service import perform_google_search, fetch_and_parse_html_async, fetch_and_parse_pdf_async

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

# --- Helper: safely pull the submit_report call out of the Gemini response -----------------

def _extract_submit_report(response: genai.types.GenerateContentResponse) -> tuple[str, list[dict]]:
    """Return (report_markdown, citations_list) from a Gemini function‑call response.

    Raises
    ------
    ValueError
        If the response does not contain a call to `submit_report`.
    """
    try:
        fc = response.candidates[0].content.parts[0].function_call
    except (AttributeError, IndexError):
        raise ValueError("Model response contained no function_call – check prompt/tools")

    if fc.name != "submit_report":
        raise ValueError(f"Expected 'submit_report' but model called '{fc.name}' instead")

    # Convert the protobuf Struct → native Python types so we can JSON‑serialise later
    report_md: str = fc.args["report"]
    citations_proto = fc.args.get("citations_used", [])
    citations: list[dict] = [dict(c) for c in citations_proto]
    return report_md, citations

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

def submit_report(report: str, citations_used: list[str]) -> None:
    """
    Call this function to submit your final analysis and all cited sources.
    
    Args:
        report (str): The full, final, well-structured text of your analysis in Markdown format.
        citations_used (list[str]): A list of all sources that were cited in the report,
                                        conforming to the Citation schema.
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
    Runs new search queries, de-duplicates URLs, fetches content in a single
    async batch, and combines with existing results.
    """
    print(f" M: Aggregating search results for {len(queries)} new queries...")
    logger.info(f"Aggregating search results for {len(queries)} new queries.")
    
    all_new_items = []
    
    # --- Phase 1: Perform all Google Searches Concurrently in a Thread Pool ---
    print(" M: [Aggregator] Step 1/3 - Performing all Google searches concurrently...")
    # Create a list of tasks to run the blocking function in separate threads
    search_tasks = [asyncio.to_thread(perform_google_search, q) for q in queries]
    # Gather the results from all the search threads
    list_of_results = await asyncio.gather(*search_tasks)
    # Flatten the list of lists into a single list of items
    for result_list in list_of_results:
        all_new_items.extend(result_list)
    
    print(f" M: [Aggregator] Found {len(all_new_items)} new potential results across all queries.")

    # --- Phase 2: Centralize and De-duplicate the Fetching Workload ---
    print(" M: [Aggregator] Step 2/3 - De-duplicating URLs to fetch...")
    seen_urls = {item['link'] for item in existing_results.get('results', [])}
    unique_new_items_to_fetch = []
    
    for item in all_new_items:
        if item['link'] not in seen_urls:
            unique_new_items_to_fetch.append(item)
            seen_urls.add(item['link']) # Add to seen_urls immediately to handle duplicates within the new results
            
    if not unique_new_items_to_fetch:
        print(" M: [Aggregator] No new unique URLs to fetch. Aggregation complete.")
        return existing_results
        
    print(f" M: [Aggregator] De-duplication complete. {len(unique_new_items_to_fetch)} new unique URLs will be fetched.")

    # --- Phase 3: Execute the Centralized Fetch and Combine Results ---
    print(" M: [Aggregator] Step 3/3 - Launching unified fetch operation...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        tasks = []
        for i, item in enumerate(unique_new_items_to_fetch):
            url = item['link']
            if url.lower().endswith('.pdf'):
                tasks.append(fetch_and_parse_pdf_async(url, i))
            else:
                tasks.append(fetch_and_parse_html_async(browser, url, i))
        
        # This single gather waits for ALL scraping to complete.
        contents = await asyncio.gather(*tasks)
        await browser.close()

    # Combine fetched content back into the items
    for i, item in enumerate(unique_new_items_to_fetch):
        item['content'] = contents[i]
        print(f"   -> Successfully fetched and combined content for: {item['link']}")

    # Add the new, fully-populated items to the existing results
    final_results = list(existing_results.get('results', []))
    final_results.extend(unique_new_items_to_fetch)
    
    print(f" M: [Aggregator] Aggregation complete. Total unique results: {len(final_results)}")
    logger.info(f"Aggregation complete. Total unique results: {len(final_results)}")
    
    return {"results": final_results}


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

# PROPOSED REPLACEMENT for the analyzer_agent function in app/services/agent_service.py

def analyzer_agent(query: str, search_results: dict, feedback: str = None, num_failed_sources: int = 0) -> dict:
    """
    Analyzes search results, calls the `submit_report` tool, parses the response,
    and returns a standardized dictionary.
    """
    print(f"\n-> [Agent: Analyzer] Received task to analyze {len(search_results.get('results', []))} results and call submit_report tool.")
    if feedback:
        print(f"-> [Agent: Analyzer] Incorporating feedback for rework: \"{feedback}\"")
    logger.info("Analyzer Agent: Analyzing search results to generate a structured report via tool call.")

    available_tools = [submit_report]
    
    # --- FIX 1: Use the correct 'tool_config' parameter ---
    tool_config = {'function_calling_config': {'mode': 'ANY'}}

    prompt = f"""
    You are an expert research analyst and writer. Your goal is to produce a clear, balanced, and analytically deep briefing memo. Your final action MUST be to call the `submit_report` function with your completed work.

    **User Query:** "{query}"

    **Search Results Data:**
    {json.dumps(search_results, indent=2)}

    **--- YOUR SYSTEM OF INSTRUCTIONS ---**

    **PART 1: EVIDENCE TRIAGE**
    Before composing your report, first triage the `Search Results Data`. Prioritize information from the most recent sources.

    **PART 2: CONTENT AND STRUCTURE of the 'report' Argument**
    - Identify the core themes required by the User Query and structure your report around them.
    - Use Markdown formatting: `##` for headings, `*` for bullet points.
    - For comparative queries, conclude with a Markdown summary table.
    - For every claim, you MUST include an APA-style in-line citation `(Author, Year)`.

    **PART 3: CONTENT of the 'citations_used' Argument**
    - Your function signature for `submit_report` requires the `citations_used` argument to be a `list[str]`.
    - For every source you cited, you must provide its corresponding URL as a string in this list.
    - The `url` for each citation string MUST be the exact value from the `link` key of that source in the `Search Results Data`.

    **PART 4: ERROR HANDLING**
    - If `{num_failed_sources}` is greater than 0, you must include a concluding note in your report stating that "{num_failed_sources} potential sources could not be retrieved."

    **--- FINAL ACTION ---**
    After you have composed the full report and the list of citations according to all instructions, you MUST call the `submit_report` function. Provide the entire Markdown report as the `report` argument, and provide the complete list of citation URL strings as the `citations_used` argument.
    """
    if feedback:
        prompt += f"""
        **--- REWORK INSTRUCTIONS ---**
        The previous analysis was reviewed. Regenerate your work and re-submit it by calling the `submit_report` tool based on the following feedback:
        **Feedback:** "{feedback}"
        """

    print("-> [Agent: Analyzer] Calling Gemini model and expecting a tool call...")
    try:
        response = model.generate_content(
            prompt,
            tools=available_tools,
            tool_config=tool_config 
        )
        
        function_call = response.candidates[0].content.parts[0].function_call

        if function_call.name == "submit_report":
            print("-> [Agent: Analyzer] Received 'submit_report' tool call. Parsing arguments...")
            args = dict(function_call.args)
            
            # --- FIX 2: Correctly parse the list of strings ---
            # The model returns a protobuf-like list, so we cast it to a native Python list.
            citations = []
            if 'citations_used' in args and args['citations_used']:
                citations = list(args['citations_used'])
            
            report_text = args.get('report', '')

            logger.info("Analyzer Agent: Successfully received and parsed submit_report tool call.")
            return {
                "report": report_text,
                "citations_used": citations
            }
        else:
            raise ValueError(f"Expected 'submit_report' tool call, but received '{function_call.name}'.")

    except Exception as e:
        print(f"-> [Agent: Analyzer] CRITICAL ERROR: An error occurred during tool call processing. Error: {e}")
        # --- FIX 3: Use a safe method to log the response to avoid another error ---
        raw_response_content = "No response object"
        if 'response' in locals():
            try:
                # Try to get the raw parts safely
                raw_response_content = str(response.candidates[0].content.parts[0])
            except (AttributeError, IndexError):
                raw_response_content = "Could not extract parts from response object."

        logger.error(f"Analyzer Agent: Failed to get or parse tool call. Raw response part: {raw_response_content}. Error: {e}")
        return {
            "report": f"Error: The Analyzer agent failed to call or parse the submit_report tool correctly. Details: {e}",
            "citations_used": []
        }

# PROPOSED REVISION for app/services/agent_service.py

# --- Make sure our new tools are available to be referenced ---
# (Assuming they were added in the HELPER FUNCTIONS section as per Phase 1)

# PROPOSED REVISION for app/services/agent_service.py

# PROPOSED REVISION for app/services/agent_service.py (VERSION 2.2.0)

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
        2.  Assess the source material mentioned or implied in the analysis. Is it recent, relevant, and sufficient to meet the query's requirements?
        3.  Based on your assessment, call one of the following functions:
            - **mark_as_complete:** Call this if the answer is complete, accurate, and based on sufficient data.
            - **request_rework:** Call this if the answer has correctable flaws (e.g., style, structure, minor omissions) but the underlying source data appears to be sufficient.
            - **request_more_info:** Call this if the analysis is fundamentally weak because the sources are **outdated, irrelevant, or insufficient** to answer the query properly. Provide new, specific search queries to find better information (e.g., queries focused on a more recent timeframe like "right to repair 2024 arguments"). You should also call this if the analysis explicitly mentions data fetching errors.
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

# PROPOSED FINAL VERSION of the orchestration function in app/services/agent_service.py

# PROPOSED FINAL REPLACEMENT for the run_agentic_pipeline function in app/services/agent_service.py

async def run_agentic_pipeline(initial_query: str):
    """
    The main orchestration for the agentic pipeline, now with state management,
    iteration, and tool-based analysis submission.
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
            
            # Placeholder for tracking fetch failures
            num_failures = 0 
            
            conversation_history[-1]["steps"].append({"agent": "Searcher", "query": current_queries, "results_count": len(search_results.get("results", []))})
        else:
            print(" M: Skipping Searcher Agent. Using existing results for rework.")
            logger.info("Pipeline: Skipping Searcher Agent for rework.")
            conversation_history[-1]["steps"].append({"agent": "Searcher", "action": "Skipped, using previous results for rework."})
            num_failures = 0

        # 2. Analysis Phase
        print(" M: Calling Analyzer Agent to synthesize an answer...")
        logger.info("Pipeline: Calling Analyzer Agent.")
        analysis_payload = analyzer_agent(initial_query, search_results, feedback=feedback, num_failed_sources=num_failures)
        
        # --- FIX: The analyzer_agent now returns a clean dictionary. We just need to get the values. ---
        report_text = analysis_payload.get('report', 'Error: Report text not found in payload.')
        citations_list = analysis_payload.get('citations_used', [])
        # No more _extract_submit_report needed here.
        logger.info("Pipeline: Received parsed payload from Analyzer Agent.")

        conversation_history[-1]["steps"].append({
            "agent": "Analyzer", 
            "feedback_received": feedback, 
            "analysis": report_text,
            "citations": citations_list
        })
        feedback = None

        # 3. Review Phase
        logger.info("Pipeline: Calling Reviewer Agent.")
        
        is_final = (i == max_iterations - 1)
        
        review_response = reviewer_agent(initial_query, report_text, is_final_round=is_final)
        command, payload = tool_parser_agent(review_response)
        
        # Safely log reviewer's decision
        try:
            review_decision_str = str(review_response.candidates[0].content.parts[0])
        except (AttributeError, IndexError):
            review_decision_str = "Could not parse reviewer decision."
        conversation_history[-1]["steps"].append({"agent": "Reviewer", "review": review_decision_str})

        if command == "COMPLETE":
            print("\n M: Reviewer approved the answer. Pipeline complete! ✅")
            logger.info("Pipeline: Reviewer returned COMPLETE. Finalizing.")
            return {
                "response": report_text, 
                "references": citations_list,
                "history": conversation_history
            }
        elif command == "NEEDS_REWORK":
            feedback = payload
            current_queries = None # Ensure we don't re-search on a rework request
            print(f"\n M: Reviewer requested rework. Looping again with feedback.")
            print(f"   [Feedback]: {feedback}")
            logger.info(f"Pipeline: Reviewer requested rework. Feedback: {feedback}")
        elif command == "NEEDS_MORE_INFO":
            current_queries = payload
            feedback = None # Ensure we don't pass old feedback on a new info request
            print(f"\n M: Reviewer requested more information. Looping again with new searches.")
            print(f"   [New Search Queries]: {current_queries}")
            logger.info(f"Pipeline: Reviewer requested more info. New queries: {current_queries}")
        else:
            # Handle unknown commands or errors from the reviewer
            error_details = payload or "Unknown error"
            print(f" M: CRITICAL - Unknown command or error from Reviewer: {command}. Details: {error_details}. Aborting.")
            logger.error(f"Pipeline: Unknown command '{command}' from Reviewer. Payload: {error_details}")
            return {"error": f"Unknown command from Reviewer: {command}. Details: {error_details}", "history": conversation_history}

    print("\n M: Maximum iterations reached. Ending pipeline.")
    logger.warning("Pipeline: Max iterations reached.")

    final_report_text = report_text
    final_references_list = citations_list

    return {
        "response": final_report_text,
        "references": final_references_list,
        "history": conversation_history
    }