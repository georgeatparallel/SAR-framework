# SAR Framework

**Search-Analyze-Review (SAR)** — An agentic AI pipeline framework built with Flask and Google's Gemini AI.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-3.x-green.svg)](https://flask.palletsprojects.com/)
[![Gemini AI](https://img.shields.io/badge/Gemini-1.5--flash-orange.svg)](https://ai.google.dev/)

## Overview

SAR Framework implements a three-agent orchestration model for automated information retrieval and analysis:

```
User Query → Searcher Agent → Analyzer Agent → Reviewer Agent → Final Response
                   ↑                                    │
                   └────────── Feedback Loop ───────────┘
```

### The Three Agents

| Agent | Role | Description |
|-------|------|-------------|
| 🔍 **Searcher** | Information Retrieval | Executes Google searches, fetches and parses HTML/PDF content |
| 📊 **Analyzer** | Synthesis & Analysis | Processes search results, generates structured reports with citations |
| ✅ **Reviewer** | Quality Control | Evaluates output quality, decides to approve, request rework, or request more information |

## Features

- **Agentic Pipeline**: Autonomous multi-agent workflow with feedback loops
- **Async Content Fetching**: Parallel HTML and PDF content retrieval using Playwright
- **Smart Iteration**: Up to 3 refinement cycles based on Reviewer feedback
- **Citation Tracking**: Automatic source citation and reference management
- **Structured Logging**: Comprehensive logging for debugging and monitoring
- **Conversation History**: Full pipeline execution tracking for transparency

## Quick Start

### Prerequisites

- Python 3.11+
- Virtual environment (`.venv`)
- API Keys (see [Configuration](#configuration))

### Installation

```bash
# Clone the repository
git clone https://github.com/ignaciourbina/SAR-framework.git
cd SAR-framework

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Configuration

Create a `.env` file in the project root:

```env
# Required: Google Gemini API Key
GEMINI_API_KEY=your_gemini_api_key

# Required: Google Custom Search API
GOOGLE_API_KEY=your_google_api_key
SEARCH_ENGINE_ID=your_search_engine_id

# Optional
FLASK_CONFIG=development
SECRET_KEY=your_secret_key
```

### Running the Server

**Development mode:**
```bash
./devserver.sh
```

**Test server (background with random port):**
```bash
./run_test_server.sh    # Start server
./kill_server.sh        # Stop server
```

### Usage

**Via Web Interface:**
Open `http://localhost:8080` in your browser.

**Via API:**
```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Latest developments in quantum computing 2024"}'
```

## Project Structure

```
SAR-framework/
├── app/
│   ├── __init__.py              # Flask app factory
│   ├── main/
│   │   ├── __init__.py          # Blueprint registration
│   │   └── routes.py            # API endpoints
│   ├── services/
│   │   ├── agent_service.py     # Agent orchestration logic
│   │   └── search_service.py    # Search and content fetching
│   ├── static/                  # Frontend assets
│   └── templates/               # HTML templates
├── docs/
│   ├── Blueprint.md             # Architecture vision
│   ├── alpha_dev_notes.md       # Development notes
│   └── conceptual_orchestration_workflow.md
├── logs/                        # Application logs
├── config.py                    # Flask configuration
├── main.py                      # Application entry point
├── PROMPTS.md                   # Agent system prompts
├── GEMINI.md                    # AI collaboration guidelines
└── requirements.txt             # Python dependencies
```

## API Reference

### `POST /search`

Triggers the agentic pipeline for a given query.

**Request:**
```json
{
  "query": "Your research question here"
}
```

**Response:**
```json
{
  "response": "Markdown-formatted analysis report",
  "references": ["https://source1.com", "https://source2.com"],
  "history": [
    {
      "iteration": 1,
      "steps": [...]
    }
  ]
}
```

## Pipeline Workflow

1. **Initialization**: Pipeline receives user query, sets max iterations (default: 3)
2. **Search Phase**: Searcher Agent fetches relevant web content
3. **Analysis Phase**: Analyzer Agent synthesizes information into a structured report
4. **Review Phase**: Reviewer Agent evaluates and decides next action:
   - `COMPLETE` → Return final response
   - `NEEDS_REWORK` → Loop back to Analyzer with feedback
   - `NEEDS_MORE_INFO` → Loop back to Searcher with new queries
5. **Termination**: Pipeline ends on approval or max iterations reached

## Documentation

| Document | Description |
|----------|-------------|
| [Blueprint.md](docs/Blueprint.md) | Architectural vision and MVP plan |
| [PROMPTS.md](PROMPTS.md) | Agent system prompts and behaviors |
| [GEMINI.md](GEMINI.md) | AI collaboration protocol |
| [Workflow Pseudocode](docs/conceptual_orchestration_workflow.md) | Pipeline logic in pseudocode |

## Development

### Running Tests

```bash
source .venv/bin/activate
python test_search.py
```

### Logging

Logs are written to:
- Console (stdout)
- `logs/app.log`

### Environment Configurations

| Config | Use Case |
|--------|----------|
| `development` | Local development with debug mode |
| `testing` | Automated testing |
| `production` | Production deployment |

## Dependencies

- **Flask[async]** - Async web framework
- **google-generativeai** - Gemini AI SDK
- **Playwright** - Browser automation for content fetching
- **BeautifulSoup4** - HTML parsing
- **PyMuPDF** - PDF text extraction
- **google-api-python-client** - Google Custom Search API

## Contributing

1. Follow the collaborative protocol defined in [GEMINI.md](GEMINI.md)
2. Use modular design principles
3. Maintain comprehensive logging
4. Test changes before committing

## License

This project is licensed under the MIT License.

---

**Built with ❤️ using Flask and Google Gemini AI**