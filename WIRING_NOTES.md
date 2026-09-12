# agentic_sar v2 — wiring notes (2026-05-19)

## What this port did

Brought `agentic_sar_new` (the v2 redesign that lived in
`.archieve.zip/tools/agentic_sar_new/`) to top-level
`tools/agentic_sar_v2/` and wired the three CLI entry points that
were stubbed with `pass`/TODO comments at archival time.

## CLI entry points

All three are invoked via `python -m cli.<name>` from inside
`tools/agentic_sar_v2/` with `PYTHONPATH=src`:

- `cli.campaign` — single-campaign dossier extraction, drop-in replacement
  for v1's `campaign_research.py`. Args:
  `--campaign-name --country --year-start [--year-end] [--region]
   [--notes] [--save-dir] [--focus-fields]`.
- `cli.research` — generic literature research, mirrors v1's `research.py`.
  Args: `query [-v] [-c CONTEXT] [--save-report PATH]
  [--max-iterations N] [--backends a,b,c]`.
- `cli.batch` — batch campaign verification across a master CSV,
  mirrors v1's `run_campaign_batch_verification.py`. Args:
  `--dataset PATH --output-root PATH [--slug | --country | --limit
  --offset | --overwrite | --dry-run | --rebuild-index]`.

Each CLI loads `.env` from the v2 root (env vars are prefixed `SAR_`,
per `src/sar/config.py`'s `AppConfig`).

## Bug fixes the wiring exposed

Smoke testing `cli.campaign` on the Iran 2017 case surfaced four
issues already present in the archived v2 codebase. Fixed in this
port:

1. **`runner.run_campaign_pipeline` constructed `SearchRequest()`
   without a query string** (the line ``req = SearchRequest()`` had a
   stale comment "Set attributes that exist on the SearchRequest stub"
   from a refactor in progress). Pydantic v2 makes `query` required,
   so the whole SEARCH phase aborted silently with
   `state.results = []`. Now constructs `SearchRequest(query=q)`.

2. **`run_campaign_pipeline` did `campaign_spec.get("year_start", "")`
   as a fallback** after `getattr(campaign_spec, "year_start", "")`,
   crashing on pydantic models which have no `.get` method, and using
   the wrong field name (`CampaignSpec.year`, not `year_start`).
   Replaced with a `_spec_get` helper that supports both pydantic
   models and dicts and tolerates alias names.

3. **Reader returns flat dicts; runner constructed `PaperSummary`
   with the wrong kwargs.** The contract is
   `PaperSummary(source: SearchResult, extraction: dict, quality_score
   float, notes: str)`, not flat fields. Runner now rebuilds a
   `SearchResult` (re-using the original by URL when possible) and
   wraps the extraction dict.

4. **Three `_emit_update` helpers built `Event()` with no args, then
   assigned `event.type = ...`.** Event requires `event_type` at
   construction and uses the field name `event_type` (not `type`).
   Also `EventType.AGENT_UPDATE` was missing from the enum. Fixed
   all three call sites and added `AGENT_UPDATE` to the enum.

5. **`LLMRequest.messages` was required at construction** but
   `query_expander`, `source_judge`, and `reader` did
   `LLMRequest(); request.messages = [...]` — which fails at
   construction. Made `messages` default to `[]` and switched
   `query_expander` to pass `messages=` at construction.

6. **`OpenAIClient` only accepted `ToolDefinition` objects**, but
   `src/sar/llm/tools.py` returns OpenAI-format raw dicts. Added a
   coercion: the client now accepts both shapes.

7. **`max_tokens` / `temperature` for reasoning models.** GPT-5 and
   `o*` reasoning models reject `max_tokens` (use
   `max_completion_tokens` instead) and reject custom temperature.
   `OpenAIClient.call` now detects reasoning model prefixes and routes
   parameters correctly; also bumps `max_completion_tokens` to ≥8192
   to leave room for the hidden reasoning trace plus the visible tool
   call.

## What still needs work

These were noticed during the smoke but not fixed (out of CLI-wiring
scope):

- **`sar.search.fetch.ContentFetcher` is still a stub.** Its
  `__init__` and `fetch` bodies are `pass` with TODO comments. v2
  currently runs the reader on `SearchResult.snippet` only, so the
  dossier is thinner than v1's (v1 uses full HTML/PDF text via
  Playwright). Wire by porting `agentic_sar/services/search_service.py`
  `fetch_and_parse_html_async()` and `fetch_and_parse_pdf()`.
- **Backend rate limits / 403s:** Wikipedia returns 403 (needs
  `User-Agent`), arXiv and Semantic Scholar return 429 (needs
  backoff/throttling). Only DuckDuckGo and CrossRef are reliable
  without keys. CrossRef alone is sufficient for academic citations;
  for press coverage, swap DuckDuckGo for Brave or wire a Google CSE
  backend.
- **`duckduckgo_search` is deprecated** — pip wants `ddgs`. Either
  upgrade the import or pin `duckduckgo_search<=8.x`.
- **GPT-5 reasoning models sometimes return `tool_calls=[]`** even
  with `tool_choice="function:name"`. The likely fix is to switch to
  the `responses.create` endpoint (which is the recommended path for
  reasoning + tools) and/or supply explicit `reasoning_effort=low`.
  Without this, the campaign synthesizer occasionally fails and the
  pipeline degrades to a stub dossier.
- **Tests are scaffolds** (`pass`/TODO bodies in
  `tests/integration/test_campaign_e2e.py`,
  `tests/unit/test_backends/test_semantic_scholar.py`, etc.). Wire
  them once the runtime is stable.

## How to run

```
# 1. Put a working OpenAI key into tools/agentic_sar_v2/.env (prefix SAR_)
cd tools/agentic_sar_v2

# 2. Single campaign:
PYTHONPATH=src ../../venv/bin/python -m cli.campaign \
    --campaign-name "Iran Dey Protests" --country "Iran" \
    --year-start 2017 --year-end 2018 --region "MENA" \
    --save-dir /tmp/iran_v2

# 3. Generic research:
PYTHONPATH=src ../../venv/bin/python -m cli.research -v \
    "Twitter bot prevalence empirical 2017"

# 4. Batch verification (dry-run first):
PYTHONPATH=src ../../venv/bin/python -m cli.batch \
    --dataset ../../external_data/campaigns/master_dataset.csv \
    --output-root /tmp/batch_v2 --dry-run --limit 5
```

For most fact-checking work today, **prefer the v1 port at
`tools/agentic_sar/`** — it has the full Playwright fetcher and was
verified end-to-end on the Iran 2017 weak-duration case. Move to v2
once `ContentFetcher` and the GPT-5 tool-calling issue are resolved.
