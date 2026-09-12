"""NA-collision matrix for campaign normalizers.

The shared NA vocabulary (NA_TOKENS) contains "none", which collides
with fields where "none" is a semantic zero. normalize_repression and
normalize_binary consult their own mapping BEFORE the NA check, so
"none" codes as zero there; normalize_trivalued keeps the NA-first
order, so "none" stays missing for trivalued fields.
"""

import pytest

from sar.campaign.schema import NA_TOKENS
from sar.campaign.validators import (
    normalize_binary,
    normalize_repression,
    normalize_trivalued,
    validate_coded_row,
)

# ---------------------------------------------------------------------------
# normalize_repression
# ---------------------------------------------------------------------------


class TestNormalizeRepressionNaCollision:
    """"none" is repression level 0, not a missing value."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            # The NA collision, resolved in favor of the scale's zero point.
            ("none", "0"),
            ("None", "0"),
            ("NONE", "0"),
            ("no repression", "0"),
            # Genuine NA tokens still normalize to NA.
            ("unknown", "NA"),
            ("", "NA"),
            (None, "NA"),
            ("n/a", "NA"),
            ("na", "NA"),
            ("null", "NA"),
            ("not available", "NA"),
            # Direct scale values.
            ("0", "0"),
            ("2", "2"),
            ("severe", "3"),
            # Numeric fallback clamps to [0, 3].
            ("7", "3"),
            ("-2", "0"),
            ("1.6", "2"),
            # Unmapped text passes through verbatim.
            ("weird text", "weird text"),
            ("Sporadic Clashes", "Sporadic Clashes"),
        ],
    )
    def test_matrix(self, raw, expected):
        assert normalize_repression(raw) == expected

    def test_none_token_is_in_shared_na_vocabulary(self):
        """The collision is real: "none" IS an NA token; mapping wins anyway."""
        assert "none" in NA_TOKENS
        assert normalize_repression("none") == "0"


# ---------------------------------------------------------------------------
# normalize_binary
# ---------------------------------------------------------------------------


class TestNormalizeBinaryNaCollision:
    """"none" is a semantic zero for binary fields, not a missing value."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            # The NA collision, resolved in favor of 0.0.
            ("none", "0.0"),
            ("None", "0.0"),
            ("absent", "0.0"),
            # Genuine NA tokens still normalize to NA.
            ("unknown", "NA"),
            ("", "NA"),
            ("na", "NA"),
            ("n/a", "NA"),
            # Prefix fallbacks for verbose LLM output.
            ("none at all", "0.0"),
            ("no shutdown reported", "0.0"),
            ("yes indeed", "1.0"),
            # Plain mapping.
            ("yes", "1.0"),
            ("0", "0.0"),
            ("1", "1.0"),
        ],
    )
    def test_matrix(self, raw, expected):
        assert normalize_binary(raw) == expected


# ---------------------------------------------------------------------------
# normalize_trivalued
# ---------------------------------------------------------------------------


class TestNormalizeTrivaluedKeepsNaFirst:
    """Trivalued fields keep NA-first semantics: "none" stays missing."""

    def test_none_is_na(self):
        """Unchanged semantics -- this must NOT start coding as 0.0."""
        assert normalize_trivalued("none") == "NA"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("None", "NA"),
            ("unknown", "NA"),
            ("", "NA"),
            ("no", "0.0"),  # explicit negation still codes as zero
            ("yes", "1.0"),
            ("partial", "0.5"),
        ],
    )
    def test_surrounding_semantics_hold(self, raw, expected):
        assert normalize_trivalued(raw) == expected


# ---------------------------------------------------------------------------
# validate_coded_row: repression_level checks
# ---------------------------------------------------------------------------


def _minimal_valid_row() -> dict[str, str]:
    """A row with only trivalued/binary keys absent so they don't distract."""
    return {}


class TestValidateCodedRowRepression:
    """repression_level must be one of {0, 1, 2, 3, NA} when present."""

    def test_flags_out_of_scale_value(self):
        issues = validate_coded_row({"repression_level": "5"})
        assert "invalid code for repression_level: 5" in issues

    @pytest.mark.parametrize("value", ["0", "1", "2", "3", "NA"])
    def test_passes_valid_values(self, value):
        issues = validate_coded_row({"repression_level": value})
        assert not any("repression_level" in issue for issue in issues)

    def test_absent_key_not_flagged(self):
        issues = validate_coded_row(_minimal_valid_row())
        assert not any("repression_level" in issue for issue in issues)

    def test_raw_none_string_would_be_flagged(self):
        """An unnormalized "none" is invalid -- normalization must run first."""
        issues = validate_coded_row({"repression_level": "none"})
        assert "invalid code for repression_level: none" in issues
