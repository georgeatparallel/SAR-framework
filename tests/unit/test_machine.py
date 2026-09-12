"""Tests for the pipeline state machine (transitions and gates)."""

import pytest

from sar.contracts.pipeline import GateDecision, PipelinePhase, PipelineState
from sar.orchestrator.machine import InvalidTransitionError, PipelineMachine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _passing_gate(name: str = "pass_gate"):
    """Build a gate function that always passes."""

    def gate(state: PipelineState) -> GateDecision:
        return GateDecision(gate_name=name, passed=True, reason="ok")

    return gate


def _failing_gate(name: str = "fail_gate"):
    """Build a gate function that always fails."""

    def gate(state: PipelineState) -> GateDecision:
        return GateDecision(gate_name=name, passed=False, reason="nope")

    return gate


# ---------------------------------------------------------------------------
# transition()
# ---------------------------------------------------------------------------


class TestTransition:
    """Gate-driven phase transitions."""

    def test_single_passing_gate_moves_phase_and_returns_decision(self):
        state = PipelineState(phase=PipelinePhase.EXPAND)
        machine = PipelineMachine(
            state,
            {PipelinePhase.EXPAND: [(PipelinePhase.SEARCH, _passing_gate())]},
        )

        decision = machine.transition()

        assert machine.current_phase == PipelinePhase.SEARCH
        assert decision is not None
        assert decision.passed is True
        assert decision.gate_name == "pass_gate"
        # The decision is logged on the state.
        assert state.gate_log == [decision]

    def test_first_passing_candidate_wins(self):
        """Candidates are evaluated in order; the first pass is taken."""
        state = PipelineState(phase=PipelinePhase.EXPAND)
        machine = PipelineMachine(
            state,
            {
                PipelinePhase.EXPAND: [
                    (PipelinePhase.SEARCH, _failing_gate("g1")),
                    (PipelinePhase.JUDGE, _passing_gate("g2")),
                    (PipelinePhase.READ, _passing_gate("g3")),
                ]
            },
        )

        decision = machine.transition()

        assert machine.current_phase == PipelinePhase.JUDGE
        assert decision.gate_name == "g2"
        # Both evaluated gates were logged; g3 never ran.
        assert [d.gate_name for d in state.gate_log] == ["g1", "g2"]

    def test_all_candidates_fail_moves_to_failed_returns_last_decision(self):
        state = PipelineState(phase=PipelinePhase.EXPAND)
        machine = PipelineMachine(
            state,
            {
                PipelinePhase.EXPAND: [
                    (PipelinePhase.SEARCH, _failing_gate("g1")),
                    (PipelinePhase.JUDGE, _failing_gate("g2")),
                ]
            },
        )

        decision = machine.transition()

        assert machine.current_phase == PipelinePhase.FAILED
        assert machine.is_terminal is True
        assert decision is not None
        assert decision.passed is False
        assert decision.gate_name == "g2"  # the last gate's decision
        assert [d.gate_name for d in state.gate_log] == ["g1", "g2"]

    def test_none_gate_is_unconditional_and_returns_none(self):
        state = PipelineState(phase=PipelinePhase.ANALYZE)
        machine = PipelineMachine(
            state,
            {PipelinePhase.ANALYZE: [(PipelinePhase.REVIEW, None)]},
        )

        decision = machine.transition()

        assert decision is None
        assert machine.current_phase == PipelinePhase.REVIEW
        assert state.gate_log == []  # nothing was gated, nothing logged

    @pytest.mark.parametrize(
        "terminal_phase", [PipelinePhase.COMPLETE, PipelinePhase.FAILED]
    )
    def test_terminal_phase_raises(self, terminal_phase):
        state = PipelineState(phase=terminal_phase)
        machine = PipelineMachine(
            state,
            {terminal_phase: [(PipelinePhase.EXPAND, None)]},
        )

        assert machine.is_terminal is True
        with pytest.raises(InvalidTransitionError, match="terminal"):
            machine.transition()

    def test_unknown_phase_raises(self):
        """A phase absent from the transition table is an error."""
        state = PipelineState(phase=PipelinePhase.READ)
        machine = PipelineMachine(state, {})

        with pytest.raises(InvalidTransitionError, match="No transition registered"):
            machine.transition()


# ---------------------------------------------------------------------------
# force_phase()
# ---------------------------------------------------------------------------


class TestForcePhase:
    """Runner-driven overrides bypass the gate table entirely."""

    def test_bypasses_failing_gates(self):
        state = PipelineState(phase=PipelinePhase.EXPAND)
        machine = PipelineMachine(
            state,
            {PipelinePhase.EXPAND: [(PipelinePhase.SEARCH, _failing_gate())]},
        )

        machine.force_phase(PipelinePhase.SEARCH)

        assert machine.current_phase == PipelinePhase.SEARCH
        assert state.gate_log == []  # no gate ran

    @pytest.mark.parametrize(
        "terminal_phase", [PipelinePhase.COMPLETE, PipelinePhase.FAILED]
    )
    def test_can_force_into_terminal_states(self, terminal_phase):
        state = PipelineState(phase=PipelinePhase.REVIEW)
        machine = PipelineMachine(state, {})

        machine.force_phase(terminal_phase)

        assert machine.current_phase == terminal_phase
        assert machine.is_terminal is True
        # Once terminal, gate-driven transitions are refused.
        with pytest.raises(InvalidTransitionError):
            machine.transition()

    def test_force_out_of_review_loop(self):
        """The reviewer loop forces REVIEW back to EXPAND."""
        state = PipelineState(phase=PipelinePhase.REVIEW)
        machine = PipelineMachine(state, {})

        machine.force_phase(PipelinePhase.EXPAND)

        assert machine.current_phase == PipelinePhase.EXPAND
        assert machine.is_terminal is False
