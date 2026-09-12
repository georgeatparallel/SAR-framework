"""Typed contracts for literature-map records and synthesis output."""

from __future__ import annotations

import warnings
from typing import Literal

from pydantic import BaseModel, Field

Lineage = Literal[
    "direct_ancestor",
    "same_family",
    "empirical_backdrop",
    "method_toolkit",
    "peripheral",
    "unrelated",
]


class Quote(BaseModel):
    """A verbatim excerpt with its locator."""

    quote: str = ""
    locator: str = ""


# "construct" is the extraction ontology's own field name and the key in the
# records already on disk, so it stays. Pydantic warns at class creation
# because the name shadows BaseModel.construct, a v1 method deprecated in v2
# and unused here. Suppress that one warning rather than rename the data.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore", message=r'Field name "construct" .*shadows',
        category=UserWarning)

    class SubstantiveLink(Quote):
        construct: str = ""  # type: ignore[assignment]  # ontology vocabulary, shadows BaseModel.construct
        relation: str = ""


class EmpiricalPattern(Quote):
    claim: str = ""


class NotableQuote(Quote):
    why: str = ""


class Bibliographic(BaseModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    verified_from: str | None = None


class LitRecord(BaseModel):
    """One document's extraction record from the fan-out."""

    file: str
    bibliographic: Bibliographic = Field(default_factory=Bibliographic)
    approach: str | None = None
    regime_context: str | None = None
    unit_and_data: str | None = None
    lineage: Lineage = "unrelated"
    lineage_justification: str | None = None
    substantive_links: list[SubstantiveLink] = Field(default_factory=list)
    empirical_patterns: list[EmpiricalPattern] = Field(default_factory=list)
    could_support_gap: list[str] = Field(default_factory=list)
    gap_justification: str | None = None
    notable_quotes: list[NotableQuote] = Field(default_factory=list)
    confidence: str | None = None
    caveats: str | None = None

    def all_quotes(self) -> list[str]:
        """Every verbatim quote this record carries."""
        items: list[Quote] = [
            *self.substantive_links,
            *self.empirical_patterns,
            *self.notable_quotes,
        ]
        return [q.quote for q in items if q.quote]


class LiteratureMap(BaseModel):
    """The joined map: every record, plus derived indexes."""

    records: list[LitRecord]

    def by_file(self) -> dict[str, LitRecord]:
        return {r.file: r for r in self.records}

    def by_lineage(self) -> dict[str, list[LitRecord]]:
        out: dict[str, list[LitRecord]] = {}
        for r in self.records:
            out.setdefault(r.lineage, []).append(r)
        return out

    def by_gap(self) -> dict[str, list[LitRecord]]:
        out: dict[str, list[LitRecord]] = {}
        for r in self.records:
            for g in r.could_support_gap:
                out.setdefault(g, []).append(r)
        return out


class Claim(BaseModel):
    """One synthesized claim, pointing back into the map."""

    text: str
    file: str
    locator: str = ""
    quote: str = ""


class LineageSynthesis(BaseModel):
    lineage: str
    summary: str = ""
    claims: list[Claim] = Field(default_factory=list)


class GapDigest(BaseModel):
    """One gap's evidence digest.

    ``claim`` is the paper's actual uncited sentence. It is what the verdict
    must be judged against; an empty ``claim`` means the digest was produced
    label-only and its verdict is not trustworthy as guidance.
    """

    gap: str
    claim: str = ""
    verdict: str = ""
    claims: list[Claim] = Field(default_factory=list)


class SynthesisOutput(BaseModel):
    """What a synthesis run produces; provenance-checked before acceptance."""

    lineage_syntheses: list[LineageSynthesis] = Field(default_factory=list)
    gap_digests: list[GapDigest] = Field(default_factory=list)
