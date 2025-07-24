
import os
import json
import google.generativeai as genai
from . import search_service

# --- Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env file")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')


# --- Agent Functions ---

async def searcher_agent(query: str) -> dict:
    """
    Calls the search service to get search results with content.
    """
    print(f"  [Searcher Agent] Performing search for: '{query}'")
    results = await search_service.search_and_fetch_content(query)
    print(f"  [Searcher Agent] Found {len(results.get('results', []))} results.")
    return results

def analyzer_agent(query: str, search_results: dict, feedback: str = None) -> str:
    """
    Analyzes search results using the Gemini model, optionally incorporating feedback.
    """
    print("  [Analyzer Agent] Analyzing search results.")
    
    # Base prompt
    prompt = f"""
    You are the Analyzer Agent. Your task is to analyze the provided search results and construct a detailed and accurate answer to the user's original query.
    User Query: "{query}"
    Search Results:
    {json.dumps(search_results, indent=2)}
    """

    # Add rework feedback if provided
    if feedback:
        print(f"Analyzer Agent: Incorporating feedback: {feedback}")
        prompt += f"""
        The previous analysis was reviewed and requires rework. Please revise your answer based on the following feedback:
        Feedback: "{feedback}"
        """

    prompt += "Based on all the provided information, construct a comprehensive answer."
    
    response = model.generate_content(prompt)
    return response.text

def reviewer_agent(query: str, analysis: str) -> str:
    """
    Reviews the analysis and provides feedback for completion or iteration.
    """
    print("Reviewer Agent: Reviewing the analysis.")
    prompt = f"""
    You are the Reviewer Agent. Your role is to assess the quality of the generated answer based on the user's original query.
    User Query: "{query}"
    Generated Answer: "{analysis}"

    Carefully review the answer for accuracy, completeness, and relevance.

    Respond with ONE of the following commands and ONLY the command:
    - `COMPLETE`: If the answer is satisfactory and fully addresses the user's query.
    - `NEEDS_REWORK: [Provide specific, actionable feedback for the Analyzer Agent. Explain what is missing or incorrect in the analysis.]`: If the information seems sufficient but the analysis is flawed or incomplete.
    - `NEEDS_MORE_INFO: [Formulate a new, more specific search query to find the missing information.]`: If the answer is good but requires additional information that was not in the initial search results.
    """
    response = model.generate_content(prompt)
    return response.text.strip()


# --- Orchestration ---

async def run_agentic_pipeline(initial_query: str):
    """
    The main orchestration for the agentic pipeline, now with state management and iteration.
    """
    query = initial_query
    feedback = None
    conversation_history = []
    
    max_iterations = 3
    for i in range(max_iterations):
        print(f"--- Iteration {i+1} ---")
        conversation_history.append({"iteration": i + 1, "steps": []})

        # 1. Call Searcher
        # The searcher only runs if we don't have feedback for rework (which implies we use existing data)
        if feedback is None:
            search_results = await searcher_agent(query)
            if not search_results.get("results"):
                return {"error": "Could not fetch search results.", "history": conversation_history}
            conversation_history[-1]["steps"].append({"agent": "Searcher", "query": query, "results_count": len(search_results.get("results", []))})
        else:
             # On a rework, we re-use the previous search results
             conversation_history[-1]["steps"].append({"agent": "Searcher", "action": "Skipped, using previous results for rework."})


        # 2. Call Analyzer
        analysis = analyzer_agent(initial_query, search_results, feedback=feedback)
        conversation_history[-1]["steps"].append({"agent": "Analyzer", "feedback_received": feedback, "analysis": analysis})
        feedback = None # Reset feedback after use

        # 3. Call Reviewer
        review = reviewer_agent(initial_query, analysis)
        conversation_history[-1]["steps"].append({"agent": "Reviewer", "review": review})
        print(f"Reviewer Command: {review}")

        if review.startswith("COMPLETE"):
            return {"response": analysis, "history": conversation_history}
        elif review.startswith("NEEDS_REWORK"):
            feedback = review.split(":", 1)[1].strip()
        elif review.startswith("NEEDS_MORE_INFO"):
            query = review.split(":", 1)[1].strip()
        else:
            return {"error": f"Unknown command from Reviewer: {review}", "history": conversation_history}

    final_analysis = analyzer_agent(initial_query, search_results)
    return {
        "response": "Max iterations reached. Returning the best analysis so far.", 
        "analysis": final_analysis,
        "history": conversation_history
    }
