"""Campaign field normalization and validation.

Normalizes raw LLM output values to strict codebook-compliant formats.
Every coded_row field passes through the appropriate normalizer before
persistence. Validation errors are collected, not raised -- the caller
decides how to handle them.

Migrated from: validate_coded_row_values(), _normalize_*() functions
in agentic_sar/campaign_service.py
"""

from __future__ import annotations

import logging
from typing import Any

from sar.campaign.schema import (
    _DATASET_COLUMNS_SET,
    BINARY_FIELDS,
    CATEGORICAL_FIELDS,
    DATASET_COLUMNS,
    NA_TOKENS,
    NUMERIC_FIELDS,
    TRIVALUED_FIELDS,
)

logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    """Coerce to stripped string; None becomes empty string.

    Args:
        value: Any raw value.

    Returns:
        Stripped string representation.
    """
    if value is None:
        return ""
    return str(value).strip()


def _is_na(value: Any) -> bool:
    """Check whether a value represents missing data.

    Args:
        value: Any raw value.

    Returns:
        True if the value is a recognized NA token.
    """
    return _text(value).lower() in NA_TOKENS


def normalize_trivalued(value: Any) -> str:
    """Normalize a value to {0.0, 0.5, 1.0, NA}.

    Handles common LLM output variations: "yes" -> "1.0", "no" -> "0.0",
    "partial" -> "0.5", empty -> "NA", numeric strings, etc.

    Args:
        value: Raw value from LLM output.

    Returns:
        One of "0.0", "0.5", "1.0", or "NA".
    """
    text = _text(value).lower()
    if text in NA_TOKENS:
        return "NA"

    mapping: dict[str, str] = {
        "0": "0.0",
        "0.0": "0.0",
        "no": "0.0",
        "false": "0.0",
        "absent": "0.0",
        "failure": "0.0",
        "1": "1.0",
        "1.0": "1.0",
        "yes": "1.0",
        "true": "1.0",
        "present": "1.0",
        "success": "1.0",
        "confirmed": "1.0",
        "0.5": "0.5",
        "mixed": "0.5",
        "partial": "0.5",
        "unclear": "0.5",
        "moderate": "0.5",
        "some": "0.5",
    }
    if text in mapping:
        return mapping[text]

    # Prefix matching for verbose LLM responses
    if text.startswith("yes"):
        return "1.0"
    if text.startswith("no"):
        return "0.0"

    # Numeric fallback: bucket to nearest trivalued anchor
    try:
        num = float(text)
        if num <= 0.25:
            return "0.0"
        if num >= 0.75:
            return "1.0"
        return "0.5"
    except (ValueError, TypeError):
        pass

    return _text(value)


def normalize_binary(value: Any) -> str:
    """Normalize a value to {0.0, 1.0, NA}.

    Args:
        value: Raw value from LLM output.

    Returns:
        One of "0.0", "1.0", or "NA".
    """
    text = _text(value).lower()
    mapping: dict[str, str] = {
        "0": "0.0",
        "0.0": "0.0",
        "no": "0.0",
        "false": "0.0",
        "none": "0.0",
        "absent": "0.0",
        "1": "1.0",
        "1.0": "1.0",
        "yes": "1.0",
        "true": "1.0",
        "present": "1.0",
    }
    if text in mapping:
        return mapping[text]
    # The shared NA vocabulary is consulted only after the field's own
    # mapping: "none" is a semantic zero for a binary field, not a
    # missing value. Genuine NA tokens ("", "na", "unknown") still land here.
    if text in NA_TOKENS:
        return "NA"


    if text.startswith("yes"):
        return "1.0"
    if text.startswith("no") or text.startswith("none"):
        return "0.0"

    return _text(value)


