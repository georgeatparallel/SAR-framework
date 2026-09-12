"""Golden-output regression tests for the literature-map synthesis.

`examples/literature-map/` shipped code and no data, and no stored output
existed anywhere in the repo, so nothing could detect a regression in what the
pipeline actually produces -- only that it produced *something* of the right
shape. The artifacts here are a real acceptance run (`litmap-35713a5c`:
94 claims across 13 lineages and 10 gap digests) which until now lived
unversioned outside the repository.

These assert structural and provenance invariants rather than exact text: the
output is model-generated and will not reproduce verbatim. What must never
regress is that every claim is traceable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "litmap"

# The fixture DATA is deliberately not committed: it carries local file paths
# and verbatim excerpts from copyrighted PDFs, and this repository is public.
# See README.md in this directory for how to place it. Without it these tests
# skip rather than fail, so a fresh clone still gets a green suite.
pytestmark = pytest.mark.skipif(
    not (GOLDEN / "synthesis.json").exists(),
    reason="golden litmap data not present (see tests/golden/README.md)",
)


@pytest.fixture(scope="module")
def synthesis() -> dict:
    return json.loads((GOLDEN / "synthesis.json").read_text())


@pytest.fixture(scope="module")
def audit() -> list[dict]:
    lines = (GOLDEN / "audit.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _all_claims(synthesis: dict) -> list[dict]:
    claims: list[dict] = []
    for lineage in synthesis["lineage_syntheses"]:
        claims.extend(lineage.get("claims", []))
    for gap in synthesis["gap_digests"]:
        claims.extend(gap.get("claims", []))
    return claims


class TestShape:
    def test_has_both_sections(self, synthesis):
        assert synthesis["lineage_syntheses"]
        assert synthesis["gap_digests"]

    def test_expected_scale(self, synthesis):
        """The acceptance run produced 13 lineages and 10 gap digests."""
        assert len(synthesis["lineage_syntheses"]) == 13
        assert len(synthesis["gap_digests"]) == 10

    def test_every_lineage_has_a_summary(self, synthesis):
        for lineage in synthesis["lineage_syntheses"]:
            assert lineage.get("lineage")
            assert lineage.get("summary", "").strip()


class TestProvenance:
    """The property the whole pipeline exists to guarantee."""

    def test_there_are_claims_to_check(self, synthesis):
        # The stored run produced 94. Pinned as a floor: a synthesis that
        # suddenly yields far fewer claims is a regression worth failing on.
        assert len(_all_claims(synthesis)) == 94

    def test_every_claim_carries_a_source_file(self, synthesis):
        unsourced = [c for c in _all_claims(synthesis) if not c.get("file", "").strip()]
        assert unsourced == [], f"{len(unsourced)} claims without a source file"

    def test_every_claim_carries_a_verbatim_quote(self, synthesis):
        unquoted = [c for c in _all_claims(synthesis) if not c.get("quote", "").strip()]
        assert unquoted == [], f"{len(unquoted)} claims without a quote"

    def test_every_claim_carries_a_locator(self, synthesis):
        """A page or section reference, so a reader can find the quote."""
        unlocated = [c for c in _all_claims(synthesis) if not c.get("locator", "").strip()]
        assert unlocated == [], f"{len(unlocated)} claims without a locator"

    def test_claim_text_is_not_just_the_quote(self, synthesis):
        """A synthesis that only echoes quotes has not synthesized anything."""
        echoed = [
            c for c in _all_claims(synthesis)
            if c.get("text", "").strip() == c.get("quote", "").strip()
        ]
        assert len(echoed) <= len(_all_claims(synthesis)) * 0.1


class TestAuditTrail:
    def test_audit_is_parseable_and_non_empty(self, audit):
        assert len(audit) >= 10

    def test_every_entry_has_a_run_id_and_event_type(self, audit):
        for entry in audit:
            assert entry.get("run_id")
            assert entry.get("event_type")

    def test_the_run_completed(self, audit):
        """The litmap path emits its own synthesis_* events, not pipeline_*."""
        types = {e["event_type"] for e in audit}
        assert "synthesis_start" in types
        assert "synthesis_complete" in types

    def test_no_provenance_drops_in_the_accepted_run(self, audit):
        """No claim was discarded for unverifiable provenance.

        One `provenance_retry` did occur -- the gate caught an unsupported
        claim and the retry produced a traceable one, which is the mechanism
        working, not failing.
        """
        assert [e for e in audit if e["event_type"] == "provenance_drop"] == []
        assert sum(1 for e in audit if e["event_type"] == "provenance_retry") == 1
