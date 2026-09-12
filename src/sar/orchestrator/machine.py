"""Pipeline state machine: phases, transitions, and gate enforcement.

Defines the allowed phase transitions and enforces gate checks at
every boundary. The machine is deterministic -- given the same state
and inputs, it always produces the same transition.

The transition table maps each source phase to a list of candidate
(target_phase, gate_function) pairs. When ``transition()`` is called,
the machine evaluates candidates in order and takes the first one
whose gate passes. If no gate passes, the pipeline moves to FAILED.

Migrated from: implicit if/elif chains in agent_service.py run_agentic_pipeline()
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sar.contracts.pipeline import GateDecision, PipelinePhase, PipelineState

logger = logging.getLogger(__name__)

# Type alias for a single transition entry: (target_phase, gate_function).
# gate_function may be None for unconditional transitions.
TransitionEntry = tuple[PipelinePhase, Callable[[PipelineState], GateDecision] | None]


class InvalidTransitionError(Exception):
    """Raised when an attempted transition is not in the TRANSITIONS table."""

    pass


class PipelineMachine:
    """Explicit state machine for research pipeline orchestration.

    Manages transitions between pipeline phases. Each transition
    optionally passes through a gate function. All transitions
    and gate decisions are logged to the pipeline state's gate_log.

    The machine never transitions silently: every phase change is
    either gated (with a logged GateDecision) or explicitly forced
    via ``force_phase()``.

    Attributes:
        _state: The current pipeline state.
        _transitions: Mapping from source phase to candidate transitions.
    """

    def __init__(
        self,
        state: PipelineState,
        transitions: dict[PipelinePhase, list[TransitionEntry]],
    ) -> None:
        """Initialize the state machine.

        Args:
            state: Starting pipeline state (phase should be EXPAND).
            transitions: Mapping from source phase to ordered list of
                (target_phase, gate_function) candidates.
        """
        self._state = state
        self._transitions = transitions

    @property
    def state(self) -> PipelineState:
        """The current pipeline state.

        Returns:
            Mutable PipelineState.
        """
        return self._state

    @property
    def current_phase(self) -> PipelinePhase:
        """Return the current phase.

        Returns:
            The state machine's current phase.
        """
        return self._state.phase

    @property
    def is_terminal(self) -> bool:
        """Check if the machine is in a terminal state (COMPLETE or FAILED).

        Returns:
            True if no further transitions are possible.
        """
        return self._state.phase in (PipelinePhase.COMPLETE, PipelinePhase.FAILED)

    def transition(self) -> GateDecision | None:
        """Attempt to transition to the next phase.

        Looks up the current phase in the transition table, evaluates
        each candidate gate in order, and takes the first transition
        whose gate passes. If all gates fail, the pipeline moves to
        FAILED using the last gate's decision as the reason.

        For phases with a single unconditional transition (gate is None),
        the transition happens immediately and None is returned.

        Returns:
            The GateDecision if a gate was checked, None if the transition
            was unconditional.

        Raises:
            InvalidTransitionError: If the current phase has no registered
                transitions.
            InvalidTransitionError: If the machine is in a terminal state.
        """
        if self.is_terminal:
            raise InvalidTransitionError(
                f"Cannot transition from terminal phase {self.current_phase.value}"
            )

        candidates = self._transitions.get(self.current_phase)
        if candidates is None:
            raise InvalidTransitionError(
                f"No transition registered for phase {self.current_phase.value}"
            )

        last_decision: GateDecision | None = None

        for target_phase, gate_fn in candidates:
            if gate_fn is None:
                # Unconditional transition
                logger.info(
                    "Transition %s -> %s (unconditional)",
                    self.current_phase.value,
                    target_phase.value,
                )
                self._state.phase = target_phase
                return None

            decision = gate_fn(self._state)
            self._state.gate_log.append(decision)
            logger.info(
                "Gate [%s]: %s -- %s",
                decision.gate_name,
                "PASS" if decision.passed else "FAIL",
                decision.reason,
            )

            if decision.passed:
                logger.info(
                    "Transition %s -> %s",
                    self.current_phase.value,
                    target_phase.value,
                )
                self._state.phase = target_phase
                return decision

            last_decision = decision

        # All gates failed -- move to FAILED
        logger.warning(
            "All gates failed for phase %s -- moving to FAILED",
            self.current_phase.value,
        )
        self._state.phase = PipelinePhase.FAILED
        return last_decision

    def force_phase(self, phase: PipelinePhase) -> None:
        """Force the pipeline to a specific phase (for reviewer loops).

        Used when the runner needs to override normal gate-based transitions,
        such as looping back to EXPAND after a reviewer NEEDS_MORE_INFO
        verdict, or jumping to COMPLETE on the final iteration.

        Args:
            phase: The phase to force the machine into.
        """
        logger.info(
            "Forced transition %s -> %s",
            self.current_phase.value,
            phase.value,
        )
        self._state.phase = phase
