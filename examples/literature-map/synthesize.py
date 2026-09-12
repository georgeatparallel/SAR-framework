#!/usr/bin/env python3
"""Synthesize a literature map into a provenance-checked digest.

Reads LITERATURE-MAP.json, drives the configured LLM backend to produce
per-lineage syntheses and per-gap evidence digests, and refuses any claim
that does not trace back into the map: a cited file must exist there, and a
quoted passage must be verbatim from that record's own quotes. Claims that
fail the check are retried once with the failures spelled out, then dropped
and logged. The audit JSONL is the provenance artifact for the whole run.

Usage:
    python synthesize.py --map LITERATURE-MAP.json --out-dir out/ \
        [--backend codex|openai] [--model MODEL] [--batch-size 6]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sar.contracts.llm import LLMRequest  # noqa: E402
from sar.litmap import LiteratureMap, SynthesisOutput, check_synthesis  # noqa: E402
from sar.litmap.contracts import (  # noqa: E402
    GapDigest,
    LineageSynthesis,
    LitRecord,
)
from sar.llm import LLMClientError, build_client  # noqa: E402
from sar.llm.budget import BudgetTracker  # noqa: E402
from sar.llm.metering import MeteredClient  # noqa: E402
from sar.persistence.audit import AuditLog  # noqa: E402

SYNTH_LINEAGES = [
    "direct_ancestor", "same_family", "empirical_backdrop", "method_toolkit",
]

CONTRACT = """\
Reply with ONE JSON object and nothing else, shaped exactly like:
{"summary": "<3-6 sentences>",
 "claims": [{"text": "<one finding>", "file": "<file id copied from a record>",
             "locator": "<locator copied from that record>",
             "quote": "<verbatim quote copied character-for-character from that record>"}]}

Rules:
- Every claim MUST carry the "file" of the record it comes from, copied exactly.
- "quote" MUST be copied verbatim from that record's own quotes (you may
  shorten it, but never reword it). No quote from memory, ever.
- 3 to 6 claims. Plain factual prose in "text".
"""

GAP_CONTRACT = """\
Reply with ONE JSON object and nothing else, shaped exactly like:
{"verdict": "<2-4 sentences: does this evidence support the claim, and how directly>",
 "claims": [{"text": "...", "file": "...", "locator": "...", "quote": "..."}]}

