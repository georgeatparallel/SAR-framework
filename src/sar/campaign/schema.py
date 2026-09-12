"""Campaign codebook schema: column definitions, allowed values, field metadata.

Single source of truth for the protest campaign dataset structure.
Defines all 39 dataset columns, their types, allowed values, and
validation rules. Used by the synthesizer agent (via prompt injection)
and by validators (for post-hoc normalization).

Migrated from: DATASET_COLUMNS, CODEBOOK_ALLOWED_VALUES, KEY_FIELDS,
TRIVALUED_FIELDS, NUMERIC_FIELDS in agentic_sar/campaign_service.py
Updated to match the current master_dataset.csv (39 columns).
"""

from __future__ import annotations

from typing import Any


# --- Dataset column definitions ---
# Full ordered list of codebook columns, matching master_dataset.csv exactly.

DATASET_COLUMNS: list[str] = [
    "country",
    "year_start",
    "campaign_name",
    "demand_type",
    "nonviolent",
    "organised",
    "external_support",
    "prior_campaigns",
    "region",
    "outcome",
    "duration_days",
    "peak_participation",
    "peak_participation_share",
    "onset_to_peak_days",
    "platform_dominant",
    "platform_blocked",
    "repression_level",
    "internet_shutdown",
    "press_freedom_score",
    "rsf_rank",
    "freedom_house_score",
    "fh_pr",
    "fh_cl",
    "polity_score",
    "pts_score",
    "v_dem_libdem",
    "v_dem_polyarchy",
    "internet_penetration",
    "mobile_penetration",
    "urbanisation_rate",
    "gini_coefficient",
    "pop_15_64_pct",
    "pop_0_14_pct",
    "internet_censorship",
    "social_media_penetration",
    "pre_campaign_repression",
    "journalist_arrests",
    "ethnic_fractionalisation",
    "religious_fractionalisation",
]

# Fields that the campaign research pipeline prioritises.
# These receive focused search queries and stronger validation.
KEY_FIELDS: list[str] = [
    "outcome",
    "duration_days",
    "repression_level",
]

# Fields shown in the extraction prompt alongside KEY_FIELDS.
PROMPT_FOCUS_FIELDS: list[str] = [
    "outcome",
    "duration_days",
    "repression_level",
]


# --- Allowed values by field type ---

TRIVALUED_FIELDS: set[str] = {
    "nonviolent",
    "organised",
    "external_support",
    "outcome",
}

BINARY_FIELDS: set[str] = {
    "platform_blocked",
    "internet_shutdown",
}

NUMERIC_FIELDS: set[str] = {
    "year_start",
    "duration_days",
    "peak_participation",
    "peak_participation_share",
    "onset_to_peak_days",
    "press_freedom_score",
    "rsf_rank",
    "freedom_house_score",
    "fh_pr",
    "fh_cl",
    "polity_score",
    "pts_score",
    "v_dem_libdem",
    "v_dem_polyarchy",
    "internet_penetration",
    "mobile_penetration",
    "urbanisation_rate",
    "gini_coefficient",
    "pop_15_64_pct",
    "pop_0_14_pct",
    "internet_censorship",
    "social_media_penetration",
    "pre_campaign_repression",
    "journalist_arrests",
    "ethnic_fractionalisation",
    "religious_fractionalisation",
}

CATEGORICAL_FIELDS: dict[str, list[str]] = {
    "demand_type": [
        "anti_corruption",
        "democratic_reform",
        "policy_reversal",
        "regime_change",
        "NA",
    ],
    "region": [
        "East Asia",
        "Latin America",
        "MENA",
        "Post-Soviet",
        "South Asia",
        "Sub-Saharan Africa",
        "NA",
    ],
    "repression_level": ["0", "1", "2", "3", "NA"],
}

# Tokens that should be interpreted as missing / not available.
NA_TOKENS: set[str] = {"", "na", "n/a", "none", "null", "unknown", "not available"}

