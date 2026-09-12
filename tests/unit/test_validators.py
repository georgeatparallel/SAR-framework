"""Tests for campaign field normalization and validation."""

import pytest

from sar.campaign.schema import CATEGORICAL_FIELDS, DATASET_COLUMNS
from sar.campaign.validators import (
    normalize_binary,
    normalize_categorical,
    normalize_coded_row,
    normalize_numeric,
    normalize_repression,
    normalize_trivalued,
    validate_campaign_dossier,
    validate_coded_row,
)

# ---------------------------------------------------------------------------
# normalize_trivalued
# ---------------------------------------------------------------------------


class TestNormalizeTrivalued:
    """Tests for trivalued normalization (yes/no/partial → 1.0/0.0/0.5)."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("yes", "1.0"),
            ("Yes", "1.0"),
            ("YES", "1.0"),
            ("no", "0.0"),
            ("No", "0.0"),
            ("partial", "0.5"),
            ("Partial", "0.5"),
            ("", "NA"),
            ("NA", "NA"),
            ("n/a", "NA"),
            ("unknown", "NA"),
            ("none", "NA"),
            ("null", "NA"),
            ("not available", "NA"),
        ],
    )
    def test_normalize_trivalued_when_common_labels(self, raw, expected):
        """Common text labels map to the correct trivalued code."""
        assert normalize_trivalued(raw) == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("1", "1.0"),
            ("0", "0.0"),
            ("0.5", "0.5"),
            ("1.0", "1.0"),
            ("0.0", "0.0"),
        ],
    )
    def test_normalize_trivalued_when_numeric_strings(self, raw, expected):
        """Numeric string inputs map to the correct trivalued code."""
        assert normalize_trivalued(raw) == expected

    def test_normalize_trivalued_when_numeric_bucket_low(self):
        """A float ≤ 0.25 buckets to 0.0."""
        assert normalize_trivalued("0.2") == "0.0"
        assert normalize_trivalued("0.1") == "0.0"

    def test_normalize_trivalued_when_numeric_bucket_mid(self):
        """A float between 0.25 and 0.75 buckets to 0.5."""
        assert normalize_trivalued("0.3") == "0.5"
        assert normalize_trivalued("0.6") == "0.5"

    def test_normalize_trivalued_when_numeric_bucket_high(self):
        """A float ≥ 0.75 buckets to 1.0."""
        assert normalize_trivalued("0.8") == "1.0"
        assert normalize_trivalued("0.9") == "1.0"

    def test_normalize_trivalued_when_prefix_match_yes(self):
        """Verbose yes-prefix maps to 1.0."""
        assert normalize_trivalued("yes please") == "1.0"
        assert normalize_trivalued("yes, confirmed") == "1.0"

    def test_normalize_trivalued_when_prefix_match_no(self):
        """Verbose no-prefix maps to 0.0."""
        assert normalize_trivalued("no evidence") == "0.0"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("true", "1.0"),
            ("false", "0.0"),
            ("present", "1.0"),
            ("absent", "0.0"),
            ("success", "1.0"),
            ("failure", "0.0"),
            ("confirmed", "1.0"),
            ("mixed", "0.5"),
            ("unclear", "0.5"),
            ("some", "0.5"),
            ("moderate", "0.5"),
        ],
    )
    def test_normalize_trivalued_when_synonym_labels(self, raw, expected):
        """Alternative synonym labels map correctly."""
        assert normalize_trivalued(raw) == expected

    def test_normalize_trivalued_when_none_input(self):
        """None input produces NA."""
        assert normalize_trivalued(None) == "NA"


# ---------------------------------------------------------------------------
# normalize_binary
# ---------------------------------------------------------------------------


class TestNormalizeBinary:
    """Tests for binary normalization (true/false → 1.0/0.0)."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("true", "1.0"),
            ("True", "1.0"),
            ("false", "0.0"),
            ("False", "0.0"),
            ("1", "1.0"),
            ("0", "0.0"),
            ("1.0", "1.0"),
            ("0.0", "0.0"),
            ("yes", "1.0"),
            ("no", "0.0"),
            ("present", "1.0"),
            ("absent", "0.0"),
        ],
    )
    def test_normalize_binary_when_known_values(self, raw, expected):
        """Known binary labels map to the correct code."""
        assert normalize_binary(raw) == expected

    @pytest.mark.parametrize("raw", ["", "NA", "n/a", "unknown", "null"])
    def test_normalize_binary_when_na_tokens(self, raw):
        """NA-like values produce NA."""
        assert normalize_binary(raw) == "NA"

    def test_normalize_binary_when_prefix_yes(self):
        """Verbose yes-prefix maps to 1.0."""
        assert normalize_binary("yes it was blocked") == "1.0"

    def test_normalize_binary_when_prefix_no(self):
        """Verbose no-prefix maps to 0.0."""
        assert normalize_binary("no shutdown") == "0.0"

    def test_normalize_binary_when_none_prefix(self):
        """'none' prefix maps to 0.0."""
        assert normalize_binary("none observed") == "0.0"


