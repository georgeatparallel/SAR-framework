# Promotion checklist

The six criteria below are the author's own promotion gate for SAR v2, set in the
incubator README ("Promotion requires ..."). This file is the acceptance document
for publishing v2 to the public repository. Each item is checked only when the
evidence linked beside it exists in this repo.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Complete offline test suite | met | `pytest -m "not slow and not live"` green, no uncollectable modules |
| 2 | Explicit migration behavior | met | `docs/MIGRATION.md` |
| 3 | Secret and cache isolation | met | `.gitignore`, `.env.example`, no tracked secrets |
| 4 | Stable schemas | met | generated from Pydantic models + drift-guard test + `docs/SCHEMAS.md` |
| 5 | Documented operator workflow | met | `README.md`, `docs/PROMPTS.md`, `docs/ORCHESTRATION.md`, `examples/literature-map/` |
| 6 | Decision to retire or retain v1 | met | `docs/MIGRATION.md`: v1 retired, tagged `v1-flask-gemini` |

Acceptance run (2026-09-12, `litmap-6241a742`): 91 records synthesized in 26
codex/terra calls; 105 claims, all provenance-checked, 0 dropped, 3 model
fabrications caught and corrected by the retry loop; every claim carries a
verbatim quote. Independent re-validation after the run: 0 failures.

Statuses move to `met` with a link to the evidence as stages land. The final
push happens only when all six read `met`.
