# Changelog

## [2.0.0-dev] — Unreleased

### Added
- Project scaffold with typed contracts (Pydantic models)
- Explicit state machine orchestrator with gate decisions
- SearchBackend protocol with 8 backend implementations (TODO)
- LLMClient protocol decoupling orchestration from OpenAI
- Append-only JSONL audit log per pipeline run
- CLI entry points: research, campaign, batch

### Changed
- Replaced implicit loop orchestration with PipelineMachine
- Replaced bare dicts with Pydantic contracts at all boundaries
- Dropped Google CSE; Brave Search API as primary web search
- Dropped Node.js scraper sidecar; Playwright-only content fetching

### Removed
- Flask web app (rebuilt as optional FastAPI in web/)
- Legacy v1/v2 API versions
- research.sh bash wrapper
