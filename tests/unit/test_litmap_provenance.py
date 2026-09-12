"""The anti-fabrication guarantee, tested on real extraction output.

The fixture under tests/fixtures/litmap/ is genuine fan-out output from a
91-PDF literature swarm (three batches, quotes trimmed for fair use). These
tests encode the project's core promise: a synthesis cannot cite a document
the map does not contain, and cannot alter a quote and keep it labeled
verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sar.litmap import (
    LiteratureMap,
    LitRecord,
    ProvenanceError,
    SynthesisOutput,
    check_synthesis,
)
from sar.litmap.contracts import Claim, GapDigest, LineageSynthesis
from sar.litmap.provenance import assert_synthesis

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "litmap"


@pytest.fixture(scope="module")
def lit_map() -> LiteratureMap:
    records = []
    for f in sorted(FIXTURE_DIR.glob("batch-*.json")):
        for raw in json.loads(f.read_text())["records"]:
            records.append(LitRecord.model_validate(raw))
    return LiteratureMap(records=records)


class TestFixtureIntegrity:
    def test_fixture_loads_and_validates(self, lit_map):
        assert len(lit_map.records) == 15
        assert all(r.file for r in lit_map.records)

    def test_fixture_carries_real_quotes(self, lit_map):
        quoted = [r for r in lit_map.records if r.all_quotes()]
        assert len(quoted) >= 8, "fixture lost its quotes"

    def test_lineage_values_are_contract_legal(self, lit_map):
        # model_validate would have raised; this documents the vocabulary
        seen = {r.lineage for r in lit_map.records}
        assert seen <= {
            "direct_ancestor", "same_family", "empirical_backdrop",
            "method_toolkit", "peripheral", "unrelated",
        }

    def test_indexes(self, lit_map):
        assert set(lit_map.by_file()) == {r.file for r in lit_map.records}
        assert sum(len(v) for v in lit_map.by_lineage().values()) == 15


def _first_quoted_record(lit_map: LiteratureMap) -> LitRecord:
    for r in lit_map.records:
        if r.all_quotes():
            return r
    raise AssertionError("no quoted record in fixture")


class TestProvenanceCheck:
    def test_faithful_claim_passes(self, lit_map):
        record = _first_quoted_record(lit_map)
        quote = record.all_quotes()[0]
        output = SynthesisOutput(lineage_syntheses=[LineageSynthesis(
            lineage=record.lineage,
            claims=[Claim(text="supported", file=record.file, quote=quote)],
        )])
        assert check_synthesis(output, lit_map) == []

    def test_whitespace_variation_still_passes(self, lit_map):
        record = _first_quoted_record(lit_map)
        quote = record.all_quotes()[0]
        mangled = "  ".join(quote.split())  # different spacing, same words
        output = SynthesisOutput(gap_digests=[GapDigest(
            gap="TEST",
            claims=[Claim(text="x", file=record.file, quote=mangled)],
        )])
        assert check_synthesis(output, lit_map) == []

    def test_invented_file_fails(self, lit_map):
        output = SynthesisOutput(gap_digests=[GapDigest(
            gap="TEST",
            claims=[Claim(text="x", file="does/not/exist.pdf")],
        )])
        problems = check_synthesis(output, lit_map)
        assert len(problems) == 1
        assert "absent from the map" in problems[0]

    def test_paraphrased_quote_fails(self, lit_map):
        record = _first_quoted_record(lit_map)
        output = SynthesisOutput(lineage_syntheses=[LineageSynthesis(
            lineage=record.lineage,
            claims=[Claim(
                text="x", file=record.file,
                quote="a paraphrase the source never printed",
            )],
        )])
        problems = check_synthesis(output, lit_map)
        assert len(problems) == 1
        assert "not verbatim" in problems[0]

    def test_truncating_a_real_quote_passes(self, lit_map):
        # A shorter verbatim excerpt of a stored quote is still verbatim.
        record = _first_quoted_record(lit_map)
        quote = record.all_quotes()[0]
        head = quote[: max(20, len(quote) // 2)]
        output = SynthesisOutput(gap_digests=[GapDigest(
            gap="TEST", claims=[Claim(text="x", file=record.file, quote=head)],
        )])
        assert check_synthesis(output, lit_map) == []

    def test_assert_raises_with_full_inventory(self, lit_map):
        record = _first_quoted_record(lit_map)
        output = SynthesisOutput(gap_digests=[GapDigest(
            gap="TEST",
            claims=[
                Claim(text="a", file="nope.pdf"),
                Claim(text="b", file=record.file, quote="fabricated words"),
            ],
        )])
        with pytest.raises(ProvenanceError, match="2 provenance failure"):
            assert_synthesis(output, lit_map)

    def test_claim_without_quote_needs_only_a_real_file(self, lit_map):
        record = lit_map.records[0]
        output = SynthesisOutput(gap_digests=[GapDigest(
            gap="TEST", claims=[Claim(text="x", file=record.file)],
        )])
        assert check_synthesis(output, lit_map) == []
