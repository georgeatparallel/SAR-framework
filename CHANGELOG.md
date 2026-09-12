# Changelog

## 2.0.0 (unreleased)

The v2 rewrite becomes the repository's main line; the Flask + Gemini
generation is retired at tag `v1-flask-gemini` (see `docs/MIGRATION.md`).

### Provenance: the guarantee now holds on the main pipeline

The README's promise -- that SAR "refuses to state a claim it cannot trace to a
document" -- did not hold, for three independent reasons. Each is fixed, and
each now has a regression test that fails against the previous code.

- **The analyzer never received the claims or the quotes.** The runner stored
  `key_claims`/`key_quotes` (PAPER_SUMMARY_SCHEMA, the schema the reader is
  actually given) while `analyzer._format_summaries` read `key_findings`/
  `summary` (DEFAULT_SCHEMA, dead). Every source block reached the model as
  `Key claims: N/A` with no quotes, while the prompt instructed it to cite
  `[Source N]` per claim. `campaign_synthesizer` had the identical mismatch,
  collapsing dossier evidence to a title and a URL.
- **No document text was ever fetched.** `ContentFetcher` was seven `pass`
  bodies and was never instantiated; `SearchEngine` stored a fetcher and never
  called it; `SearchResult` had no `content` field. Every claim derived from a
  search snippet. The fetcher is implemented (HTTP → Playwright → snippet, with
  BeautifulSoup and PyMuPDF extraction), `SearchResult.content` exists, the
  engine populates it, and `URLCache` -- 148 implemented but unreachable lines
  -- is now its cache.
- **The reviewer approved by default.** It was the only agent that did not set
  `tool_choice`, and a missing or unrecognised tool call returned "complete".
  It now forces a tool call and raises `ReviewerError` instead; the runner
  fails the run rather than accepting an unreviewed report.

### Fixed

- `relevance_score` was constrained to `[0, 1]` while the source judge assigned
  0-10 against `QUALITY_THRESHOLD=6.0`. Pydantic does not validate on
  assignment, so every real score violated the contract silently. The contract
  now matches the runtime scale and the judge clamps model output into it.
- Batch campaign synthesis crashed on every run: the batch spec is a
  `SimpleNamespace` carrying `year_start`/`notes`, while the synthesizer read
  `.year`/`.description`/`.priority_fields`, raising `AttributeError` that
  became an empty "Synthesis failed" dossier.
- `duckduckgo_search` was imported by the default-registered DuckDuckGo backend
  and **declared nowhere**, so on a clean install the import failed, the engine
  swallowed it, and web/press coverage vanished from every run. Migrated to the
  renamed `ddgs` package (old name still honoured) and declared as a dependency.
- The reader fabricated `relevance_score = 3` when extraction failed,
  indistinguishable from a real judgement. Failures are now marked
  `extraction_failed` and scored 0.
- Gate decisions never reached the audit log, though BLUEPRINT.md and the
  README both said they did -- a run's JSONL could not explain why it ended.
- Search telemetry was discarded: `backends_failed` was populated and read by
  nothing, so a silently absent backend left no trace. It is now audited.
- Every `llm_call` was attributed to the client class, so cost and tokens could
  not be traced to an agent, phase, or iteration.
- `sar doctor` never loaded `.env`, so it reported missing keys and exited 1 in
  directories where `sar research` worked. It now loads `.env`, reports the
  version, search credentials, `ddgs`, Playwright browser binaries, and cache
  and audit-log writability -- and creates no directories while doing so.

### Added

