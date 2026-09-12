# Agentic SAR v2 — Project Blueprint

## Design principles

1. **Contracts first** — every service boundary has a typed schema; no bare dicts cross a boundary
2. **Deterministic cycles** — the orchestrator is an explicit state machine, not a while-loop with implicit variables
3. **Transparent logs** — every gate decision, every search result score, every LLM call is logged to a structured audit trail
4. **Decoupled services** — search, LLM, orchestration, and persistence are independent; any can be swapped or mocked
5. **Fail loud, degrade intentionally** — no silent fallbacks; errors propagate with context; degradation is an explicit code path

---

## Folder structure

```
agentic_sar/
│
├── BLUEPRINT.md              ← this file (project constitution)
├── CHANGELOG.md              ← version history, dated entries
├── pyproject.toml            ← single source for deps, build, linting
├── .env.example              ← template; .env is gitignored
│
├── docs/
│   ├── adr/                  ← Architecture Decision Records (ADR-NNN-title.md)
│   │   ├── ADR-001-multi-service-split.md
│   │   ├── ADR-002-search-backend-selection.md
│   │   └── ...
│   ├── contracts/            ← human-readable contract docs (rendered from schemas)
│   └── runbooks/             ← operational how-tos (run batch, add a search backend, etc.)
│
├── schemas/                  ← machine-readable interface contracts (JSON Schema / Pydantic exports)
│   ├── search_result.json
│   ├── pipeline_state.json
│   ├── campaign_spec.json
│   ├── dossier_output.json
│   └── llm_request.json
│
├── src/
│   ├── sar/                  ← importable Python package
│   │   ├── __init__.py
│   │   ├── config.py         ← single AppConfig (pydantic BaseSettings), validated at startup
│   │   │
│   │   ├── contracts/        ← Pydantic models matching schemas/
│   │   │   ├── __init__.py
│   │   │   ├── search.py     ← SearchResult, SearchRequest, SearchResponse
│   │   │   ├── pipeline.py   ← PipelineState, IterationRecord, GateDecision
│   │   │   ├── campaign.py   ← CampaignSpec, CampaignDossier, CodedRow, EvidenceEntry
│   │   │   ├── llm.py        ← LLMRequest, LLMResponse, ToolCall, ToolResult
│   │   │   └── events.py     ← Event, EventType (enum), EventPayload
│   │   │
│   │   ├── search/           ← search service (no LLM dependency)
│   │   │   ├── __init__.py
│   │   │   ├── engine.py     ← SearchEngine class: register backends, fan-out, deduplicate
│   │   │   ├── backends/     ← one file per backend, each implements SearchBackend protocol
│   │   │   │   ├── __init__.py
│   │   │   │   ├── _protocol.py      ← SearchBackend Protocol class
│   │   │   │   ├── semantic_scholar.py
│   │   │   │   ├── arxiv.py
│   │   │   │   ├── crossref.py
│   │   │   │   ├── pubmed.py
│   │   │   │   ├── unpaywall.py       ← enrichment-only backend
│   │   │   │   ├── brave.py           ← Brave Search API (primary web search)
│   │   │   │   ├── duckduckgo.py      ← fallback web search (no key)
│   │   │   │   └── wikipedia.py
│   │   │   ├── fetch.py      ← ContentFetcher: URL→text (HTTP, PDF, Playwright)
│   │   │   └── cache.py      ← URLCache (sqlite, TTL-based)
│   │   │
│   │   ├── llm/              ← LLM gateway service (no search dependency)
│   │   │   ├── __init__.py
│   │   │   ├── client.py     ← LLMClient Protocol + OpenAIClient implementation
│   │   │   ├── tools.py      ← tool schema definitions (JSON dicts)
│   │   │   ├── retry.py      ← backoff, circuit breaker (moved from retry_utils)
│   │   │   └── budget.py     ← token/cost tracker per pipeline run
│   │   │
│   │   ├── agents/           ← agent definitions (stateless functions, depend on contracts only)
│   │   │   ├── __init__.py
│   │   │   ├── query_expander.py
│   │   │   ├── source_judge.py
│   │   │   ├── reader.py             ← generic StructuredReader (parametrized by extraction schema)
│   │   │   ├── analyzer.py
│   │   │   ├── reviewer.py
│   │   │   └── campaign_synthesizer.py
│   │   │
│   │   ├── orchestrator/     ← pipeline orchestration (state machine)
│   │   │   ├── __init__.py
│   │   │   ├── machine.py    ← PipelineMachine: states, transitions, gates
│   │   │   ├── gates.py      ← gate functions (quality threshold, budget check, max-iter)
│   │   │   └── runner.py     ← run_pipeline(config, initial_state) → FinalResult
│   │   │
│   │   ├── persistence/      ← session storage, audit log
│   │   │   ├── __init__.py
│   │   │   ├── sessions.py   ← SessionStore (sqlite)
│   │   │   ├── audit.py      ← AuditLog: append-only structured log per run
│   │   │   └── citations.py  ← CitationStore + formatters (APA, BibTeX)
│   │   │
│   │   ├── campaign/         ← campaign-specific logic (pluggable domain)
│   │   │   ├── __init__.py
│   │   │   ├── schema.py     ← codebook definitions, allowed values, field metadata
│   │   │   ├── validators.py ← normalize_trivalued, normalize_repression, etc.
│   │   │   └── batch.py      ← batch runner, diff generation, manifest
│   │   │
│   │   └── events.py         ← EventBus: publish/subscribe for SSE, CLI, logging
│   │
│   └── cli/                  ← CLI entry points
│       ├── research.py       ← generic research CLI
│       ├── campaign.py       ← campaign research CLI
│       └── batch.py          ← batch verification CLI
│
├── tests/                    ← mirrors src/sar/ structure
│   ├── conftest.py           ← shared fixtures (mock LLM, mock search, tmp cache)
│   ├── unit/
│   │   ├── test_contracts.py ← round-trip serialization, validation edge cases
│   │   ├── test_search_engine.py
│   │   ├── test_backends/
│   │   │   ├── test_semantic_scholar.py
│   │   │   └── ...
│   │   ├── test_agents/
│   │   │   ├── test_query_expander.py
│   │   │   └── ...
│   │   ├── test_gates.py
│   │   └── test_validators.py
│   ├── integration/
│   │   ├── test_search_live.py       ← hits real APIs (marked slow)
│   │   ├── test_pipeline_e2e.py      ← full pipeline with mock LLM
│   │   └── test_campaign_e2e.py
│   └── fixtures/
│       ├── search_results.json       ← canned search responses
│       ├── llm_responses.json        ← canned LLM outputs
│       └── campaign_specs.json
│
└── web/                      ← optional web UI (separate concern)
    ├── server.py             ← Flask/FastAPI app (imports from src/sar/)
    ├── routes.py
    ├── templates/
    └── static/
```

