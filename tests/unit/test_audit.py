"""Tests for the append-only JSONL audit log."""

import json
from datetime import datetime
from pathlib import Path

from sar.persistence.audit import AuditLog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log(tmp_path: Path, run_id: str = "run-001") -> AuditLog:
    """Create an AuditLog writing under a temp directory."""
    return AuditLog(run_id=run_id, log_dir=tmp_path)


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------


class TestRecord:
    """Tests for the core record() primitive."""

    def test_writes_one_json_line_with_envelope_fields(self, tmp_path):
        """Each record() call emits a single JSON line with the envelope."""
        log = _make_log(tmp_path)
        log.record("phase_transition", {"from": "expand", "to": "search"})

        lines = log.log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert set(entry) == {"timestamp", "run_id", "event_type", "data"}
        assert entry["run_id"] == "run-001"
        assert entry["event_type"] == "phase_transition"
        assert entry["data"] == {"from": "expand", "to": "search"}
        # Timestamp is ISO-8601 parseable.
        assert datetime.fromisoformat(entry["timestamp"])

    def test_none_data_becomes_empty_dict(self, tmp_path):
        """Omitted data payload is recorded as {}."""
        log = _make_log(tmp_path)
        log.record("heartbeat")
        assert log.dump()[0]["data"] == {}

    def test_appends_across_calls(self, tmp_path):
        """Successive record() calls append lines in order."""
        log = _make_log(tmp_path)
        log.record("first", {"n": 1})
        log.record("second", {"n": 2})
        log.record("third", {"n": 3})

        lines = log.log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert [json.loads(line)["event_type"] for line in lines] == [
            "first",
            "second",
            "third",
        ]

    def test_append_survives_reopening_log_object(self, tmp_path):
        """A second AuditLog with the same run_id appends, not truncates."""
        _make_log(tmp_path).record("first")
        reopened = _make_log(tmp_path)
        reopened.record("second")
        assert [e["event_type"] for e in reopened.dump()] == ["first", "second"]

    def test_default_str_serializes_non_json_values(self, tmp_path):
        """Non-serializable payload values are stringified, not fatal."""
        log = _make_log(tmp_path)
        marker = datetime(2026, 1, 2, 3, 4, 5)
        log.record("weird", {"when": marker, "path": Path("/tmp/x")})

        entry = log.dump()[0]
        assert entry["data"]["when"] == str(marker)
        assert entry["data"]["path"] == str(Path("/tmp/x"))


# ---------------------------------------------------------------------------
# dump()
# ---------------------------------------------------------------------------


class TestDump:
    """Tests for reading entries back."""

    def test_round_trips_recorded_entries(self, tmp_path):
        """dump() returns exactly what record() wrote, in order."""
        log = _make_log(tmp_path)
        payloads = [{"i": i, "tag": f"e{i}"} for i in range(4)]
        for p in payloads:
            log.record("event", p)

        entries = log.dump()
        assert [e["data"] for e in entries] == payloads
        assert all(e["run_id"] == "run-001" for e in entries)

    def test_empty_when_no_writes(self, tmp_path):
        """dump() on a never-written log returns [] (file is lazy)."""
        log = _make_log(tmp_path)
        assert log.dump() == []
        assert not log.log_path.exists()


# ---------------------------------------------------------------------------
# Convenience recorders
# ---------------------------------------------------------------------------


class TestConvenienceRecorders:
    """Tests for the typed record_* helpers."""

    def test_record_llm_call_shape(self, tmp_path):
        """record_llm_call writes an llm_call event with the standard keys."""
        log = _make_log(tmp_path)
        log.record_llm_call(
            agent_name="reader",
            model="gpt-4o-mini",
            input_tokens=120,
            output_tokens=34,
            tool_calls=["submit_summary"],
        )

        entry = log.dump()[0]
        assert entry["event_type"] == "llm_call"
        assert entry["data"] == {
            "agent_name": "reader",
            "model": "gpt-4o-mini",
            "input_tokens": 120,
            "output_tokens": 34,
            "tool_calls": ["submit_summary"],
        }

    def test_record_llm_call_merges_extra_keys_into_data(self, tmp_path):
        """The extra dict's keys land alongside the standard keys."""
        log = _make_log(tmp_path)
        log.record_llm_call(
            agent_name="reader",
            model="codex",
            input_tokens=0,
            output_tokens=0,
            extra={"response_source": "handoff-file", "attempt": 2},
        )

        data = log.dump()[0]["data"]
        assert data["response_source"] == "handoff-file"
        assert data["attempt"] == 2
        # Standard keys are still present.
        assert data["agent_name"] == "reader"
        assert data["tool_calls"] == []

    def test_record_llm_call_defaults(self, tmp_path):
        """Omitted tool_calls and extra produce [] and no extra keys."""
        log = _make_log(tmp_path)
        log.record_llm_call(
            agent_name="a", model="m", input_tokens=1, output_tokens=2
        )
        data = log.dump()[0]["data"]
        assert data["tool_calls"] == []
        assert set(data) == {
            "agent_name", "model", "input_tokens", "output_tokens", "tool_calls"
        }

    def test_record_gate_decision_shape(self, tmp_path):
        """record_gate_decision writes a gate_decision event."""
        log = _make_log(tmp_path)
        log.record_gate_decision(
            gate_name="quality_threshold",
            passed=False,
            reason="No results above score 4.0",
            metrics={"kept": 0, "threshold": 4.0},
        )

        entry = log.dump()[0]
        assert entry["event_type"] == "gate_decision"
        assert entry["data"] == {
            "gate_name": "quality_threshold",
            "passed": False,
            "reason": "No results above score 4.0",
            "metrics": {"kept": 0, "threshold": 4.0},
        }

    def test_record_search_shape(self, tmp_path):
        """record_search writes a search event."""
        log = _make_log(tmp_path)
        log.record_search(
            query="protest campaigns 2019",
            backends=["brave", "openalex"],
            result_count=17,
            elapsed=1.25,
        )

        entry = log.dump()[0]
        assert entry["event_type"] == "search"
        assert entry["data"] == {
            "query": "protest campaigns 2019",
            "backends": ["brave", "openalex"],
            "result_count": 17,
            "elapsed_seconds": 1.25,
        }

    def test_record_error_shape(self, tmp_path):
        """record_error writes an error event; context defaults to {}."""
        log = _make_log(tmp_path)
        log.record_error("TimeoutError", "search timed out")

        entry = log.dump()[0]
        assert entry["event_type"] == "error"
        assert entry["data"] == {
            "error_type": "TimeoutError",
            "message": "search timed out",
            "context": {},
        }


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


class TestFileLayout:
    """Tests for log file naming and directory creation."""

    def test_log_file_named_by_run_id(self, tmp_path):
        """The log file is {run_id}.jsonl inside log_dir."""
        log = AuditLog(run_id="run-xyz", log_dir=tmp_path)
        log.record("ping")
        assert log.log_path == tmp_path / "run-xyz.jsonl"
        assert log.log_path.exists()
        assert log.run_id == "run-xyz"

    def test_creates_missing_log_dir(self, tmp_path):
        """A nested, nonexistent log_dir is created eagerly."""
        nested = tmp_path / "a" / "b"
        assert not nested.exists()
        AuditLog(run_id="r", log_dir=nested)
        assert nested.is_dir()
