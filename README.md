# SAR

A research pipeline that searches, reads, and reviews sources with cooperating
agents, and refuses to state a claim it cannot trace to a document.

SAR runs the Search, Analyze, Review loop as a typed state machine. Every LLM
call, gate decision, and search goes to an append-only audit log. Extraction
output carries the source and locator for each field. The reviewer can send the
pipeline back for rework or more searching before anything is returned.

This is research software with a working test suite, not a hosted product. You
bring your own model access.

## How it works

```
Query -> EXPAND -> SEARCH -> JUDGE -> READ -> ANALYZE -> REVIEW -> report
           ^                                     ^          |
           |            (more info needed)       | (rework) |
           +-------------------------------------+----------+
```

1. **Expand** turns the question into diverse search queries.
2. **Search** fans out across academic and web backends (Crossref, Semantic
   Scholar, arXiv, PubMed, Wikipedia, DuckDuckGo; Brave and Unpaywall when
   keys are present) and deduplicates.
3. **Judge** scores sources for relevance, authority, and recency; low scores
   are dropped.
4. **Read** extracts structured fields per source, with the instruction to
   include only facts the source supports.
5. **Analyze** writes a report where every claim carries an inline citation.
6. **Review** approves, requests rework with feedback, or sends the pipeline
   back to search with new queries. Iterations are bounded.

Each phase transition is a typed gate with a recorded decision. The audit log
(`JSONL`, one file per run) is the provenance record: what was searched, what
each model call cost, which gates passed, and why the loop ended.

## Two ways to run the model

The pipeline is written against one client protocol; the backend is
configuration.

| Backend | What it is | Needs |
|---|---|---|
| `openai` | Hosted API with native function calling | `OPENAI_API_KEY` |
| `codex`  | The local Codex CLI as an agent process | `codex` on PATH |

```
SAR_LLM_BACKEND=codex sar research "protest repression nexus in democracies"
```

With the `codex` backend, tool schemas are rendered into the prompt and the
model returns its answer as JSON through a file handoff. No key, and nothing
leaves your machine except what the model provider itself does.

## Install

Requires Python 3.11+.

```bash
pip install -e .            # core: pipeline + codex backend
pip install -e ".[openai]"  # hosted API backend
pip install -e ".[scrape]"  # HTML fetching (playwright)
pip install -e ".[pdf]"     # PDF reading (PyMuPDF)
pip install -e ".[all]"
```

Copy `.env.example` to `.env` and fill what your backend needs. Then:

```bash
sar doctor      # checks binaries, keys, and optional deps
sar research "your question" --output report.md
```

## Documentation

- `docs/PROMPTS.md` — every agent's actual system prompt and tool contract
- `docs/ORCHESTRATION.md` — the state machine, gates, and reviewer loop
- `docs/SCHEMAS.md` — schema generation and versioning policy
- `docs/MIGRATION.md` — coming from v1 (Flask + Gemini)
- `examples/literature-map/` — a real run: 91 literature records synthesized
  with per-claim provenance

## License

MIT.