def normalize_numeric(value: Any) -> str:
    """Normalize a numeric value to a bare number string or NA.

    Strips units, commas, approximate prefixes, and multiplier words.
    Returns "NA" if the value cannot be parsed as a number.

    Args:
        value: Raw value (e.g., "~150,000", "300 days", "1.2 million").

    Returns:
        Bare numeric string (e.g., "150000", "300", "1200000") or "NA".
    """
    text = _text(value)
    if _is_na(text):
        return "NA"

    # Fast path: already numeric
    try:
        number = float(text)
        if number.is_integer():
            return str(int(number))
        return format(number, ".15g")
    except (TypeError, ValueError):
        pass

    # Slow path: strip formatting
    s = text

    # Remove approximate prefixes
    import re

    s = re.sub(r"^[~\u2248><=]+\s*", "", s)

    # Handle multiplier words
    multiplier = 1.0
    if re.search(r"million", s, re.I):
        multiplier = 1_000_000
        s = re.sub(r"\s*million\s*", "", s, flags=re.I)
    elif re.search(r"billion", s, re.I):
        multiplier = 1_000_000_000
        s = re.sub(r"\s*billion\s*", "", s, flags=re.I)
    elif re.search(r"thousand", s, re.I):
        multiplier = 1_000
        s = re.sub(r"\s*thousand\s*", "", s, flags=re.I)

    # Remove percentage sign and trailing units
    s = s.rstrip("%").strip()
    s = s.replace(",", "")
    s = re.sub(r"\s*[a-zA-Z]+\s*$", "", s)
    s = s.strip()

    try:
        number = float(s) * multiplier
        if number.is_integer():
            return str(int(number))
        return format(number, ".15g")
    except (ValueError, TypeError):
        logger.warning("Could not parse numeric value: %r", value)
        return "NA"


def normalize_repression(value: Any) -> str:
    """Normalize repression level to {0, 1, 2, 3, NA}.

    Scale: 0 = none, 1 = mild (warnings, fines, limited arrests),
    2 = moderate (mass arrests, tear gas), 3 = severe (killings, massacres).

    Args:
        value: Raw repression value from LLM output.

    Returns:
        One of "0", "1", "2", "3", or "NA".
    """
    text = _text(value).lower()
    mapping: dict[str, str] = {
        "0": "0",
        "1": "1",
        "2": "2",
        "3": "3",
        "none": "0",
        "no repression": "0",
        "peaceful": "0",
        "low": "1",
        "limited": "1",
        "mild": "1",
        "light": "1",
        "minimal": "1",
        "moderate": "2",
        "medium": "2",
        "significant": "2",
        "high": "3",
        "severe": "3",
        "heavy": "3",
        "extreme": "3",
        "brutal": "3",
        "lethal": "3",
    }
    if text in mapping:
        return mapping[text]
    # NA check deliberately follows the mapping: "none" is repression
    # level 0 (the scale's own zero point), not a missing value. The
    # collision with NA_TOKENS made the mapping's "none" entry unreachable.
    if text in NA_TOKENS:
        return "NA"


    # Numeric fallback
    try:
        v = int(round(float(text)))
        v = max(0, min(3, v))
        return str(v)
    except (ValueError, TypeError):
        pass

    return _text(value)


def normalize_categorical(value: Any, allowed: list[str]) -> str:
    """Normalize a categorical value against an allowed list.

    Tries exact match (case-insensitive), then substring containment.
    Returns "NA" if no reasonable match is found.

    Args:
        value: Raw value from LLM output.
        allowed: List of valid category values for this field.

    Returns:
        The matched allowed value, or "NA" if no match.
    """
    text = _text(value)
    if _is_na(text):
        return "NA"

    lower = text.lower()

    # Exact match (case-insensitive)
    for a in allowed:
        if lower == a.lower():
            return a

    # Substring containment
    for a in allowed:
        if lower in a.lower() or a.lower() in lower:
            return a

    logger.warning("No categorical match for %r in %s", value, allowed)
    return "NA"


