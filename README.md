# SAR Framework

A prototype agentic AI pipeline that automates web research using three cooperating agents: Search, Analyze, and Review. Built with Flask and Google Gemini.

This is a research/learning project, not production software. The API keys are not included; you need to supply your own.

## How it works

```
User Query -> Searcher Agent -> Analyzer Agent -> Reviewer Agent -> Final Report
                  ^                                    |
                  +------------ Feedback Loop ---------+
```

1. **Searcher** runs a Google Custom Search, then fetches and parses the content of each result (HTML via Playwright, PDFs via PyMuPDF).
2. **Analyzer** sends the collected content to Gemini, which produces a cited Markdown report using function calling (`submit_report`).
3. **Reviewer** evaluates the report via Gemini function calling and decides to approve it, request a rework (loops back to Analyzer with feedback), or request more information (loops back to Searcher with new queries).

The pipeline runs up to 3 iterations before returning the best available result.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Create a `.env` file:

```
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_API_KEY=your_google_api_key
SEARCH_ENGINE_ID=your_search_engine_id
```

## Running

Development server:

```bash
./devserver.sh
```

Or start a background test server on a random port:

```bash
./run_test_server.sh
./kill_server.sh        # to stop it
```

Open `http://localhost:8080` for the web UI, or call the API directly:

```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Your research question here"}'
```

## Project structure

```
SAR-framework/
  app/
    __init__.py             # Flask app factory
    main/
      __init__.py           # Blueprint registration
      routes.py             # API endpoints (GET /, POST /search)
    services/
      agent_service.py      # Agent definitions and orchestration
      search_service.py     # Google Search, HTML/PDF fetching
    static/main.js          # Frontend JS
    templates/index.html    # Web UI
  docs/                     # Architecture notes, workflow diagrams
  config.py                 # Flask configuration (dev/test/prod)
  main.py                   # Entry point
  requirements.txt
```

## API

### POST /search

Request:

```json
{"query": "Your research question"}
```

Response:

```json
{
  "response": "Markdown report with citations",
  "references": ["https://source1.com", "https://source2.com"],
  "history": [{"iteration": 1, "steps": [...]}]
}
```

## Dependencies

- Flask (async) for the web layer
- google-generativeai (Gemini 1.5 Flash) for agent reasoning
- Playwright for headless browser content fetching
- BeautifulSoup4 + lxml for HTML parsing
- PyMuPDF for PDF text extraction
- google-api-python-client for Google Custom Search
- httpx for async HTTP requests (PDF downloads)

## License

MIT
