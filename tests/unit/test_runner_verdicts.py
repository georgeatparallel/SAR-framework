"""Tests for runner internals: verdict parsing, gate flattening, transitions."""

from functools import partial

import pytest

from sar.contracts.pipeline import (
    GateDecision,
    IterationRecord,
    PipelinePhase,
    ReviewerVerdict,
)
from sar.orchestrator import gates
from sar.orchestrator.runner import (
    _KNOWN_VERDICT_COMMANDS,
    _flatten_iteration_gates,
    _parse_verdict,
    build_transitions,
)

# ---------------------------------------------------------------------------
# _parse_verdict
# ---------------------------------------------------------------------------


class TestParseVerdict:
    """Command string -> ReviewerVerdict mapping."""

    @pytest.mark.parametrize(
        "command, expected",
        [
            # Tool-name commands
            ("mark_as_complete", ReviewerVerdict.APPROVED),
            ("complete", ReviewerVerdict.APPROVED),
            ("request_rework", ReviewerVerdict.NEEDS_REWORK),
            ("needs_rework", ReviewerVerdict.NEEDS_REWORK),
            ("request_more_info", ReviewerVerdict.NEEDS_MORE_INFO),
            ("needs_more_info", ReviewerVerdict.NEEDS_MORE_INFO),
            # Enum value passthrough
            ("approved", ReviewerVerdict.APPROVED),
            ("failed", ReviewerVerdict.FAILED),
            # Defaults
            (None, ReviewerVerdict.APPROVED),
            ("garbage", ReviewerVerdict.APPROVED),
        ],
    )
    def test_mapping_table(self, command, expected):
        assert _parse_verdict(command) is expected

    @pytest.mark.parametrize(
        "command, expected",
        [
            ("  Mark_As_Complete  ", ReviewerVerdict.APPROVED),
            ("NEEDS_REWORK", ReviewerVerdict.NEEDS_REWORK),
            (" Failed ", ReviewerVerdict.FAILED),
        ],
    )
    def test_case_and_whitespace_insensitive(self, command, expected):
        assert _parse_verdict(command) is expected


class TestKnownVerdictCommands:
    """The audit allowlist covers everything _parse_verdict maps."""

    def test_contains_every_mapped_command_plus_none(self):
        mapped_commands = [
            "mark_as_complete",
            "complete",
            "request_rework",
            "needs_rework",
            "request_more_info",
            "needs_more_info",
        ]
        for command in mapped_commands:
            assert command in _KNOWN_VERDICT_COMMANDS

        # Enum value passthroughs are known too.
        for verdict in ReviewerVerdict:
            assert verdict.value in _KNOWN_VERDICT_COMMANDS

        # None (reviewer returned nothing) is a known non-command.
        assert None in _KNOWN_VERDICT_COMMANDS

    def test_unrecognized_commands_are_not_known(self):
        assert "garbage" not in _KNOWN_VERDICT_COMMANDS


# ---------------------------------------------------------------------------
# _flatten_iteration_gates
# ---------------------------------------------------------------------------


def _decision(name: str) -> GateDecision:
    return GateDecision(gate_name=name, passed=True, reason="ok")


def _record(iteration: int, decisions: list[GateDecision]) -> IterationRecord:
    return IterationRecord(
        iteration=iteration,
        phase=PipelinePhase.REVIEW,
        gate_decisions=decisions,
    )


class TestFlattenIterationGates:
    """Flattening prior iterations' gate decisions for dedup filtering."""

    def test_empty_iterations_flatten_to_empty(self):
        assert _flatten_iteration_gates([]) == []

    def test_concatenates_in_iteration_order(self):
        d1, d2, d3 = _decision("g1"), _decision("g2"), _decision("g3")
        iterations = [_record(1, [d1, d2]), _record(2, [d3])]

        assert _flatten_iteration_gates(iterations) == [d1, d2, d3]

    def test_supports_membership_dedup_as_used_by_runner(self):
        """The runner filters gate_log entries already seen in past iterations."""
        d1, d2, d3 = _decision("g1"), _decision("g2"), _decision("g3")
        iterations = [_record(1, [d1]), _record(2, [d2])]
        gate_log = [d1, d2, d3]

        fresh = [
            g for g in gate_log
            if g not in _flatten_iteration_gates(iterations)
        ]
        assert fresh == [d3]

    def test_preserves_duplicates_within_iterations(self):
        """Flattening itself does not dedup; it is a plain concatenation."""
        d1 = _decision("g1")
        iterations = [_record(1, [d1]), _record(2, [d1])]

        assert _flatten_iteration_gates(iterations) == [d1, d1]


# ---------------------------------------------------------------------------
# build_transitions
# ---------------------------------------------------------------------------


class _ConfigStub:
    """Minimal config carrying only the attrs build_transitions reads."""

    MIN_RESULTS_TO_PROCEED = 7
    QUALITY_THRESHOLD = 6.5


class TestBuildTransitions:
    """Transition-table construction from config."""

    def test_keys_are_the_five_non_review_phases(self):
        table = build_transitions(_ConfigStub())

        assert set(table) == {
            PipelinePhase.EXPAND,
            PipelinePhase.SEARCH,
            PipelinePhase.JUDGE,
            PipelinePhase.READ,
            PipelinePhase.ANALYZE,
        }
        assert PipelinePhase.REVIEW not in table

    def test_targets_and_gate_functions(self):
        table = build_transitions(_ConfigStub())

        assert table[PipelinePhase.EXPAND] == [
            (PipelinePhase.SEARCH, gates.check_queries_generated)
        ]
        assert table[PipelinePhase.READ] == [
            (PipelinePhase.ANALYZE, gates.check_summaries_ready)
        ]
        assert table[PipelinePhase.ANALYZE] == [
            (PipelinePhase.REVIEW, gates.check_report_submitted)
        ]

    def test_thresholds_honor_config_attrs(self):
        table = build_transitions(_ConfigStub())

        search_target, search_gate = table[PipelinePhase.SEARCH][0]
        assert search_target == PipelinePhase.JUDGE
        assert isinstance(search_gate, partial)
        assert search_gate.func is gates.check_results_obtained
        assert search_gate.keywords == {"min_results": 7}

        judge_target, judge_gate = table[PipelinePhase.JUDGE][0]
        assert judge_target == PipelinePhase.READ
        assert isinstance(judge_gate, partial)
        assert judge_gate.func is gates.check_quality_threshold
        assert judge_gate.keywords == {"min_score": 6.5}

    def test_defaults_when_config_lacks_attrs(self):
        """A config without tuning attrs falls back to module defaults."""
        table = build_transitions(object())

        assert table[PipelinePhase.SEARCH][0][1].keywords == {"min_results": 1}
        assert table[PipelinePhase.JUDGE][0][1].keywords == {"min_score": 4.0}