# ---------------------------------------------------------------------------
# normalize_numeric
# ---------------------------------------------------------------------------


class TestNormalizeNumeric:
    """Tests for numeric normalization (strip units, commas, multipliers)."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("~150,000", "150000"),
            ("300 days", "300"),
            ("unknown", "NA"),
            ("1.2 million", "1200000"),
            ("3.14", "3.14"),
            ("", "NA"),
            ("NA", "NA"),
            ("42", "42"),
            ("0", "0"),
            ("1000", "1000"),
        ],
    )
    def test_normalize_numeric_when_common_inputs(self, raw, expected):
        """Common numeric formats map to bare number strings."""
        assert normalize_numeric(raw) == expected

    def test_normalize_numeric_when_million_multiplier(self):
        """Million multiplier is applied correctly."""
        assert normalize_numeric("2.5 million") == "2500000"

    def test_normalize_numeric_when_billion_multiplier(self):
        """Billion multiplier is applied correctly."""
        assert normalize_numeric("1 billion") == "1000000000"

    def test_normalize_numeric_when_approximate_prefix(self):
        """Approximate prefix (~, >, <) is stripped."""
        assert normalize_numeric("~500") == "500"
        assert normalize_numeric(">100") == "100"

    def test_normalize_numeric_when_percent_suffix(self):
        """Percent sign is stripped."""
        assert normalize_numeric("85%") == "85"

    def test_normalize_numeric_when_none_input(self):
        """None input produces NA."""
        assert normalize_numeric(None) == "NA"

    def test_normalize_numeric_when_unparseable(self):
        """Completely unparseable text produces NA."""
        assert normalize_numeric("lots of people") == "NA"

    def test_normalize_numeric_when_integer_float(self):
        """A float with no fractional part is returned as int string."""
        assert normalize_numeric("100.0") == "100"


# ---------------------------------------------------------------------------
# normalize_repression
# ---------------------------------------------------------------------------


class TestNormalizeRepression:
    """Tests for repression level normalization (none..severe → 0..3)."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("severe", "3"),
            ("none", "0"),
            ("moderate", "2"),
            ("mild", "1"),
            ("2", "2"),
            ("0", "0"),
            ("3", "3"),
            ("low", "1"),
            ("high", "3"),
            ("extreme", "3"),
            ("peaceful", "0"),
            ("limited", "1"),
            ("significant", "2"),
            ("brutal", "3"),
            ("lethal", "3"),
        ],
    )
    def test_normalize_repression_when_known_values(self, raw, expected):
        """Known repression labels and integers map correctly."""
        assert normalize_repression(raw) == expected

    @pytest.mark.parametrize("raw", ["", "NA", "n/a", "unknown"])
    def test_normalize_repression_when_na_tokens(self, raw):
        """NA-like values produce NA."""
        assert normalize_repression(raw) == "NA"

    def test_normalize_repression_when_numeric_float(self):
        """Float values are rounded and clamped to [0, 3]."""
        assert normalize_repression("2.7") == "3"
        assert normalize_repression("1.4") == "1"

    def test_normalize_repression_when_clamped_high(self):
        """Values > 3 are clamped to 3."""
        assert normalize_repression("5") == "3"

    def test_normalize_repression_when_clamped_low(self):
        """Values < 0 are clamped to 0."""
        assert normalize_repression("-1") == "0"


# ---------------------------------------------------------------------------
# normalize_categorical
# ---------------------------------------------------------------------------


