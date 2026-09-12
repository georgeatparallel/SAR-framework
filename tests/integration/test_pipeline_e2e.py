"""End-to-end pipeline test with mock LLM.

Runs the full pipeline with real search backends but a mock LLM
to verify the orchestration flow without incurring API costs.
"""

# TODO(tests): test full pipeline EXPAND -> SEARCH -> JUDGE -> READ -> ANALYZE -> REVIEW -> COMPLETE
# TODO(tests): test pipeline handles NEEDS_REWORK loop correctly
# TODO(tests): test pipeline handles NEEDS_MORE_INFO loop correctly
# TODO(tests): test pipeline respects MAX_ITERATIONS limit
# TODO(tests): test pipeline writes audit log entries for each phase
# TODO(tests): test pipeline returns PipelineResult with all fields populated