- **Bibliographies.** `persistence/citations.py` was fully implemented and
  imported by nothing, so reports carried inline `[Source N]` markers with no
  reference list behind them and there was no way to export one. It is now
  wired: `run_pipeline` builds a `CitationStore` from the sources behind the
  report, appends an APA reference list *after* REVIEW (so the reviewer still
  judges the analyzer's own prose), and returns structured records on the new
  `PipelineResult.citations`. `sar research --bibtex PATH` writes BibTeX.
  Sources are deduplicated by DOI, falling back to URL.

### Resilience and performance

- Search backends had no retry and no rate limiting despite documented 429s
  from arXiv and Semantic Scholar. `call_with_backoff` and `CircuitBreaker`
  existed but were wired to nothing; the engine now uses them, with per-backend
  concurrency limits. `OpenAIClient` had no retry at all and now has one.
- The runner issued its expanded queries serially, making SEARCH cost the sum
  of per-query latencies rather than the slowest. Queries now run concurrently.
- Backend registration was triplicated across the three CLIs and the web layer,
  which had drifted into querying different sources for the same question. All
  four now use one `build_search_engine`.
- The orchestrator reached into `SearchEngine._deduplicate`; the engine exposes
  a public `deduplicate` for cross-query merging.

### Validation

- Coverage measured package-wide rather than over two subpackages: **66% → 79%**,
  with the CI floor raised accordingly. The previous floor covered only
  `sar.llm` and `sar.orchestrator`, so the modules that carried every defect
  above -- the backends, the CLI entry points, the fetcher -- sat at 0% behind a
  green build.
- New suites for the content fetcher, the CLI construction surface, and an
  offline contract suite covering all eight search backends (only one had a
  unit test). Test count **474 → 573**.
- `mypy src/sar` added to CI and passing; three latent type errors fixed along
  the way (an unguarded `.text` on an optional XML node, `hasattr`-narrowed
  `model_dump`, and a mis-typed `asyncio.gather` result).
- Two tests had pinned defects as intended behaviour -- that `relevance_score`
  rejects the scale the judge assigns, and that gate decisions are *absent*
  from the audit log. Both corrected.

### Packaging

- Added `py.typed` (the package is annotated and now type-checked, but was
  advertised as untyped), a `constraints.txt` for reproducible installs, and
  the fetcher's parsers to the `dev` extra so CI exercises the real path.
- `.coverage` was tracked in git; removed and ignored.
- `[project.urls] Homepage` pointed at a repository that is not the remote.
- `.env.example` documented three variables using the wrong prefix and omitted
  every search credential, making the keyed backends undiscoverable. It now
  documents all of them and explains the `SAR_` prefix rule.

### Carried forward from v1

- The per-agent prompt specification document (`docs/PROMPTS.md`) and the
  orchestration workflow document (`docs/ORCHESTRATION.md`), both rewritten
  against the v2 code so that every quoted prompt and every decision rule is
  the one the code actually uses.
- The pipeline flow visualization (`docs/flow_visualization.html`).
- The README's shape: honest scope, diagram, numbered walk-through.

### Added

- `check_claim`/`check_synthesis`/`assert_synthesis` take `require_quote`,
  and `synthesize.py` exposes it as `--require-quotes`. The default gate
  verifies a quote when one is present; a claim with no quote names a real
  file and passes, which makes it unfalsifiable rather than verified. The
  flag refuses those.

### Fixed

- The literature-map example asked the model to assess each citation gap by
  its bare identifier (`INTRO-P4-S2`) and never sent the paper's claim text,
  so the model inferred what the gap asserted. In the first live acceptance
  run it inferred wrong on five gaps out of ten, producing verdicts that were
  fully provenance-clean and answered the wrong question. `synthesize.py`
  now takes `--gaps`, the digest carries the claim it judged, and a gap left
  undefined is declared as such in the prompt, the run summary, the audit log
  and the rendered markdown rather than guessed at.
- Claims that failed the provenance gate were identified by substring-matching
  the problem strings. A claim carrying no quote used `""` as its needle, and
  `""` is a substring of every string, so one fabricated claim caused every
  quoteless claim in the same unit to be dropped with it. Failing claims are
  now identified by re-checking each claim.
- A claim dict missing a required field raised `ValidationError` out of
  synthesis assembly and aborted the whole run; malformed claims are now
  discarded like any other unusable output.
- Three agents matched tool calls on a `tool_name` attribute that the real
  clients' `ToolCall` contract does not have, so with any real backend the
  tool-call branch never fired and parsing always fell back to free text.
- A `NEEDS_MORE_INFO` review round's targeted queries were assigned and then
  immediately overwritten by a fresh expansion of the original question;
  the reviewer's queries now drive the next search round.
- The ANALYZE-REVIEW rework cycle had no bound of its own (the iteration
  counter only advances through EXPAND); `MAX_REWORKS_PER_ITERATION` caps it.
- `BudgetTracker` used field names that do not exist on `TokenBudget` and was
  never constructed; rewritten against the real contract and wired into the
  pipeline through a metering decorator, making the budget gate live.
- Batch campaign runs passed plain-dict specs to a synthesizer that reads
  attributes, so every batch dossier silently degraded to "Synthesis failed".
- `normalize_repression("none")` returned `NA`; the repression scale's zero
  point collided with the shared missing-value vocabulary.
- The Wikipedia backend sent no User-Agent and Wikimedia now rejects library
  defaults with 403.
- Schemas drifted from the contracts (a required field that did not exist on
  the model, mismatched field names); now generated from the models with a
  drift-guard test.
- aiohttp was imported by seven search backends without being a declared
  dependency; ported to httpx (a declared dependency), with redirect
  following preserved and package imports made lazy.

### Deviations from plan

- CodexClient retries process-level failures with a local bounded loop
  rather than the shared `call_with_backoff`, whose retryability heuristics
  are HTTP-oriented and would veto subprocess failures.

### Acceptance run

- The literature-map example ran live end to end on the codex backend
  (gpt-5.6-terra): 91 records, 26 calls, 105 provenance-checked claims with
  verbatim quotes, 0 dropped, 3 fabrications caught by the retry loop. The
  first live attempt exposed and fixed a double-encoded-handoff failure mode
  now covered by an offline regression test.

### Added

- Local agent backend: `CodexClient` drives the `codex exec` CLI, passing tool
  schemas through the prompt and reading structured results from a file
  handoff with recorded provenance (`raw["source"]`). `build_client()` selects
  the backend from `SAR_LLM_BACKEND`.
