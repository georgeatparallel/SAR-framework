# Agentic AI Pipeline Blueprint

## Vision

The vision is to build an agentic AI pipeline that, given a search task, can orchestrate multiple agents through custom system prompts and programmatic prompt engineering. These agents will work in a collaborative loop until the task is completed or a predefined limit (e.g., time) is reached.

This pipeline aims to automate complex information retrieval and analysis tasks by breaking them down into smaller steps handled by specialized agents, mimicking a team-based approach to problem-solving.

## Agentic Orchestration Model (Three-Agent MVP)

For the Minimum Viable Product (MVP), we will implement a three-way agentic orchestration model:

1.  **Agent 1: The Searcher**
    *   **Goal:** Perform accurate and relevant information retrieval through searching.
    *   **Functionality:** Take a search query as input, utilize the available search tool (`search_tool.py`) to fetch results, and perform an initial parsing or "loading" of the retrieved content (acting as an NLP Knowledge ETL process).
    *   **Output:** The raw or semi-processed retrieved content (e.g., parsed snippets, titles, links) passed to Agent 2.

2.  **Agent 2: The Analyzer**
    *   **Goal:** Process and analyze the retrieved content in the context of the original user query and generate the requested output.
    *   **Functionality:** Receive the output from Agent 1, understand the content of the documents, determine their relevance, and perform analytical work based on the user's request. This could involve summarizing, extracting key information, synthesizing insights, or formatting data.
    *   **Output:** The generated output for the user task (e.g., a Markdown document, a table, a summary) passed to Agent 3.

3.  **Agent 3: The Reviewer**
    *   **Goal:** Evaluate the work of Agent 2, determine task completion, and guide the workflow for further refinement or iteration.
    *   **Functionality:** Receive and review the output from Agent 2. Based on predefined criteria and the original user query, the Reviewer will make a decision:
        *   **Incomplete (Needs More Search):** If the results are insufficient or the task requires more information, request a new search from Agent 1 (potentially with a refined query).
        *   **Incomplete (Needs Revision):** If the output needs corrections or improvements, request revisions from Agent 2.
        *   **Complete:** If the output successfully addresses the user query and meets the quality criteria, signal task completion.
    *   **Output:** Feedback/instructions directed back to Agent 1 or Agent 2, or a final signal of task completion.

## Workflow Cycle

The agents will operate in a cycle:

User Query -> Agent 1 (Search) -> Agent 2 (Analyze) -> Agent 3 (Review) -> (Loop back to Agent 1 or Agent 2 based on Reviewer feedback) -> Task Completion or Time Limit Reached.

## Limits and Termination

*   **Time Limit:** The pipeline will have a predefined time limit. If the task is not completed within this limit, Agent 3 will stop the iteration process and provide the best available output from Agent 2 at that point.
*   **Completion Criteria:** Agent 3 will determine task completion based on whether Agent 2's output effectively addresses the original user query and meets quality standards.

## MVP Development Plan

To bootstrap our learning and get a basic end-to-end pipeline working quickly, the MVP will focus on:

1.  **Refining Agent 1 (Searcher):** Create a simple programmatic way to trigger a search using `search_tool.py` and process its JSON output.
2.  **Building a Basic Agent 2 (Analyzer):** Implement a minimal function to receive search results and perform a very basic transformation or extraction (e.g., just listing snippets).
3.  **Building a Placeholder Agent 3 (Reviewer):** Create a simple function that checks if Agent 2 produced any output and, if so, signals completion; otherwise, signals a need for more search (without complex analysis).
4.  **Creating a Simple Orchestration Flow:** Develop a core function or endpoint to initiate the process, call the basic agent functions in sequence, and include a simple time counter to simulate the time limit.
5.  **Basic Communication:** Use direct function calls or simple data passing between the basic agent functions for the MVP.

This MVP will provide a concrete foundation to test the basic agentic loop and identify key areas for future enhancement, such as more sophisticated NLP for Agent 2, advanced review logic for Agent 3, robust inter-agent communication, and dynamic prompt engineering.
