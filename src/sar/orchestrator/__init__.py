"""Pipeline orchestration -- explicit state machine with gate-controlled transitions.

Owns the pipeline lifecycle:
- State machine definition with named phases (machine.py)
- Gate functions for transition control (gates.py)
- Pipeline runner that drives the state machine (runner.py)

The orchestrator depends only on contracts and calls into search, llm,
and persistence services through injected interfaces.
"""

from sar.orchestrator.machine import InvalidTransitionError, PipelineMachine
from sar.orchestrator.runner import run_campaign_pipeline, run_pipeline

__all__ = [
    "PipelineMachine",
    "InvalidTransitionError",
    "run_pipeline",
    "run_campaign_pipeline",
]