# Lookup set for O(1) membership checks.
_DATASET_COLUMNS_SET: set[str] = set(DATASET_COLUMNS)


def get_codebook_prompt_block() -> str:
    """Generate the codebook instructions block for LLM prompts.

    Returns a formatted string describing all fields, their types,
    allowed values, and normalization rules. Injected into the
    campaign synthesizer agent's system prompt.

    Returns:
        Multi-line string with codebook instructions.
    """
    lines: list[str] = ["Codebook constraints for `coded_row`:"]

    # Categorical fields
    for field, allowed in CATEGORICAL_FIELDS.items():
        opts = ", ".join(f"`{v}`" for v in allowed)
        lines.append(f"- `{field}`: one of {opts}.")

    # Trivalued fields
    tri_names = ", ".join(f"`{f}`" for f in sorted(TRIVALUED_FIELDS))
    lines.append(
        f"- {tri_names}: use only `0.0`, `0.5`, `1.0`, or `NA`."
    )

    # Binary fields
    bin_names = ", ".join(f"`{f}`" for f in sorted(BINARY_FIELDS))
    lines.append(f"- {bin_names}: use only `0.0`, `1.0`, or `NA`.")

    # General rules
    lines.append(
        "- Numeric fields must contain bare numbers only. "
        "Do not put prose, units, or ranges in numeric cells."
    )
    lines.append(
        "- Put narrative justification, caveats, and contested "
        "interpretations in `evidence_log`, `uncertainty_note`, "
        "and `report_markdown`, not in `coded_row`."
    )

    return "\n".join(lines)


def get_extraction_schema() -> dict[str, Any]:
    """Generate a JSON extraction schema for campaign reader agents.

    Returns a dict that can be passed to reader.run(extraction_schema=...)
    to instruct the reader on what fields to extract from each source.

    Returns:
        JSON-compatible schema dict for reader agent parametrization.
    """
    properties: dict[str, Any] = {}

    for field in KEY_FIELDS:
        if field in TRIVALUED_FIELDS:
            properties[field] = {
                "type": ["number", "null"],
                "description": "Trivalued: 0.0 (no/failure), 0.5 (partial/mixed), 1.0 (yes/success)",
                "enum": [0.0, 0.5, 1.0, None],
            }
        elif field in CATEGORICAL_FIELDS:
            allowed = CATEGORICAL_FIELDS[field]
            properties[field] = {
                "type": ["string", "null"],
                "description": f"One of: {', '.join(allowed)}",
                "enum": allowed + [None],
            }
        elif field in NUMERIC_FIELDS:
            properties[field] = {
                "type": ["number", "null"],
                "description": "Bare numeric value or null if unknown",
            }
        else:
            properties[field] = {
                "type": ["string", "null"],
                "description": "Free text",
            }

    # Timeline facts for deriving duration_days
    properties["timeline_facts"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "One of: start_date, end_date, other",
                },
                "value": {
                    "type": "string",
                    "description": "YYYY-MM-DD or descriptive text",
                },
                "notes": {"type": "string"},
            },
            "required": ["label", "value"],
        },
    }

    # Scope signal for inclusion/exclusion decision
    properties["scope_signal"] = {
        "type": ["string", "null"],
        "description": "Does this source support inclusion or exclusion and why?",
    }

    return {
        "type": "object",
        "properties": properties,
        "required": list(KEY_FIELDS),
    }


def _get_field_type(field: str) -> str:
    """Get human-readable type label for a field.

    Args:
        field: Column name from DATASET_COLUMNS.

    Returns:
        Short type description string.
    """
    if field in TRIVALUED_FIELDS:
        return "trivalued: 0.0/0.5/1.0"
    if field in BINARY_FIELDS:
        return "binary: 0.0/1.0"
    if field in NUMERIC_FIELDS:
        return "numeric"
    if field in CATEGORICAL_FIELDS:
        return f"categorical: {', '.join(CATEGORICAL_FIELDS[field])}"
    return "text"