def normalize_coded_row(
    coded_row: dict[str, Any],
    campaign_spec: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Normalize all fields in a coded row to codebook-compliant values.

    Dispatches each field to the appropriate type-specific normalizer.
    Optionally overrides identity fields (country, campaign_name, year_start,
    region) from the campaign specification.

    Args:
        coded_row: Raw field values from LLM extraction.
        campaign_spec: Optional campaign specification dict for overrides.

    Returns:
        Normalized dict with all DATASET_COLUMNS as keys.
    """
    normalized: dict[str, str] = {}

    for column in DATASET_COLUMNS:
        value = coded_row.get(column, "NA")

        if column in NUMERIC_FIELDS:
            normalized[column] = normalize_numeric(value)
        elif column in TRIVALUED_FIELDS:
            normalized[column] = normalize_trivalued(value)
        elif column in BINARY_FIELDS:
            normalized[column] = normalize_binary(value)
        elif column == "repression_level":
            normalized[column] = normalize_repression(value)
        elif column in CATEGORICAL_FIELDS:
            normalized[column] = normalize_categorical(
                value, CATEGORICAL_FIELDS[column]
            )
        else:
            text = _text(value)
            normalized[column] = "NA" if _is_na(text) else text

    # Override identity fields from campaign spec if provided
    if campaign_spec:
        if "country" in campaign_spec:
            normalized["country"] = campaign_spec["country"]
        if "campaign_name" in campaign_spec:
            normalized["campaign_name"] = campaign_spec["campaign_name"]
        if "year_start" in campaign_spec:
            normalized["year_start"] = normalize_numeric(
                campaign_spec["year_start"]
            )
        if campaign_spec.get("region"):
            normalized["region"] = campaign_spec["region"]

    return normalized


def validate_coded_row(row: dict[str, str]) -> list[str]:
    """Validate an entire coded row against the codebook schema.

    Checks that trivalued, binary, categorical, and numeric fields
    contain permitted values. Collects warnings for fields that fail.
    Does NOT normalize in place -- call normalize_coded_row() first.

    Args:
        row: Column name -> value mapping (should already be normalized).

    Returns:
        List of warning strings for fields that failed validation.
        Empty list means all fields are valid.
    """
    issues: list[str] = []

    # Check trivalued fields
    for field in TRIVALUED_FIELDS:
        value = _text(row.get(field, "NA"))
        if value not in {"0.0", "0.5", "1.0", "NA"}:
            issues.append(f"invalid code for {field}: {value}")

    # Check binary fields
    for field in BINARY_FIELDS:
        value = _text(row.get(field, "NA"))
        if value not in {"0.0", "1.0", "NA"}:
            issues.append(f"invalid code for {field}: {value}")

    # Check the repression scale (its own field: ordinal 0-3 or NA)
    if "repression_level" in row:
        value = _text(row.get("repression_level", "NA"))
        if value not in {"0", "1", "2", "3", "NA"}:
            issues.append(f"invalid code for repression_level: {value}")

    # Check categorical fields
    for field, allowed in CATEGORICAL_FIELDS.items():
        value = _text(row.get(field, "NA"))
        allowed_set = set(allowed)
        if value not in allowed_set:
            issues.append(f"invalid code for {field}: {value}")

    # Check numeric fields
    for field in NUMERIC_FIELDS:
        value = _text(row.get(field, "NA"))
        if value == "NA":
            continue
        try:
            float(value)
        except ValueError:
            issues.append(f"non-numeric value for {field}: {value}")

    # Check for unknown fields
    for field in row:
        if field not in _DATASET_COLUMNS_SET:
            issues.append(f"unknown field: {field}")

    return issues


def validate_campaign_dossier(dossier: dict[str, Any]) -> list[str]:
    """Validate a full campaign dossier structure.

    Checks that the inclusion memo, coded row, and evidence log are
    present and contain the required fields. When included=yes, also
    validates coded row values and checks evidence coverage of KEY_FIELDS.

    Args:
        dossier: Raw dossier dict from the synthesizer agent.

    Returns:
        List of issue strings. Empty means the dossier is valid.
    """
    from sar.campaign.schema import KEY_FIELDS

    issues: list[str] = []
    inclusion_memo = dossier.get("inclusion_memo") or {}
    coded_row = dossier.get("coded_row") or {}
    evidence_log = dossier.get("evidence_log") or []

    # Inclusion memo completeness
    for field in (
        "campaign_name",
        "country",
        "start_date",
        "end_date",
        "included",
        "reason_for_inclusion_or_exclusion",
        "notes_on_scope_ambiguity",
    ):
        if not str(inclusion_memo.get(field, "")).strip():
            issues.append(f"missing inclusion memo field: {field}")

    # Coded row completeness
    for column in DATASET_COLUMNS:
        if column not in coded_row:
            issues.append(f"missing coded row field: {column}")

    # Value validation (only for included campaigns)
    # included may arrive as bool True (Pydantic model) or string "yes" (raw dict from LLM)
    _included_raw = inclusion_memo.get("included", "")
    _is_included = _included_raw is True or str(_included_raw).strip().lower() == "yes"
    if _is_included:
        issues.extend(validate_coded_row(coded_row))

        # Evidence coverage for key fields
        evidence_fields = {
            row.get("field_name", "") for row in evidence_log
        }
        for field in KEY_FIELDS:
            if field not in evidence_fields:
                issues.append(
                    f"missing evidence log coverage for: {field}"
                )

    return issues