Rules as before: file copied exactly from a record; quote verbatim from that
record's quotes, shortening allowed, rewording forbidden.
"""


def _record_view(r: LitRecord) -> dict:
    """What the model sees for one record: identity plus its quotes."""
    return {
        "file": r.file,
        "title": r.bibliographic.title,
        "year": r.bibliographic.year,
        "lineage": r.lineage,
        "lineage_justification": r.lineage_justification,
        "quotes": [
            {"quote": q.quote, "locator": q.locator}
            for q in [*r.substantive_links, *r.empirical_patterns,
                      *r.notable_quotes]
            if q.quote
        ],
    }


async def _ask(llm, prompt: str) -> dict:
    resp = await llm.call(LLMRequest(
        messages=[{"role": "user", "content": prompt}]))
    if resp.content:
        try:
            parsed = json.loads(resp.content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    # Codex handoff tolerance: a model may write the requested object as the
    # handoff root instead of wrapping it in "content". The raw payload is
    # preserved on the response; accept it when it carries our keys, so a
    # well-formed answer in the wrong envelope is not silently discarded.
    raw = (resp.raw or {}).get("payload")
    if isinstance(raw, dict) and ("claims" in raw or "summary" in raw
                                  or "verdict" in raw):
        return raw
    return {}


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


async def synthesize(args) -> int:
    lit_map = LiteratureMap.model_validate(
        json.loads(Path(args.map).read_text()))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"litmap-{uuid.uuid4().hex[:8]}"
    audit = AuditLog(run_id=run_id, log_dir=out_dir / "audit")
    tracker = BudgetTracker()
    llm = MeteredClient(
        build_client(args.backend, model=args.model), tracker, audit)
    audit.record("synthesis_start", {
        "map": str(args.map), "records": len(lit_map.records),
        "backend": args.backend or "env-default",
    })

    output = SynthesisOutput()
    dropped: list[str] = []
    skipped_units: list[str] = []

    async def checked(build_prompt, attach, label: str) -> None:
        """One synthesis unit: ask, provenance-check, retry once, attach.

        A failed model call skips this unit rather than aborting the run;
        the audit log records what was skipped.
        """
        try:
            payload = await _ask(llm, build_prompt(None))
        except LLMClientError as exc:
            audit.record_error("synthesis_unit", str(exc), {"unit": label})
            skipped_units.append(label)
            return
        probe = attach(payload, dry=True)
        problems = check_synthesis(probe, lit_map)
        if problems:
            audit.record("provenance_retry", {"unit": label,
                                              "problems": problems})
            try:
                payload = await _ask(llm, build_prompt(problems))
            except LLMClientError as exc:
                audit.record_error("synthesis_unit", str(exc), {"unit": label})
                skipped_units.append(label)
                return
            probe = attach(payload, dry=True)
            problems = check_synthesis(probe, lit_map)
        if problems:
            # Drop only the failing claims; keep the traceable remainder.
            audit.record("provenance_drop", {"unit": label,
                                             "problems": problems})
            dropped.extend(problems)
            payload["claims"] = [
                c for c in payload.get("claims", [])
                if not any(str(c.get("file")) in p or
                           str(c.get("quote", ""))[:80] in p
                           for p in problems)
            ]
        attach(payload, dry=False)

    # ---- per-lineage syntheses, batched -----------------------------------
    for lineage in SYNTH_LINEAGES:
        records = lit_map.by_lineage().get(lineage, [])
        if not records:
            continue
        for bi, batch in enumerate(_chunk(records, args.batch_size), 1):
            views = json.dumps([_record_view(r) for r in batch],
                               ensure_ascii=False, indent=1)

            def build_prompt(problems, lineage=lineage, views=views):
                fix = ""
                if problems:
                    fix = ("\nYour previous answer failed provenance checks:\n- "
                           + "\n- ".join(problems)
                           + "\nCorrect these: copy quotes verbatim and cite "
                             "only listed files.\n")
                return (
                    f"You are synthesizing literature records classified as "
                    f"'{lineage}' relative to a formal-theory paper on "
                    f"repression severity in democracies.\n\n"
                    f"RECORDS:\n{views}\n\n{fix}{CONTRACT}")

            def attach(payload, dry, lineage=lineage):
                syn = LineageSynthesis(
                    lineage=lineage,
                    summary=str(payload.get("summary", "")),
                    claims=[c for c in (payload.get("claims") or [])
                            if isinstance(c, dict) and c.get("file")],
                )
                if dry:
                    return SynthesisOutput(lineage_syntheses=[syn])
                output.lineage_syntheses.append(syn)
                return None

            await checked(build_prompt, attach, f"{lineage}[{bi}]")

    # ---- per-gap digests ---------------------------------------------------
    for gap, records in sorted(lit_map.by_gap().items()):
        views = json.dumps([_record_view(r) for r in records],
                           ensure_ascii=False, indent=1)

        def build_prompt(problems, gap=gap, views=views):
            fix = ""
            if problems:
                fix = ("\nYour previous answer failed provenance checks:\n- "
                       + "\n- ".join(problems) + "\nCorrect these.\n")
            return (
                f"These records were flagged as possible support for an open "
                f"citation gap labeled {gap} in the paper. Assess how directly "
                f"the evidence supports that gap.\n\n"
                f"RECORDS:\n{views}\n\n{fix}{GAP_CONTRACT}")

        def attach(payload, dry, gap=gap):
            digest = GapDigest(
                gap=gap,
                verdict=str(payload.get("verdict", "")),
                claims=[c for c in (payload.get("claims") or [])
                        if isinstance(c, dict) and c.get("file")],
            )
            if dry:
                return SynthesisOutput(gap_digests=[digest])
            output.gap_digests.append(digest)
            return None

        await checked(build_prompt, attach, f"gap:{gap}")

    # ---- final validation gate --------------------------------------------
    residual = check_synthesis(output, lit_map)
    if residual:
        audit.record_error("provenance", "residual failures after drop",
                           {"problems": residual})
        print("RESIDUAL PROVENANCE FAILURES:", *residual, sep="\n- ",
              file=sys.stderr)
        return 1

    (out_dir / "LITERATURE-MAP-SYNTHESIS.json").write_text(json.dumps(
        output.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n")
    _render_markdown(output, lit_map, out_dir / "LITERATURE-MAP.md",
                     run_id, tracker, dropped)
    audit.record("synthesis_complete", {
        "llm_calls": tracker.call_count,
        "unmetered_calls": tracker.unmetered_calls,
        "cost_usd": tracker.estimated_cost_usd,
        "dropped_claims": len(dropped),
        "skipped_units": skipped_units,
    })
    print(json.dumps({
        "run_id": run_id,
        "llm_calls": tracker.call_count,
        "lineage_syntheses": len(output.lineage_syntheses),
        "gap_digests": len(output.gap_digests),
        "claims_total": sum(len(s.claims) for s in output.lineage_syntheses)
        + sum(len(d.claims) for d in output.gap_digests),
        "dropped_claims": len(dropped),
        "skipped_units": skipped_units,
        "audit_log": str(audit.log_path),
    }, indent=2))
    return 0


def _render_markdown(output, lit_map, path, run_id, tracker, dropped) -> None:
    by_file = lit_map.by_file()
    lines = [
        "# Literature map",
        "",
        f"Synthesized from {len(lit_map.records)} extraction records; every",
        "claim below carries the source record and a verbatim quote, checked",
        f"mechanically before acceptance. Run `{run_id}`;",
        f"{tracker.call_count} model calls; {len(dropped)} claims dropped for",
        "provenance failures (see the audit log).",
        "",
    ]
    for syn in output.lineage_syntheses:
        lines += [f"## {syn.lineage.replace('_', ' ')}", "", syn.summary, ""]
        for c in syn.claims:
            rec = by_file.get(c.file)
            title = (rec.bibliographic.title if rec else None) or c.file
            lines += [f"- {c.text}",
                      f"  - *{title}* ({c.locator or 'no locator'}):",
                      f"    > {c.quote}", ""]
    if output.gap_digests:
        lines += ["## Evidence for the paper's open citation gaps", ""]
        for d in output.gap_digests:
            lines += [f"### `{d.gap}`", "", d.verdict, ""]
            for c in d.claims:
                rec = by_file.get(c.file)
                title = (rec.bibliographic.title if rec else None) or c.file
                lines += [f"- {c.text}",
                          f"  - *{title}* ({c.locator or 'no locator'}):",
                          f"    > {c.quote}", ""]
    Path(path).write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--backend", default=None, choices=["openai", "codex"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--batch-size", type=int, default=6)
    return asyncio.run(synthesize(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
