#!/usr/bin/env python3
"""Join fan-out extraction batches into one validated literature map.

Input: a directory of batch-NN.json files, each ``{"batch": n, "records":
[...]}`` produced by independent extraction agents over a PDF corpus.
Output: LITERATURE-MAP.json — every record validated against the LitRecord
contract, deduplicated by file, with lineage and gap indexes.

Usage:
    python join.py --batches DIR --out LITERATURE-MAP.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sar.litmap import LiteratureMap, LitRecord  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batches", required=True, help="Directory of batch-*.json")
    ap.add_argument("--out", required=True, help="Output LITERATURE-MAP.json")
    args = ap.parse_args()

    records: dict[str, LitRecord] = {}
    problems: list[str] = []
    files = sorted(Path(args.batches).glob("batch-*.json"))
    if not files:
        print(f"no batch-*.json under {args.batches}", file=sys.stderr)
        return 1

    for f in files:
        payload = json.loads(f.read_text())
        for i, raw in enumerate(payload.get("records", [])):
            try:
                rec = LitRecord.model_validate(raw)
            except Exception as exc:
                problems.append(f"{f.name}[{i}]: {exc}")
                continue
            if rec.file in records:
                # Same document extracted twice: keep the richer record.
                old = records[rec.file]
                if len(rec.all_quotes()) <= len(old.all_quotes()):
                    continue
            records[rec.file] = rec

    lit_map = LiteratureMap(records=sorted(
        records.values(), key=lambda r: r.file))

    out = Path(args.out)
    out.write_text(json.dumps(
        lit_map.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n")

    by_lineage = {k: len(v) for k, v in sorted(lit_map.by_lineage().items())}
    by_gap = {k: len(v) for k, v in sorted(lit_map.by_gap().items())}
    quotes = sum(len(r.all_quotes()) for r in lit_map.records)
    print(json.dumps({
        "batches": len(files),
        "records": len(lit_map.records),
        "validation_failures": len(problems),
        "verbatim_quotes": quotes,
        "by_lineage": by_lineage,
        "by_gap": by_gap,
    }, indent=2))
    for p in problems:
        print(f"INVALID {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
