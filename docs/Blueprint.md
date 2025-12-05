# SAR Framework — Architecture Blueprint

## Table of Contents

- [Vision](#vision)
- [Architecture Overview](#architecture-overview)
- [Agent Specifications](#agent-specifications)
- [Pipeline Workflow](#pipeline-workflow)
- [Technical Implementation](#technical-implementation)
- [MVP Development Plan](#mvp-development-plan)
- [Future Enhancements](#future-enhancements)

---

## Vision

The SAR (Search-Analyze-Review) Framework is an agentic AI pipeline that orchestrates multiple specialized agents to automate complex information retrieval and analysis tasks. Given a search task, the system:

1. **Searches** for relevant information across the web
2. **Analyzes** and synthesizes findings into structured reports
3. **Reviews** output quality and iterates until completion

This approach mimics a research team's workflow — breaking down complex tasks into specialized roles that collaborate through defined interfaces.

---

## Architecture Overview

### High-Level Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Searcher  │────▶│  Analyzer   │────▶│  Reviewer   │
│    Agent    │     │    Agent    │     │    Agent    │
└─────────────┘     └─────────────┘     └─────────────┘
       ▲                                       │
       │                                       ▼
       │            ┌─────────────┐      ┌──────────┐
       └────────────│ Orchestrator│◀─────│ Decision │
                    └─────────────┘      └──────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   Output    │
                    └─────────────┘
```

### Component Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Web Framework** | Flask (async) | API endpoints, request handling |
| **AI Model** | Gemini 1.5 Flash | Agent reasoning and generation |
| **Search** | Google Custom Search API | Web information retrieval |
| **Content Fetch** | Playwright, httpx | HTML/PDF content extraction |
| **Parsing** | BeautifulSoup, PyMuPDF | Content processing |

---

## Agent Specifications

### Agent 1: The Searcher

**Goal:** Accurate and relevant information retrieval.

| Attribute | Description |
|-----------|-------------|
| **Input** | Search query string |
| **Process** | Execute Google search, fetch page content (HTML/PDF), parse text |
| **Output** | Structured results with title, link, snippet, and full content |
| **Async** | Yes — parallel content fetching with Playwright |

**Capabilities:**
- Google Custom Search API integration
- Concurrent URL content fetching
- PDF text extraction via PyMuPDF
- HTML parsing with multiple fallback strategies
- URL deduplication across iterations

### Agent 2: The Analyzer

**Goal:** Synthesize retrieved content into coherent, cited analysis.

| Attribute | Description |
|-----------|-------------|
| **Input** | Search results, original query, optional feedback |
| **Process** | Analyze content, generate structured report, cite sources |
| **Output** | Markdown report with APA citations and source URLs |
| **Tool** | `submit_report(report, citations_used)` |

**Capabilities:**
- Evidence triage and prioritization
- Theme identification and structuring
- Comparative analysis with summary tables
- In-line citation generation
- Error handling for failed source fetches

### Agent 3: The Reviewer

**Goal:** Quality control and workflow direction.

| Attribute | Description |
|-----------|-------------|
| **Input** | Analysis output, original query, iteration status |
| **Process** | Evaluate completeness, accuracy, and relevance |
| **Output** | Decision function call |
| **Tools** | `mark_as_complete()`, `request_rework(feedback)`, `request_more_info(queries)` |

**Decision Matrix:**

| Condition | Action | Effect |
|-----------|--------|--------|
| Answer is complete and accurate | `mark_as_complete()` | Pipeline terminates successfully |
| Answer has correctable flaws | `request_rework(feedback)` | Loops to Analyzer with feedback |
| Insufficient source data | `request_more_info(queries)` | Loops to Searcher with new queries |
| Final round + acceptable | `mark_as_complete()` | Forced completion |
| Final round + critical flaws | `request_rework(feedback)` | One last correction attempt |

---

## Pipeline Workflow

### Pseudocode Logic

```
PROCEDURE run_pipeline(query):
    max_iterations ← 3
    search_results ← empty
    feedback ← null
    
    FOR iteration FROM 1 TO max_iterations:
        
        IF feedback IS null:
            search_results ← SEARCH(query OR new_queries)
        
        analysis ← ANALYZE(query, search_results, feedback)
        feedback ← null
        
        decision ← REVIEW(query, analysis, is_final=iteration==max)
        
        SWITCH decision:
            CASE COMPLETE:
                RETURN analysis
            CASE NEEDS_REWORK:
                feedback ← decision.payload
            CASE NEEDS_MORE_INFO:
                new_queries ← decision.payload
    
    RETURN analysis  // Max iterations reached
```

### State Management

| State Variable | Purpose |
|----------------|---------|
| `current_queries` | Query/queries for next search |
| `search_results` | Accumulated search data |
| `feedback` | Rework instructions from Reviewer |
| `conversation_history` | Full execution trace |
| `iteration` | Current loop count |

---

## Technical Implementation

### Directory Structure

```
app/services/
├── agent_service.py      # Agent logic + orchestration
│   ├── searcher_agent()
│   ├── analyzer_agent()
│   ├── reviewer_agent()
│   ├── tool_parser_agent()
│   ├── aggregate_search_results()
│   └── run_agentic_pipeline()
│
└── search_service.py     # Low-level search operations
    ├── perform_google_search()
    ├── fetch_and_parse_html_async()
    ├── fetch_and_parse_pdf_async()
    └── search_and_fetch_content()
```

### Key Design Decisions

1. **Function Calling for Decisions**: Reviewer uses Gemini function calling to ensure structured, parseable decisions.

2. **Centralized Content Fetching**: All URL fetching happens in a single async batch to maximize parallelism.

3. **Conversation History**: Full pipeline trace is returned for debugging and transparency.

4. **Graceful Degradation**: Pipeline completes with best available output if max iterations reached.

---

## MVP Development Plan

### ✅ Completed

- [x] Google Search integration with content fetching
- [x] Flask application structure with blueprints
- [x] Searcher Agent implementation
- [x] Analyzer Agent with tool-based submission
- [x] Reviewer Agent with function calling
- [x] Pipeline orchestration with iteration loop
- [x] Async content fetching (HTML + PDF)
- [x] Conversation history tracking

### 🔜 Next Steps

- [ ] Improve error handling for edge cases
- [ ] Add request timeout and rate limiting
- [ ] Implement caching for repeated queries
- [ ] Add unit tests for agent functions
- [ ] Create API documentation with examples

---

## Future Enhancements

### Short-term

- **Enhanced NLP**: Summarization, entity extraction in Analyzer
- **Source Credibility**: Ranking sources by authority
- **Multi-modal**: Image/chart analysis from search results

### Long-term

- **Custom Agent Plugins**: Allow user-defined agents
- **Streaming Responses**: Real-time pipeline progress
- **Agent Memory**: Cross-session learning and context
- **Multi-LLM Support**: Swap in different AI providers

---

## References

- [Flask Documentation](https://flask.palletsprojects.com/)
- [Google Gemini API](https://ai.google.dev/docs)
- [Google Custom Search API](https://developers.google.com/custom-search)
- [Playwright Python](https://playwright.dev/python/)
