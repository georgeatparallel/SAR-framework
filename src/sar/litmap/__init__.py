"""Literature-map contracts and the anti-fabrication provenance check.

A literature map is the joined output of a fan-out extraction over a PDF
corpus: one record per document, carrying lineage classification and verbatim
quotes with locators. The provenance check enforces the project's central
guarantee — a synthesis over the map may not state a quote or cite a
document that the map does not contain.
"""

from __future__ import annotations

from sar.litmap.contracts import (
    GapDigest,
    LineageSynthesis,
    LiteratureMap,
    LitRecord,
    SynthesisOutput,
)
from sar.litmap.provenance import ProvenanceError, check_synthesis

__all__ = [
    "GapDigest",
    "LineageSynthesis",
    "LitRecord",
    "LiteratureMap",
    "ProvenanceError",
    "SynthesisOutput",
    "check_synthesis",
]
