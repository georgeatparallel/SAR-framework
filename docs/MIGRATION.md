# Migrating from v1

v1 is the Flask + Gemini generation of this repository (the tree at tag
`v1-flask-gemini`). v2 is a rewrite with the same core loop and different
engineering. This page is the complete map.

## The decision on v1

v1 is retired. It stays reachable at the annotated tag `v1-flask-gemini` and
is not maintained. Issues against v1 will be closed with a pointer here.

## What stayed the same

- The Search, Analyze, Review loop, including the reviewer's three verdicts:
  approve, request rework with feedback, request more information with new
  queries.
- The feedback loop bound: a maximum number of iterations, after which the
  best available report is returned.
- The idea that the analyzer must cite sources inline and the reviewer checks
  it.

## What changed

| v1 | v2 |
|---|---|
| One 510-line `agent_service.py` | Seven agent modules, each with one `llm.call` |
| Decision logic inline in prose-like code | Typed gates (`sar/orchestrator/gates.py`) over a state machine (`machine.py`) |
| Google Gemini function calling | A client protocol (`sar/llm/client.py`) with two backends: OpenAI API and the local Codex CLI |
| Flask web app as the interface | A CLI (`sar research|batch|campaign`); the web layer is optional (`web/`, `[web]` extra) |
| Google Custom Search + Playwright scrape | Eight search backends behind one engine, deduplicated |
| Print-style logging | Append-only JSONL audit log per run |
| Untyped dicts between agents | Pydantic contracts (`sar/contracts/`), schemas generated from them |

## What was dropped

- The Gemini backend. The two supported backends are `openai` and `codex`.
  Adding a Gemini client back is one class implementing `LLMClient`.
- Google Custom Search as the primary search path, along with its
  `GOOGLE_API_KEY` / `SEARCH_ENGINE_ID` configuration.

## Environment variable map

| v1 | v2 |
|---|---|
| `GEMINI_API_KEY` | gone; `OPENAI_API_KEY` for the hosted backend |
| `GOOGLE_API_KEY`, `SEARCH_ENGINE_ID` | gone; backends are keyless except `BRAVE_API_KEY`, `UNPAYWALL_EMAIL` |
| n/a | `SAR_LLM_BACKEND` = `openai` (default) or `codex` |
| n/a | `SAR_CODEX_MODEL` (default `gpt-5.6-terra`) |
