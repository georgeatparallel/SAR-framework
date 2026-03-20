"""Tests for agent_service helper functions.

These tests verify parsing and orchestration logic without calling Gemini.
"""

import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")
os.environ.setdefault("SEARCH_ENGINE_ID", "test-engine-id")

from app.services.agent_service import (
    _extract_submit_report,
    tool_parser_agent,
)


class TestExtractSubmitReport:
    def test_raises_on_empty_response(self):
        mock_resp = MagicMock()
        mock_resp.candidates = []
        with pytest.raises((ValueError, IndexError)):
            _extract_submit_report(mock_resp)

    def test_raises_on_wrong_function_name(self):
        mock_resp = MagicMock()
        mock_fc = MagicMock()
        mock_fc.name = "wrong_function"
        mock_resp.candidates = [MagicMock()]
        mock_resp.candidates[0].content.parts = [MagicMock()]
        mock_resp.candidates[0].content.parts[0].function_call = mock_fc
        with pytest.raises(ValueError, match="Expected 'submit_report'"):
            _extract_submit_report(mock_resp)

    def test_extracts_report_and_citations(self):
        mock_resp = MagicMock()
        mock_fc = MagicMock()
        mock_fc.name = "submit_report"
        mock_fc.args = {
            "report": "# Test Report\n\nSome content.",
            "citations_used": [
                {"url": "https://example.com", "title": "Example"},
            ],
        }
        mock_resp.candidates = [MagicMock()]
        mock_resp.candidates[0].content.parts = [MagicMock()]
        mock_resp.candidates[0].content.parts[0].function_call = mock_fc

        report, citations = _extract_submit_report(mock_resp)
        assert report == "# Test Report\n\nSome content."
        assert len(citations) == 1
        assert citations[0]["url"] == "https://example.com"


class TestToolParserAgent:
    def test_parses_mark_as_complete(self):
        mock_resp = MagicMock()
        mock_fc = MagicMock()
        mock_fc.name = "mark_as_complete"
        mock_fc.args = {}
        mock_resp.candidates = [MagicMock()]
        mock_resp.candidates[0].content.parts = [MagicMock()]
        mock_resp.candidates[0].content.parts[0].function_call = mock_fc

        action, detail = tool_parser_agent(mock_resp)
        assert action == "COMPLETE"

    def test_parses_request_rework(self):
        mock_resp = MagicMock()
        mock_fc = MagicMock()
        mock_fc.name = "request_rework"
        mock_fc.args = {"feedback": "Need more detail on methodology."}
        mock_resp.candidates = [MagicMock()]
        mock_resp.candidates[0].content.parts = [MagicMock()]
        mock_resp.candidates[0].content.parts[0].function_call = mock_fc

        action, detail = tool_parser_agent(mock_resp)
        assert action == "NEEDS_REWORK"
        assert detail == "Need more detail on methodology."

    def test_parses_request_more_info(self):
        mock_resp = MagicMock()
        mock_fc = MagicMock()
        mock_fc.name = "request_more_info"
        mock_fc.args = {"queries": ["query 1", "query 2"]}
        mock_resp.candidates = [MagicMock()]
        mock_resp.candidates[0].content.parts = [MagicMock()]
        mock_resp.candidates[0].content.parts[0].function_call = mock_fc

        action, detail = tool_parser_agent(mock_resp)
        assert action == "NEEDS_MORE_INFO"
        assert detail == ["query 1", "query 2"]
