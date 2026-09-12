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

### Added

- Local agent backend: `CodexClient` drives the `codex exec` CLI, passing tool
  schemas through the prompt and reading structured results from a file
  handoff with recorded provenance (`raw["source"]`). `build_client()` selects
  the backend from `SAR_LLM_BACKEND`.
