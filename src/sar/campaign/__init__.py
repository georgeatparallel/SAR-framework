"""Campaign-specific domain logic -- pluggable research domain.

Owns all protest-campaign-specific knowledge:
- Codebook schema definitions and allowed values (schema.py)
- Field normalization and validation (validators.py)
- Batch verification runner with diff generation (batch.py)

This module is a pluggable domain -- the orchestrator and agents
are generic; campaign-specific behavior is injected via schema
and validators. A different domain (e.g., company profiles) would
be a sibling module with its own schema.
"""

from sar.campaign.batch import (
    build_campaign_spec_from_row,
    compare_with_baseline,
    rebuild_batch_index,
    run_batch,
)
from sar.campaign.schema import (
    DATASET_COLUMNS,
    KEY_FIELDS,
    PROMPT_FOCUS_FIELDS,
    get_codebook_prompt_block,
    get_extraction_schema,
)
from sar.campaign.validators import (
    normalize_coded_row,
    validate_campaign_dossier,
    validate_coded_row,
)

__all__ = [
    "DATASET_COLUMNS",
    "KEY_FIELDS",
    "PROMPT_FOCUS_FIELDS",
    "build_campaign_spec_from_row",
    "compare_with_baseline",
    "get_codebook_prompt_block",
    "get_extraction_schema",
    "normalize_coded_row",
    "rebuild_batch_index",
    "run_batch",
    "validate_campaign_dossier",
    "validate_coded_row",
]
