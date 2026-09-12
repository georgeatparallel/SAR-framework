"""End-to-end campaign pipeline test.

Runs the campaign pipeline with mock LLM and verifies structured
dossier output, codebook validation, and file I/O.
"""

# TODO(tests): test campaign pipeline produces valid CampaignDossier
# TODO(tests): test coded_row passes validate_coded_row with no warnings
# TODO(tests): test evidence_log has entries for KEY_FIELDS
# TODO(tests): test batch runner writes expected file structure
# TODO(tests): test compare_with_baseline classifies diffs correctly
# TODO(tests): test rebuild_batch_index regenerates summary CSVs
