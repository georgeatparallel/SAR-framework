
import os
import json
import google.generativeai as genai
from . import search_service

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env file")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

async def searcher_agent(query: str) -> dict:
    """
    Calls the search service to get search results.
    """
    return await search_service.search_and_fetch_content(query)

def analyzer_agent(query: str, search_results: dict) -> str:
    """
    Analyzes search results using the Gemini model.
    """
    print("Analyzer Agent: Analyzing search results.")
    prompt = f"""
    You are the Analyzer Agent. Your task is to analyze the provided search results and construct a detailed and accurate answer to the user's query.
    User Query: "{query}"
    Search Results:
    {json.dumps(search_results, indent=2)}

    Based on the content of the search results, provide a comprehensive answer.
    """
    response = model.generate_content(prompt)
    return response.text

def reviewer_agent(query: str, analysis: str) -> str:
    """
    Reviews the analysis and provides feedback.
    """
    print("Reviewer Agent: Reviewing the analysis.")
    prompt = f"""
    You are the Reviewer Agent. Your role is to assess the quality of the generated answer based on the user's original query.
    User Query: "{query}"
    Generated Answer: "{analysis}"

    Respond with ONE of the following commands:
    - `COMPLETE`: If the answer is satisfactory.
    - `NEEDS_MORE_INFO: [new, more specific query]`: If the answer is good but needs more information.
    - `NEEDS_REWORK: [specific feedback for the Analyzer agent]`: If the information is sufficient but the analysis is flawed.
    """
    response = model.generate_content(prompt)
    return response.text.strip()

async def run_agentic_pipeline(query: str):
    """
    The main orchestration for the agentic pipeline.
    """
    max_iterations = 3
    for i in range(max_iterations):
        print(f"Iteration {i+1} ---")
        
        # 1. Call Searcher
        search_results = await searcher_agent(query)
        if not search_results.get("results"):
            return {"error": "Could not fetch search results."}

        # 2. Call Analyzer
        analysis = analyzer_agent(query, search_results)

        # 3. Call Reviewer
        review = reviewer_agent(query, analysis)

        print(f"Reviewer Command: {review}")

        if review.startswith("COMPLETE"):
            return {"response": analysis}
        elif review.startswith("NEEDS_MORE_INFO"):
            query = review.split(":", 1)[1].strip()
        elif review.startswith("NEEDS_REWORK"):
            pass
        else:
            return {"error": f"Unknown command from Reviewer: {review}"}

    return {"response": "Max iterations reached. Here is the last analysis.", "analysis": analysis}