class TestNormalizeCategorical:
    """Tests for categorical normalization against allowed-value lists."""

    def test_normalize_categorical_when_exact_match(self):
        """Exact case-insensitive match returns the canonical value."""
        allowed = ["anti_corruption", "democratic_reform", "regime_change", "NA"]
        assert normalize_categorical("anti_corruption", allowed) == "anti_corruption"
        assert normalize_categorical("ANTI_CORRUPTION", allowed) == "anti_corruption"

    def test_normalize_categorical_when_substring_match(self):
        """Substring containment triggers a match."""
        allowed = ["democratic_reform", "regime_change", "NA"]
        assert normalize_categorical("democratic", allowed) == "democratic_reform"

    def test_normalize_categorical_when_no_match(self):
        """No match at all returns NA."""
        allowed = ["anti_corruption", "democratic_reform", "NA"]
        assert normalize_categorical("xyz_nonsense", allowed) == "NA"

    def test_normalize_categorical_when_empty_value(self):
        """Empty input produces NA."""
        allowed = ["anti_corruption", "NA"]
        assert normalize_categorical("", allowed) == "NA"

    def test_normalize_categorical_when_na_token(self):
        """Recognised NA tokens produce NA."""
        allowed = ["anti_corruption", "democratic_reform", "NA"]
        assert normalize_categorical("unknown", allowed) == "NA"

    def test_normalize_categorical_when_region_values(self):
        """Region values from the real codebook match correctly."""
        allowed = CATEGORICAL_FIELDS["region"]
        assert normalize_categorical("MENA", allowed) == "MENA"
        assert normalize_categorical("mena", allowed) == "MENA"
        assert normalize_categorical("East Asia", allowed) == "East Asia"

    def test_normalize_categorical_when_allowed_contains_raw(self):
        """If allowed value is a substring of the raw value, it matches."""
        allowed = ["East Asia", "Latin America", "NA"]
        assert normalize_categorical("East Asia region", allowed) == "East Asia"


# ---------------------------------------------------------------------------
# validate_coded_row
# ---------------------------------------------------------------------------


class TestValidateCodedRow:
    """Tests for coded-row validation against codebook constraints."""

    def _make_valid_row(self) -> dict[str, str]:
        """Build a minimal valid coded row with all fields set to NA or valid values."""
        row: dict[str, str] = {}
        for col in DATASET_COLUMNS:
            row[col] = "NA"
        # Set categorical fields to valid values
        row["demand_type"] = "regime_change"
        row["region"] = "MENA"
        row["repression_level"] = "2"
        # Set trivalued fields to valid values
        row["nonviolent"] = "1.0"
        row["organised"] = "0.5"
        row["external_support"] = "0.0"
        row["outcome"] = "1.0"
        # Set binary fields
        row["platform_blocked"] = "0.0"
        row["internet_shutdown"] = "1.0"
        return row

    def test_validate_coded_row_when_valid_row(self):
        """A fully valid row returns no warnings."""
        row = self._make_valid_row()
        assert validate_coded_row(row) == []

    def test_validate_coded_row_when_invalid_trivalued(self):
        """Invalid trivalued value produces a warning."""
        row = self._make_valid_row()
        row["nonviolent"] = "maybe"
        issues = validate_coded_row(row)
        assert any("nonviolent" in w for w in issues)

    def test_validate_coded_row_when_invalid_binary(self):
        """Invalid binary value produces a warning."""
        row = self._make_valid_row()
        row["internet_shutdown"] = "sometimes"
        issues = validate_coded_row(row)
        assert any("internet_shutdown" in w for w in issues)

    def test_validate_coded_row_when_invalid_categorical(self):
        """Invalid categorical value produces a warning."""
        row = self._make_valid_row()
        row["demand_type"] = "free_pizza"
        issues = validate_coded_row(row)
        assert any("demand_type" in w for w in issues)

    def test_validate_coded_row_when_non_numeric_value(self):
        """A non-numeric string in a numeric field produces a warning."""
        row = self._make_valid_row()
        row["duration_days"] = "many"
        issues = validate_coded_row(row)
        assert any("duration_days" in w for w in issues)

    def test_validate_coded_row_when_unknown_field(self):
        """An extra field not in the codebook produces a warning."""
        row = self._make_valid_row()
        row["alien_field"] = "42"
        issues = validate_coded_row(row)
        assert any("unknown field" in w for w in issues)

    def test_validate_coded_row_when_numeric_field_with_na(self):
        """NA in a numeric field does not produce a warning."""
        row = self._make_valid_row()
        row["duration_days"] = "NA"
        issues = validate_coded_row(row)
        assert not any("duration_days" in w for w in issues)


# ---------------------------------------------------------------------------
# validate_campaign_dossier
# ---------------------------------------------------------------------------


