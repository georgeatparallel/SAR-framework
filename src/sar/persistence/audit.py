"""Audit log: append-only structured JSONL log per pipeline run.

Every significant event in a pipeline run is recorded: LLM calls,
search queries, gate decisions, errors, and timing. The audit log
is the single source of truth for debugging and transparency.

New in v2 -- replaces scattered logger.info() calls.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:
    """Append-only JSONL audit log for a single pipeline run.

    One log file per run, stored as newline-delimited JSON.
    Each entry has a timestamp, event type, and data payload.

    The log file is created lazily on first write.

    Attributes:
        run_id: Unique identifier for this pipeline run.
        log_path: Path to the JSONL log file.
    """

    def __init__(self, run_id: str, log_dir: str | Path = "logs/audit") -> None:
        """Initialize the audit log for a run.

        Creates the log directory if it doesn't exist.

        Args:
            run_id: Unique run identifier (used as filename).
            log_dir: Directory to store audit log files.
        """
        self._run_id = run_id
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / f"{run_id}.jsonl"

    @property
    def run_id(self) -> str:
        """Unique identifier for this pipeline run."""
        return self._run_id

    @property
    def log_path(self) -> Path:
        """Path to the JSONL log file."""
        return self._log_path

    def record(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Append a structured event to the log.

        Each entry is a single JSON object on one line, containing a UTC
        timestamp, the run ID, event type, and arbitrary data payload.

        Args:
            event_type: Event classification (e.g., "llm_call", "gate_decision",
                        "search_query", "error", "phase_transition").
            data: Arbitrary JSON-serializable payload.
        """
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": self._run_id,
            "event_type": event_type,
            "data": data or {},
        }
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def record_llm_call(
        self,
        agent_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        tool_calls: list[str] | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Convenience: record an LLM API call.

        Args:
            agent_name: Which agent made the call.
            model: Model identifier used.
            input_tokens: Prompt tokens consumed.
            output_tokens: Completion tokens generated.
            tool_calls: Names of tools called (if any).
        """
        self.record("llm_call", {
            "agent_name": agent_name,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls": tool_calls or [],
            **(extra or {}),
            })

    def record_gate_decision(
        self,
        gate_name: str,
        passed: bool,
        reason: str,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Convenience: record a gate check result.

        Accepts either raw arguments or a GateDecision object's fields.

        Args:
            gate_name: Gate identifier.
            passed: Whether the gate allowed the transition.
            reason: Human-readable explanation.
            metrics: Quantitative data behind the decision.
        """
        self.record("gate_decision", {
            "gate_name": gate_name,
            "passed": passed,
            "reason": reason,
            "metrics": metrics or {},
        })

    def record_search(
        self,
        query: str,
        backends: list[str],
        result_count: int,
        elapsed: float,
    ) -> None:
        """Convenience: record a search operation.

        Args:
            query: The search query string.
            backends: Names of backends queried.
            result_count: Number of results returned.
            elapsed: Wall-clock seconds for the search.
        """
        self.record("search", {
            "query": query,
            "backends": backends,
            "result_count": result_count,
            "elapsed_seconds": elapsed,
        })

    def record_error(
        self,
        error_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Convenience: record an error.

        Args:
            error_type: Exception class name or error category.
            message: Error message.
            context: Additional context (e.g., agent name, phase).
        """
        self.record("error", {
            "error_type": error_type,
            "message": message,
            "context": context or {},
        })

    def dump(self) -> list[dict[str, Any]]:
        """Read and return all log entries.

        Returns:
            List of parsed log entry dicts, in chronological order.
        """
        if not self._log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(self._log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
