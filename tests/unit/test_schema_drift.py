"""Drift guard: schemas/ must match the Pydantic contracts.

The JSON files under schemas/ are generated from the models by
scripts/generate_schemas.py. A model change without regeneration fails here,
so the shipped schemas can never silently diverge from the real contracts
(which is exactly what had happened to the hand-written v2 schemas).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_schemas import build_schemas  # noqa: E402


def test_every_schema_file_matches_its_model():
    for filename, schema in build_schemas().items():
        on_disk = json.loads((ROOT / "schemas" / filename).read_text())
        assert on_disk == schema, (
            f"{filename} is out of date; run scripts/generate_schemas.py"
        )


def test_schemas_carry_version_and_generator():
    for filename in build_schemas():
        on_disk = json.loads((ROOT / "schemas" / filename).read_text())
        assert on_disk.get("x-schema-version"), filename
        assert on_disk.get("x-generated-by") == "scripts/generate_schemas.py", filename


def test_no_stray_schema_files():
    generated = set(build_schemas())
    on_disk = {p.name for p in (ROOT / "schemas").glob("*.json")}
    assert on_disk == generated
