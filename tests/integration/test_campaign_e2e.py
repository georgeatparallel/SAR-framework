"""End-to-end campaign pipeline tests with a scripted LLM.

Drives ``run_campaign_pipeline`` (standard pipeline + campaign
synthesizer) fully offline, then validates the dossier against the
codebook validators, and exercises the batch-layer file helpers
(compare_with_baseline, write layout via run_batch, rebuild_batch_index).

Reuses the scripted-LLM fakes from test_pipeline_e2e; the synthesizer
slot answers requests carrying the submit_campaign_dossier tool.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from sar.campaign.batch import (
    build_campaign_spec_from_row,
    compare_with_baseline,
    rebuild_batch_index,
    run_batch,
)
from sar.campaign.schema import DATASET_COLUMNS, KEY_FIELDS
from sar.campaign.validators import (
    normalize_coded_row,
    validate_campaign_dossier,
    validate_coded_row,
)
from sar.contracts.campaign import CampaignSpec
from sar.orchestrator.runner import run_campaign_pipeline
from tests.integration.test_pipeline_e2e import (
    ScriptedLLM,
    StubConfig,
    default_scripts,
    make_results,
    make_search_engine,
    tool_response,
)

SPEC = CampaignSpec(
    campaign_id="egypt-2011-tahrir",
    campaign_name="Tahrir Square Uprising",
    country="Egypt",
    year=2011,
    description="January 25 revolution",
    priority_fields=["outcome", "duration_days"],
)


def make_coded_row() -> dict[str, Any]:
    """A codebook-clean coded row covering all 39 columns."""
    row: dict[str, Any] = {col: "NA" for col in DATASET_COLUMNS}
    row.update({
        "country": "Egypt",
        "year_start": "2011",
        "campaign_name": "Tahrir Square Uprising",
        "demand_type": "regime_change",
        "nonviolent": "1.0",
        "organised": "0.5",
        "external_support": "0.0",
        "region": "MENA",
        "outcome": "1.0",
        "duration_days": "18",
        "peak_participation": "1000000",
        "platform_blocked": "1.0",
        "repression_level": "3",
        "internet_shutdown": "1.0",
    })
    return row


def make_evidence_log() -> list[dict[str, Any]]:
    """Evidence entries covering every KEY_FIELD plus one extra."""
    fields = [*KEY_FIELDS, "internet_shutdown"]
    return [
        {
            "field_name": field,
            "coded_value": make_coded_row()[field],
            "source_type": "academic",
            "source_citation": "Author (2020), Protest Dynamics",
            "source_link_or_identifier": "https://example.org/paper-1",
            "notes": "directly stated in the source",
        }
        for field in fields
    ]


def synthesizer_response() -> Any:
    return tool_response(
        "submit_campaign_dossier",
        inclusion_memo={
            "campaign_name": "Tahrir Square Uprising",
            "country": "Egypt",
            "start_date": "2011-01-25",
            "end_date": "2011-02-11",
            "included": "yes",
            "reason_for_inclusion_or_exclusion": "well-documented mass campaign",
            "notes_on_scope_ambiguity": "none of note",
        },
        coded_row=make_coded_row(),
        evidence_log=make_evidence_log(),
        uncertainty_note="Peak participation estimates vary widely.",
        report_markdown="## Campaign Report\nAn 18-day uprising [Source 1].",
        citations_used=["https://example.org/paper-1"],
    )


def campaign_scripts() -> dict[str, list[Any]]:
    return default_scripts(synthesizer=[synthesizer_response()])


async def run_campaign(tmp_audit_log, spec: Any = SPEC) -> tuple[dict[str, Any], ScriptedLLM]:
    llm = ScriptedLLM(**campaign_scripts())
    dossier = await run_campaign_pipeline(
        campaign_spec=spec,
        config=StubConfig(),
        search_engine=make_search_engine(make_results()),
        llm=llm,
        audit_log=tmp_audit_log,
    )
    return dossier, llm


# ---------------------------------------------------------------------------
# Dossier synthesis and validation
# ---------------------------------------------------------------------------


class TestCampaignPipeline:
    """run_campaign_pipeline produces a validating dossier."""

    async def test_produces_dossier_with_all_sections(self, tmp_audit_log):
        dossier, llm = await run_campaign(tmp_audit_log)

        for key in (
            "campaign_spec",
            "coded_row",
            "evidence_log",
            "inclusion_memo",
            "uncertainty_note",
            "report_markdown",
            "citations_used",
            "pipeline_result",
        ):
            assert key in dossier

        assert dossier["campaign_spec"] is SPEC
        assert dossier["pipeline_result"].success is True
        assert dossier["report_markdown"].startswith("## Campaign Report")
        assert dossier["citations_used"] == ["https://example.org/paper-1"]
        # The synthesizer was called once, after the standard pipeline.
        assert len(llm.requests["synthesizer"]) == 1

    async def test_synthesizer_receives_spec_and_summaries(self, tmp_audit_log):
        _, llm = await run_campaign(tmp_audit_log)

        request = llm.requests["synthesizer"][0]
        system = request.messages[0]["content"]
        user = request.messages[1]["content"]
        assert "Codebook constraints" in system
        assert "Tahrir Square Uprising" in user
        assert "Egypt" in user
        # Reader summaries were forwarded as evidence blocks.
        assert "[Evidence 1]" in user

    async def test_dossier_validates_cleanly(self, tmp_audit_log):
        dossier, _ = await run_campaign(tmp_audit_log)
        assert validate_campaign_dossier(dossier) == []

    async def test_coded_row_normalizes_and_validates(self, tmp_audit_log):
        dossier, _ = await run_campaign(tmp_audit_log)

        normalized = normalize_coded_row(
            dossier["coded_row"],
            campaign_spec={
                "country": SPEC.country,
                "campaign_name": SPEC.campaign_name,
                "year_start": SPEC.year,
                "region": "MENA",
            },
        )
        assert validate_coded_row(normalized) == []
        assert normalized["country"] == "Egypt"
        assert normalized["year_start"] == "2011"
        assert normalized["outcome"] == "1.0"
        assert normalized["repression_level"] == "3"

    async def test_evidence_log_references_known_fields(self, tmp_audit_log):
        dossier, _ = await run_campaign(tmp_audit_log)

        evidence_fields = {e["field_name"] for e in dossier["evidence_log"]}
        assert evidence_fields <= set(DATASET_COLUMNS)
        # Every prioritized field is covered.
        assert set(KEY_FIELDS) <= evidence_fields

    async def test_synthesizer_failure_degrades_to_empty_dossier(self, tmp_audit_log):
        # A dict spec reaches the synthesizer, whose prompt formatting
        # requires attribute access (campaign_spec.campaign_name) and
        # raises; run_campaign_pipeline degrades to an empty dossier
        # with the pipeline report preserved. This is the path run_batch
        # currently exercises, since it passes plain dict specs.
        dict_spec = {
            "campaign_name": "Tahrir Square Uprising",
            "country": "Egypt",
            "year_start": "2011",
        }
        dossier, _ = await run_campaign(tmp_audit_log, spec=dict_spec)

        assert dossier["coded_row"] == {}
        assert dossier["evidence_log"] == []
        assert dossier["uncertainty_note"].startswith("Synthesis failed:")
        assert dossier["pipeline_result"].success is True
        assert dossier["report_markdown"]  # falls back to pipeline report
        errors = [
            e for e in tmp_audit_log.dump() if e["event_type"] == "error"
        ]
        assert any(
            e["data"]["error_type"] == "campaign_synthesizer" for e in errors
        )


# ---------------------------------------------------------------------------
# Batch helpers: baseline comparison
# ---------------------------------------------------------------------------


class TestCompareWithBaseline:
    """Field diff classification: match / filled / missing / changed."""

    def test_classifies_all_statuses(self):
        baseline = {col: "NA" for col in DATASET_COLUMNS}
        baseline.update({
            "country": "Egypt",          # match
            "outcome": "1.0",            # match
            "duration_days": "20",       # changed
            "repression_level": "2",     # missing in researched
            "peak_participation": "NA",  # filled by researched
        })
        researched = {col: "NA" for col in DATASET_COLUMNS}
        researched.update({
            "country": "Egypt",
            "outcome": "1.0",
            "duration_days": "18",
            "repression_level": "NA",
            "peak_participation": "1000000",
        })

        diffs = {d["field_name"]: d for d in compare_with_baseline(researched, baseline)}

        assert len(diffs) == len(DATASET_COLUMNS)
        assert diffs["country"]["status"] == "match"
        assert diffs["outcome"]["status"] == "match"
        assert diffs["duration_days"]["status"] == "changed"
        assert diffs["repression_level"]["status"] == "missing"
        assert diffs["peak_participation"]["status"] == "filled"
        # Both-NA fields count as matches.
        assert diffs["gini_coefficient"]["status"] == "match"

    def test_na_comparison_is_case_insensitive(self):
        diffs = compare_with_baseline(
            {"country": "egypt"}, {"country": "Egypt"}
        )
        by_field = {d["field_name"]: d for d in diffs}
        assert by_field["country"]["status"] == "match"


# ---------------------------------------------------------------------------
# Batch runner file layout
# ---------------------------------------------------------------------------


def write_master_csv(path: Path) -> dict[str, str]:
    """Write a one-row master dataset; returns the row."""
    row = {col: "NA" for col in DATASET_COLUMNS}
    row.update({
        "country": "Egypt",
        "year_start": "2011",
        "campaign_name": "Tahrir Square Uprising",
        "region": "MENA",
        "outcome": "1.0",
        "duration_days": "18",
        "repression_level": "3",
    })
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DATASET_COLUMNS)
        writer.writeheader()
        writer.writerow(row)
    return row


class TestBatchRunner:
    """run_batch writes the per-campaign file layout and manifest."""

    async def test_writes_expected_file_structure(self, tmp_path):
        dataset = tmp_path / "master_dataset.csv"
        row = write_master_csv(dataset)
        output_root = tmp_path / "batch_out"
        spec = build_campaign_spec_from_row(row)
        slug = spec["slug"]
        assert slug == "egypt-2011-tahrir-square-uprising"

        manifest = await run_batch(
            dataset_path=dataset,
            output_root=output_root,
            config=StubConfig(),
            search_engine=make_search_engine(make_results()),
            llm=ScriptedLLM(**campaign_scripts()),
        )

        assert manifest["completed"] == [slug]
        assert manifest["failed"] == []
        assert manifest["success_count"] == 1

        campaign_dir = output_root / slug
        for filename in (
            "campaign_dossier.json",
            "campaign_report.md",
            "coded_row.csv",
            "baseline_row.csv",
            "evidence_log.csv",
            "verification_diff.csv",
            f"{slug}.jsonl",  # per-campaign audit log
        ):
            assert (campaign_dir / filename).exists(), filename
        assert (output_root / "batch_manifest.json").exists()

        # coded_row.csv is codebook-shaped with identity fields from the
        # spec (normalize_coded_row overrides them from the CSV row).
        with (campaign_dir / "coded_row.csv").open(encoding="utf-8") as f:
            coded_rows = list(csv.DictReader(f))
        assert len(coded_rows) == 1
        assert list(coded_rows[0].keys()) == DATASET_COLUMNS
        assert coded_rows[0]["country"] == "Egypt"
        assert coded_rows[0]["campaign_name"] == "Tahrir Square Uprising"
        assert coded_rows[0]["year_start"] == "2011"

        # verification_diff.csv covers every codebook column.
        with (campaign_dir / "verification_diff.csv").open(encoding="utf-8") as f:
            diff_rows = list(csv.DictReader(f))
        assert len(diff_rows) == len(DATASET_COLUMNS)

    async def test_skips_existing_campaign_dirs(self, tmp_path):
        dataset = tmp_path / "master_dataset.csv"
        row = write_master_csv(dataset)
        output_root = tmp_path / "batch_out"
        slug = build_campaign_spec_from_row(row)["slug"]
        (output_root / slug).mkdir(parents=True)

        manifest = await run_batch(
            dataset_path=dataset,
            output_root=output_root,
            config=StubConfig(),
            search_engine=make_search_engine(make_results()),
            llm=ScriptedLLM(**campaign_scripts()),
        )

        assert manifest["skipped"] == [slug]
        assert manifest["completed"] == []


# ---------------------------------------------------------------------------
# Batch index rebuild
# ---------------------------------------------------------------------------


class TestRebuildBatchIndex:
    """rebuild_batch_index regenerates summary CSVs from disk artifacts."""

    def _write_campaign_dir(self, root: Path, slug: str, country: str) -> None:
        campaign_dir = root / slug
        campaign_dir.mkdir(parents=True)
        coded_row = {col: "NA" for col in DATASET_COLUMNS}
        coded_row["country"] = country
        coded_row["campaign_name"] = slug
        (campaign_dir / "campaign_dossier.json").write_text(
            json.dumps({"coded_row": coded_row}), encoding="utf-8"
        )
        with (campaign_dir / "verification_diff.csv").open(
            "w", newline="", encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "field_name", "baseline_value", "researched_value", "status",
                ],
            )
            writer.writeheader()
            writer.writerow({
                "field_name": "outcome",
                "baseline_value": "NA",
                "researched_value": "1.0",
                "status": "filled",
            })

    def test_regenerates_summary_csvs(self, tmp_path):
        root = tmp_path / "batch_out"
        self._write_campaign_dir(root, "egypt-2011-tahrir", "Egypt")
        self._write_campaign_dir(root, "tunisia-2010-jasmine", "Tunisia")

        rebuild_batch_index(root)

        with (root / "verification_summary.csv").open(encoding="utf-8") as f:
            summary = list(csv.DictReader(f))
        assert len(summary) == 2
        assert {r["campaign_slug"] for r in summary} == {
            "egypt-2011-tahrir", "tunisia-2010-jasmine",
        }
        assert {r["country"] for r in summary} == {"Egypt", "Tunisia"}

        with (root / "verification_field_diffs.csv").open(encoding="utf-8") as f:
            diffs = list(csv.DictReader(f))
        assert len(diffs) == 2
        assert all(d["field_name"] == "outcome" for d in diffs)
        assert all(d["campaign_slug"] for d in diffs)

        manifest = json.loads(
            (root / "batch_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["total_campaigns"] == 2
        assert sorted(manifest["campaigns"]) == [
            "egypt-2011-tahrir", "tunisia-2010-jasmine",
        ]
