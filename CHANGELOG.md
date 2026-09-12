# Changelog

## 2.0.0 (unreleased)

The v2 rewrite becomes the repository's main line; the Flask + Gemini
generation is retired at tag `v1-flask-gemini` (see `docs/MIGRATION.md`).

### Carried forward from v1

- The per-agent prompt specification document (`docs/PROMPTS.md`) and the
  orchestration workflow document (`docs/ORCHESTRATION.md`), both rewritten
  against the v2 code so that every quoted prompt and every decision rule is
  the one the code actually uses.
- The pipeline flow visualization (`docs/flow_visualization.html`).
- The README's shape: honest scope, diagram, numbered walk-through.

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