class TestValidateCampaignDossier:
    """Tests for full dossier validation."""

    def _make_valid_dossier(self) -> dict:
        """Build a minimal valid dossier dict."""
        coded_row = {col: "NA" for col in DATASET_COLUMNS}
        coded_row["demand_type"] = "regime_change"
        coded_row["region"] = "MENA"
        coded_row["repression_level"] = "2"
        coded_row["nonviolent"] = "1.0"
        coded_row["organised"] = "0.5"
        coded_row["external_support"] = "0.0"
        coded_row["outcome"] = "1.0"
        coded_row["platform_blocked"] = "0.0"
        coded_row["internet_shutdown"] = "1.0"

        return {
            "inclusion_memo": {
                "campaign_name": "Test Campaign",
                "country": "Testland",
                "start_date": "2020-01-01",
                "end_date": "2020-06-01",
                "included": "yes",
                "reason_for_inclusion_or_exclusion": "Meets criteria",
                "notes_on_scope_ambiguity": "None",
            },
            "coded_row": coded_row,
            "evidence_log": [
                {"field_name": "outcome", "value": "1.0"},
                {"field_name": "duration_days", "value": "120"},
                {"field_name": "repression_level", "value": "2"},
            ],
        }

    def test_validate_campaign_dossier_when_valid(self):
        """A complete, valid dossier returns no issues."""
        dossier = self._make_valid_dossier()
        assert validate_campaign_dossier(dossier) == []

    def test_validate_campaign_dossier_when_missing_inclusion_field(self):
        """Missing inclusion memo field produces an issue."""
        dossier = self._make_valid_dossier()
        del dossier["inclusion_memo"]["country"]
        issues = validate_campaign_dossier(dossier)
        assert any("missing inclusion memo field: country" in i for i in issues)

    def test_validate_campaign_dossier_when_included_bool_true(self):
        """Bool True triggers coded row validation."""
        dossier = self._make_valid_dossier()
        dossier["inclusion_memo"]["included"] = True
        dossier["coded_row"]["nonviolent"] = "invalid"
        issues = validate_campaign_dossier(dossier)
        assert any("nonviolent" in i for i in issues)

    def test_validate_campaign_dossier_when_not_included_skips_row_validation(self):
        """excluded campaign skips coded-row value validation."""
        dossier = self._make_valid_dossier()
        dossier["inclusion_memo"]["included"] = "no"
        dossier["coded_row"]["nonviolent"] = "invalid_value"
        issues = validate_campaign_dossier(dossier)
        # Should NOT contain coded-row validation issues
        assert not any("nonviolent" in i for i in issues)

    def test_validate_campaign_dossier_when_missing_evidence_coverage(self):
        """Missing evidence for a KEY_FIELD produces an issue."""
        dossier = self._make_valid_dossier()
        dossier["evidence_log"] = []  # no evidence at all
        issues = validate_campaign_dossier(dossier)
        assert any("missing evidence log coverage" in i for i in issues)

    def test_validate_campaign_dossier_when_missing_coded_row_field(self):
        """Missing coded_row fields produce issues."""
        dossier = self._make_valid_dossier()
        del dossier["coded_row"]["country"]
        issues = validate_campaign_dossier(dossier)
        assert any("missing coded row field: country" in i for i in issues)


# ---------------------------------------------------------------------------
# normalize_coded_row (integration-level)
# ---------------------------------------------------------------------------


class TestNormalizeCodedRow:
    """Tests for full row normalization dispatch."""

    def test_normalize_coded_row_when_dispatches_trivalued(self):
        """Trivalued fields go through normalize_trivalued."""
        row = {"nonviolent": "yes", "organised": "no"}
        result = normalize_coded_row(row)
        assert result["nonviolent"] == "1.0"
        assert result["organised"] == "0.0"

    def test_normalize_coded_row_when_dispatches_numeric(self):
        """Numeric fields go through normalize_numeric."""
        row = {"duration_days": "~150,000"}
        result = normalize_coded_row(row)
        assert result["duration_days"] == "150000"

    def test_normalize_coded_row_when_campaign_spec_overrides(self):
        """Campaign spec identity fields override coded row values."""
        row = {"country": "wrong"}
        spec = {"country": "Correct", "campaign_name": "My Campaign"}
        result = normalize_coded_row(row, campaign_spec=spec)
        assert result["country"] == "Correct"
        assert result["campaign_name"] == "My Campaign"

    def test_normalize_coded_row_when_all_columns_present(self):
        """Output dict always contains every DATASET_COLUMNS key."""
        result = normalize_coded_row({})
        for col in DATASET_COLUMNS:
            assert col in result
