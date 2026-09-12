# Schema policy

The Pydantic models in `src/sar/contracts/` are the contracts. The JSON files
in `schemas/` are generated from them by `scripts/generate_schemas.py` for
consumers outside Python, and are never edited by hand.

## Versioning

Each schema carries `x-schema-version`, following semver:

- patch: description or metadata changes, no shape change
- minor: additive, backward-compatible fields
- major: renamed, removed, or retyped fields

`tests/unit/test_schema_drift.py` regenerates the schemas in memory and diffs
them against the committed files, so a model change without regeneration
fails the suite.

## History note

The v2 schemas were originally hand-written and drifted from the models:
`pipeline_state.json` required a `run_id` field the model never had, and
`campaign_spec.json` used field names (`campaign_name`, `year_start`) the live
`CampaignSpec` does not (`campaign_id`, `year`). The models won. The runner
keeps an input-compatibility alias for `year`/`year_start` when reading
campaign specs (see `runner.py`, `_spec_get`), documented here so it is not
mistaken for schema truth.
