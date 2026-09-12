"""The anti-fabrication check for synthesis output.

Every claim a synthesis makes must point at a file present in the map, and
every quote a claim carries must be a verbatim substring of that record's
own quotes. A model that invents a citation or paraphrases inside a quote
field fails here, before its output is accepted.

Whitespace is normalized on both sides before the substring test, because
PDF extraction and JSON round-trips fold line breaks differently; nothing
else is normalized, so a paraphrase still fails.
"""

from __future__ import annotations

from sar.litmap.contracts import Claim, LiteratureMap, SynthesisOutput


class ProvenanceError(AssertionError):
    """A synthesis claim could not be traced back to the map."""


def _norm(text: str) -> str:
    return " ".join(text.split())


def check_claim(claim: Claim, lit_map: LiteratureMap) -> list[str]:
    """Problems with one claim; empty means it traces cleanly."""
    problems: list[str] = []
    record = lit_map.by_file().get(claim.file)
    if record is None:
        return [f"claim cites a file absent from the map: {claim.file!r}"]
    if claim.quote:
        hay = _norm(" ||| ".join(record.all_quotes()))
        if _norm(claim.quote) not in hay:
            problems.append(
                f"quote is not verbatim from {claim.file!r}: {claim.quote[:80]!r}"
            )
    return problems


def check_synthesis(
    output: SynthesisOutput, lit_map: LiteratureMap
) -> list[str]:
    """All provenance problems in a synthesis; empty means it passes."""
    problems: list[str] = []
    for syn in output.lineage_syntheses:
        for claim in syn.claims:
            problems += [f"[{syn.lineage}] {p}" for p in check_claim(claim, lit_map)]
    for digest in output.gap_digests:
        for claim in digest.claims:
            problems += [f"[{digest.gap}] {p}" for p in check_claim(claim, lit_map)]
    return problems


def assert_synthesis(output: SynthesisOutput, lit_map: LiteratureMap) -> None:
    """Raise ProvenanceError listing every untraceable claim."""
    problems = check_synthesis(output, lit_map)
    if problems:
        raise ProvenanceError(
            f"{len(problems)} provenance failure(s):\n- " + "\n- ".join(problems)
        )
