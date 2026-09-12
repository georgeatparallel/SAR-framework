# Literature map

A real deep-research run: 91 PDF extraction records from a political science
literature corpus, joined, validated, and synthesized into a digest where
every claim traces to a source document and a verbatim quote.

This example doubles as the operator workflow for the pipeline and as its
acceptance test (`tests/integration/test_litmap_synthesis.py` runs exactly
this, offline, against a scripted model).

## The input

Nineteen `batch-NN.json` files, each written by an independent extraction
agent that read five PDFs and recorded, per document: bibliographic identity,
lineage relative to the target paper (`direct_ancestor`, `same_family`,
`empirical_backdrop`, `method_toolkit`, `peripheral`, `unrelated`),
substantive links to the paper's constructs, empirical patterns, and
candidate support for the paper's open citation gaps. Every link carries a
verbatim quote with a locator. A trimmed three-batch sample lives in
`tests/fixtures/litmap/`.

## Step 1: join

```bash
python join.py --batches /path/to/swarm-out --out LITERATURE-MAP.json
```

Validates every record against the `LitRecord` contract, deduplicates by
document, and prints lineage and gap indexes. Validation failures are listed
and fail the join.

## Step 2: synthesize

```bash
SAR_LLM_BACKEND=codex python synthesize.py \
    --map LITERATURE-MAP.json --out-dir out/
```

Drives the configured backend (local Codex CLI by default here; `--backend
openai` behaves identically) through per-lineage syntheses and per-gap
evidence digests, batched a few records per call.

Every returned claim passes the provenance gate before acceptance:

- the cited `file` must exist in the map;
- a `quote` must be a verbatim substring of that record's own quotes
  (whitespace-normalized, nothing else).

A response that fails is retried once with the failures spelled out; claims
that still fail are dropped and logged, never silently kept. The run writes:

- `LITERATURE-MAP.md` — the human digest
- `LITERATURE-MAP-SYNTHESIS.json` — the typed output
- `audit/<run-id>.jsonl` — one `llm_call` event per model call (with the
  codex response's recovery source), every provenance retry and drop, and
  the final tallies. This file is the proof of provenance for the whole run.
