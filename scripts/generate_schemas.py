#!/usr/bin/env python3
"""Regenerate schemas/ from the Pydantic contracts.

The Pydantic models in ``sar.contracts`` are the source of truth; the JSON
files in ``schemas/`` are generated artifacts for consumers outside Python.
A unit test (tests/unit/test_schema_drift.py) regenerates and diffs, so a
model change without a schema regeneration fails CI.

Usage: python scripts/generate_schemas.py [--check]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SCHEMA_VERSION = "2.0.0"


def build_schemas() -> dict[str, dict]:
    from sar.contracts.campaign import CampaignDossier, CampaignSpec
    from sar.contracts.llm import LLMRequest
    from sar.contracts.pipeline import PipelineState
    from sar.contracts.search import SearchResult

    models = {
        "campaign_spec.json": CampaignSpec,
        "dossier_output.json": CampaignDossier,
        "llm_request.json": LLMRequest,
        "pipeline_state.json": PipelineState,
        "search_result.json": SearchResult,
    }
    out: dict[str, dict] = {}
    for filename, model in models.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = filename.removesuffix(".json")
        schema["x-schema-version"] = SCHEMA_VERSION
        schema["x-generated-by"] = "scripts/generate_schemas.py"
        out[filename] = schema
    return out


def main() -> int:
    check = "--check" in sys.argv
    schemas_dir = ROOT / "schemas"
    schemas_dir.mkdir(exist_ok=True)
    dirty = []
    for filename, schema in build_schemas().items():
        rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        path = schemas_dir / filename
        current = path.read_text() if path.exists() else None
        if current != rendered:
            dirty.append(filename)
            if not check:
                path.write_text(rendered)
    if check:
        if dirty:
            print(f"schemas out of date: {', '.join(dirty)}", file=sys.stderr)
            return 1
        print("schemas up to date")
        return 0
    print(f"regenerated: {', '.join(dirty) if dirty else 'nothing to do'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
