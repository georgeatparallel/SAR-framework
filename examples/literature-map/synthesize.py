#!/usr/bin/env python3
"""Synthesize a literature map into a provenance-checked digest.

Reads LITERATURE-MAP.json, drives the configured LLM backend to produce
per-lineage syntheses and per-gap evidence digests, and refuses any claim
that does not trace back into the map: a cited file must exist there, and a
quoted passage must be verbatim from that record's own quotes. Claims that
fail the check are retried once with the failures spelled out, then dropped
and logged. The audit JSONL is the provenance artifact for the whole run.

A gap digest is only as good as the gap it is told to assess. Pass --gaps
with a JSON object mapping each gap id to the paper's actual claim text; the
model then judges evidence against the claim rather than guessing what an
opaque label such as "INTRO-P4-S2" is supposed to mean. Gaps left undefined
are assessed label-only and flagged in the audit log and the output.

Usage:
    python synthesize.py --map LITERATURE-MAP.json --out-dir out/ \
        [--gaps GAPS.json] [--backend codex|openai] [--model MODEL] \
        [--batch-size 6]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from pydantic import ValidationError  # noqa: E402

from sar.contracts.llm import LLMRequest  # noqa: E402
from sar.litmap import LiteratureMap, SynthesisOutput, check_synthesis  # noqa: E402
from sar.litmap.contracts import (  # noqa: E402
    Claim,
    GapDigest,
    LineageSynthesis,
    LitRecord,
)
from sar.litmap.provenance import check_claim  # noqa: E402
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
{"verdict": "<2-4 sentences: does this evidence support THE CLAIM AS WRITTEN, and how directly>",
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


def _wellformed(payload: dict) -> list[dict]:
    """Claim dicts from a payload that satisfy the Claim contract.

    A model may omit a required field ("text" is the usual one). Validating
    here keeps a malformed entry from raising out of synthesis assembly and
    killing an otherwise good run.
    """
    out: list[dict] = []
    for raw in (payload.get("claims") or []):
        if not isinstance(raw, dict) or not raw.get("file"):
            continue
        try:
            Claim.model_validate(raw)
        except ValidationError:
            continue
        out.append(raw)
    return out


def _traces(raw: object, lit_map: LiteratureMap, require_quote: bool) -> bool:
    """True when one raw claim dict is well-formed and traces into the map."""
    if not isinstance(raw, dict) or not raw.get("file"):
        return False
    try:
        claim = Claim.model_validate(raw)
    except ValidationError:
        return False
    return not check_claim(claim, lit_map, require_quote=require_quote)


async def synthesize(args) -> int:
    lit_map = LiteratureMap.model_validate(
        json.loads(Path(args.map).read_text()))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    gap_claims: dict[str, str] = {}
    if args.gaps:
        raw_gaps = json.loads(Path(args.gaps).read_text())
        for gid, val in raw_gaps.items():
            # Accept {"id": "claim"} or {"id": {"claim": "..."}}.
            text = val if isinstance(val, str) else (val or {}).get("claim", "")
            if text:
                gap_claims[gid] = " ".join(str(text).split())

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
        problems = check_synthesis(probe, lit_map,
                                   require_quote=args.require_quotes)
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
            problems = check_synthesis(probe, lit_map,
                                       require_quote=args.require_quotes)
        if problems:
            # Drop exactly the failing claims and keep the traceable
            # remainder. Identify them by re-checking each claim, not by
            # substring-matching the problem strings: a claim carrying no
            # quote has "" as its needle, and "" is a substring of every
            # problem, so string matching silently dropped every quoteless
            # claim in the unit.
            audit.record("provenance_drop", {"unit": label,
                                             "problems": problems})
            dropped.extend(problems)
            payload["claims"] = [
                c for c in (payload.get("claims") or [])
                if _traces(c, lit_map, args.require_quotes)
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
                    claims=_wellformed(payload),
                )
                if dry:
                    return SynthesisOutput(lineage_syntheses=[syn])
                output.lineage_syntheses.append(syn)
                return None

            await checked(build_prompt, attach, f"{lineage}[{bi}]")

    # ---- per-gap digests ---------------------------------------------------
    undefined_gaps: list[str] = []
    for gap, records in sorted(lit_map.by_gap().items()):
        views = json.dumps([_record_view(r) for r in records],
                           ensure_ascii=False, indent=1)
        claim_text = gap_claims.get(gap, "")
        if not claim_text:
            undefined_gaps.append(gap)
            audit.record("gap_undefined", {"gap": gap})

        def build_prompt(problems, gap=gap, views=views, claim=claim_text):
            fix = ""
            if problems:
                fix = ("\nYour previous answer failed provenance checks:\n- "
                       + "\n- ".join(problems) + "\nCorrect these.\n")
            if claim:
                head = (
                    f"The paper makes this claim, and it is currently "
                    f"uncited (gap {gap}):\n\n"
                    f"    \u201c{claim}\u201d\n\n"
                    f"The records below were flagged as possible support for "
                    f"THAT CLAIM. Assess how directly they support it. Judge "
                    f"the claim as written above; do not substitute a broader "
                    f"or adjacent claim. If the records support something "
                    f"near it but not it, say so explicitly.\n\n")
            else:
                head = (
                    f"These records were flagged as possible support for an "
                    f"open citation gap labeled {gap}. The claim text for "
                    f"this gap was NOT supplied, so you do not know what it "
                    f"asserts. Describe what the records do and do not "
                    f"support, and state plainly that the gap text was "
                    f"unavailable. Do not guess the claim.\n\n")
            return f"{head}RECORDS:\n{views}\n\n{fix}{GAP_CONTRACT}"

        def attach(payload, dry, gap=gap, claim=claim_text):
            digest = GapDigest(
                gap=gap,
                claim=claim,
                verdict=str(payload.get("verdict", "")),
                claims=_wellformed(payload),
            )
            if dry:
                return SynthesisOutput(gap_digests=[digest])
            output.gap_digests.append(digest)
            return None

        await checked(build_prompt, attach, f"gap:{gap}")

    # ---- final validation gate --------------------------------------------
    residual = check_synthesis(output, lit_map,
                               require_quote=args.require_quotes)
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
        "gaps_defined": len(output.gap_digests) - len(undefined_gaps),
        "gaps_undefined": undefined_gaps,
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
        "gaps_defined": len(output.gap_digests) - len(undefined_gaps),
        "gaps_undefined": undefined_gaps,
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
            lines += [f"### `{d.gap}`", ""]
            if d.claim:
                lines += [f"> {d.claim}", ""]
            else:
                lines += ["**Gap text was not supplied to the model; this "
                          "verdict is label-only and should not be used as "
                          "citation guidance.**", ""]
            lines += [d.verdict, ""]
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
    ap.add_argument("--gaps", default=None,
                    help="JSON mapping gap id -> the paper's claim text")
    ap.add_argument("--backend", default=None, choices=["openai", "codex"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--batch-size", type=int, default=6)
    ap.add_argument("--require-quotes", action="store_true",
                    help="reject claims that carry no verbatim quote")
    return asyncio.run(synthesize(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