---

## Naming conventions

| Layer | File pattern | Class/function pattern | Example |
|-------|-------------|----------------------|---------|
| Contracts | `src/sar/contracts/{domain}.py` | PascalCase Pydantic models | `SearchResult`, `PipelineState` |
| Backends | `src/sar/search/backends/{name}.py` | `class {Name}Backend(SearchBackend)` | `class BraveBackend(SearchBackend)` |
| Agents | `src/sar/agents/{role}.py` | `async def run(ctx: AgentContext) -> T` | `query_expander.run(ctx)` |
| Gates | `src/sar/orchestrator/gates.py` | `def check_{name}(state) -> GateDecision` | `check_quality_threshold(state)` |
| Tests | `tests/unit/test_{module}.py` | `test_{behavior}_when_{condition}` | `test_dedup_when_same_doi` |
| ADRs | `docs/adr/ADR-{NNN}-{slug}.md` | — | `ADR-002-search-backend-selection.md` |

---

## Code style: docstrings and signatures

Every public function, class, and method MUST have:

1. **A Google-style docstring** with: one-line summary, Args, Returns, Raises sections.
2. **Full type annotations** on all parameters and return values. No `Any` unless unavoidable.
3. **A brief module-level docstring** at the top of every `.py` file explaining what the module owns.

Example (canonical form):

    """One-line summary of what the function does.

    Longer description if the behavior is non-obvious or has edge cases.

    Args:
        query: The user's natural-language research question.
        limit: Maximum results per backend. Defaults to 10.

    Returns:
        Deduplicated search results sorted by relevance score.

    Raises:
        SearchBackendError: If all backends fail simultaneously.
    """

Rules:
- Docstrings are the **contract documentation** — they are not optional decoration.
- If a function's behavior cannot be explained in its docstring, the function is too complex. Split it.
- Private helpers (`_foo`) get a one-liner docstring minimum.
- Class docstrings describe the **responsibility** of the class, not its implementation.
- `# TODO(component):` prefix for trackability — e.g., `# TODO(search): implement dedup by DOI`.

---

## Key architectural contracts

### SearchBackend protocol

```python
class SearchBackend(Protocol):
    name: str
    requires_key: bool

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...
    async def healthcheck(self) -> bool: ...
```

Every backend implements this. The `SearchEngine` registers backends at startup, fans out
queries in parallel, deduplicates by URL and DOI, and returns `SearchResponse`.

### PipelineState (explicit state machine)

```python
class PipelinePhase(str, Enum):
    EXPAND   = "expand"
    SEARCH   = "search"
    JUDGE    = "judge"
    READ     = "read"
    ANALYZE  = "analyze"
    REVIEW   = "review"
    COMPLETE = "complete"
    FAILED   = "failed"

@dataclass
class PipelineState:
    phase: PipelinePhase
    iteration: int
    queries: list[str]
    results: list[SearchResult]
    summaries: list[PaperSummary]
    report: str | None
    feedback: str | None
    gate_log: list[GateDecision]     # every gate pass/fail is recorded
    budget: TokenBudget
```

Transitions are explicit: `EXPAND → SEARCH → JUDGE → READ → ANALYZE → REVIEW → {COMPLETE | EXPAND}`.
Each transition passes through a gate function that returns `GateDecision(passed=bool, reason=str)`.

### GateDecision (transparent control flow)

```python
@dataclass
class GateDecision:
    gate_name: str
    passed: bool
    reason: str
    metrics: dict[str, Any]   # e.g. {"quality_score": 7.2, "threshold": 6.0}
    timestamp: datetime
```

Every gate decision is appended to `state.gate_log` and written to the audit trail.
No implicit control flow — if the reviewer says "needs more info", the gate log shows exactly why.

### LLMClient protocol

```python
class LLMClient(Protocol):
    async def call(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        model: str | None = None,
    ) -> LLMResponse: ...
```

One implementation wraps OpenAI. Swapping to Anthropic or a local model means writing
a second implementation — no orchestrator changes.

### AuditLog (transparent logging)

```python
class AuditLog:
    def __init__(self, run_id: str, path: Path): ...
    def record(self, event_type: str, data: dict): ...
    def dump(self) -> list[dict]:  # returns full log as JSON-serializable list
```

Append-only JSONL file per run. Every LLM call, search query, gate decision, and error
gets a timestamped entry. This replaces scattered `logger.info()` calls with a single
structured audit trail that can be replayed or inspected.

---

## Service boundaries

```
┌────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                         │
│  PipelineMachine (state transitions + gates)           │
│  Depends on: contracts only                            │
│  Calls: search service, llm service, persistence       │
└──────┬──────────────┬──────────────┬───────────────────┘
       │              │              │
       ▼              ▼              ▼
┌─────────────┐ ┌──────────┐ ┌──────────────────┐
│   SEARCH    │ │   LLM    │ │   PERSISTENCE    │
│ SearchEngine│ │ LLMClient│ │ SessionStore     │
│ + backends  │ │ + retry  │ │ AuditLog         │
│ + fetcher   │ │ + budget │ │ URLCache         │
│ + cache     │ │          │ │ CitationStore    │
└─────────────┘ └──────────┘ └──────────────────┘
       │              │              │
       └──────────────┴──────────────┘
              communicate via
            contracts/ models only
```

No service imports another service's internals. They exchange Pydantic models.

---

## ADR format (docs/adr/)

Each decision record follows:

```markdown
# ADR-NNN: Title

**Status:** accepted | superseded | deprecated
**Date:** YYYY-MM-DD
**Context:** Why this decision was needed.
**Decision:** What we chose.
**Consequences:** What follows from this choice.
```

Initial ADRs to write:
- `ADR-001-multi-service-split.md` — why search/llm/orchestration are separate services
- `ADR-002-search-backend-selection.md` — Brave as primary web, academic APIs unchanged
- `ADR-003-state-machine-orchestration.md` — explicit phases vs implicit loop
- `ADR-004-contracts-pydantic.md` — why Pydantic, why JSON Schema export
- `ADR-005-audit-log.md` — JSONL append-only per run

---

## Migration path from v1

### Keep as-is (proven code)
- `cache_service.py` → refactor into `src/sar/search/cache.py` + `src/sar/persistence/sessions.py`
- `citation_service.py` → `src/sar/persistence/citations.py`
- `retry_utils.py` → `src/sar/llm/retry.py`
- `academic_sources.py` → split into individual `src/sar/search/backends/*.py` files
- Tool-based function calling pattern (JSON tool schemas + dispatcher)

### Rewrite
- `agent_service.py` → decompose into `agents/` (stateless) + `orchestrator/` (state machine)
- `campaign_service.py` → extract codebook into `campaign/schema.py`, reuse generic agents
- `search_service.py` → split into `search/engine.py` (coordination) + `search/fetch.py` (content)
- `config.py` → Pydantic `BaseSettings` with validation

### Delete
- `scraper-service/` (Node.js sidecar) — replace with Playwright-only fetcher
- `services/` (legacy tools) — not part of v2
- Flask web app in `app/` — optional, rebuilt in `web/` if needed
- `research.sh` — replaced by `cli/research.py`

---

## Search backend strategy

**Primary web search:** Brave Search API (free: 2,000 queries/month, no Chromium needed)
**Fallback web search:** DuckDuckGo HTML (no key, no rate limit, lower quality)
**Academic (unchanged):** Semantic Scholar, arXiv, CrossRef, PubMed, Unpaywall
**Knowledge:** Wikipedia API

Google CSE is dropped. Brave provides equivalent web coverage with a proper REST API,
structured JSON responses, and no browser automation.

---

## What this file governs

This blueprint is the **project constitution**. Code that contradicts it is a bug.
Decisions that change it get an ADR first. The folder structure, naming conventions,
and contract shapes defined here are the shared vocabulary between all contributors
(human and AI).
