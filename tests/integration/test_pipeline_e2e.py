"""End-to-end pipeline tests with a scripted LLM and mock search backend.

Drives ``run_pipeline`` through the full EXPAND -> SEARCH -> JUDGE ->
READ -> ANALYZE -> REVIEW state machine with zero network access.

The scripted LLM dispatches on the tool schema each agent attaches to
its request (submit_queries -> expander, submit_scores -> judge,
submit_extraction -> reader, submit_report -> analyzer,
mark_as_complete/request_* -> reviewer), so responses always land at
the right agent even when reader calls run concurrently.

Note on tool-call shapes: contracts.llm.ToolCall exposes ``.name``, but
query_expander / source_judge / reader look up ``tc.tool_name`` while
analyzer / reviewer and the metering layer use ``tc.name``. The fake
tool call below carries BOTH attributes so every agent's tool-call
branch is exercised.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sar.contracts.events import EventType
from sar.contracts.llm import LLMRequest
from sar.contracts.pipeline import PipelineState, ReviewerVerdict
from sar.contracts.search import SearchResult, SourceType
from sar.events import EventBus
from sar.orchestrator import runner as runner_module
from sar.orchestrator.runner import run_pipeline
from sar.persistence.audit import AuditLog
from sar.search.engine import SearchEngine
from tests.conftest import MockSearchBackend

QUERY = "impact of social media on protest mobilization"

REPORT_TEXT = (
    "## Executive Summary\nSocial media accelerates mobilization [Source 1].\n"
    "## Key Findings\n- Coordination costs drop [Source 2].\n"
    "## Methodology\nPanel studies [Source 1].\n"
    "## Evidence\nConvergent findings [Source 3].\n"
    "## Gaps & Limitations\nFew causal designs.\n"
    "## Conclusion\nModerate positive effect [Source 1]."
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeToolCall:
    """Duck-typed tool call exposing both ``name`` and ``tool_name``."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.id = f"call-{name}"
        self.name = name
        self.tool_name = name  # query_expander/source_judge/reader read this
        self.arguments = arguments


class FakeLLMResponse:
    """Duck-typed LLMResponse with usage so the budget meter sees tokens."""

    def __init__(
        self,
        tool_calls: list[FakeToolCall] | None = None,
        content: str | None = None,
        model: str = "gpt-4o-mini",
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.model = model
        self.usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        self.raw = None


def tool_response(name: str, **arguments: Any) -> FakeLLMResponse:
    """Build a FakeLLMResponse containing a single tool call."""
    return FakeLLMResponse(tool_calls=[FakeToolCall(name, dict(arguments))])


def expander_response(queries: list[str]) -> FakeLLMResponse:
    return tool_response("submit_queries", queries=queries)


def judge_response(urls: list[str], score: float = 8.0) -> FakeLLMResponse:
    return tool_response(
        "submit_scores",
        scores=[
            {"url": u, "score": score, "reason": "relevant and cited"}
            for u in urls
        ],
    )


def reader_response() -> FakeLLMResponse:
    return tool_response(
        "submit_extraction",
        key_claims=["Social media lowers coordination costs"],
        methodology="Panel study of protest events",
        relevance_score=8,
        key_quotes=["'mobilization accelerated markedly'"],
    )


def analyzer_response(report: str = REPORT_TEXT) -> FakeLLMResponse:
    return tool_response(
        "submit_report",
        report=report,
        citations_used=["https://example.org/paper-1"],
    )


def reviewer_complete() -> FakeLLMResponse:
    return tool_response("mark_as_complete")


def reviewer_rework(feedback: str) -> FakeLLMResponse:
    return tool_response("request_rework", feedback=feedback)


def reviewer_more_info(queries: list[str]) -> FakeLLMResponse:
    return tool_response("request_more_info", queries=queries)


def _request_tool_names(request: LLMRequest) -> set[str]:
    """Extract the function names of every tool attached to a request."""
    names: set[str] = set()
    for tool in request.tools or []:
        if isinstance(tool, dict):
            name = tool.get("function", {}).get("name") or tool.get("name")
        else:  # ToolDefinition
            name = tool.name
        if name:
            names.add(name)
    return names


class ScriptedLLM:
    """Returns scripted responses per agent, keyed by the request's tools.

    Each agent slot holds a list of responses consumed in order; the
    last response is reused once the list is down to one entry (so e.g.
    a single reader response serves every concurrent reader call).
    Requests are recorded per agent for assertions.
    """

    _DISPATCH = {
        "submit_queries": "expander",
        "submit_scores": "judge",
        "submit_extraction": "reader",
        "submit_report": "analyzer",
        "submit_campaign_dossier": "synthesizer",
        "mark_as_complete": "reviewer",
    }

    def __init__(self, **scripts: list[FakeLLMResponse]) -> None:
        self._scripts: dict[str, list[FakeLLMResponse]] = dict(scripts)
        self.requests: dict[str, list[LLMRequest]] = defaultdict(list)

    def _agent_for(self, request: LLMRequest) -> str:
        names = _request_tool_names(request)
        for tool_name, agent in self._DISPATCH.items():
            if tool_name in names:
                return agent
        raise AssertionError(f"Unrecognized tool set on request: {names}")

    async def call(self, request: LLMRequest) -> FakeLLMResponse:
        agent = self._agent_for(request)
        self.requests[agent].append(request)
        script = self._scripts.get(agent)
        if not script:
            raise AssertionError(f"No scripted response for agent {agent!r}")
        return script.pop(0) if len(script) > 1 else script[0]


class StubConfig:
    """Minimal config stand-in: plain attributes, no env required."""

    MAX_ITERATIONS = 3
    MAX_REWORKS_PER_ITERATION = 2
    MIN_RESULTS_TO_PROCEED = 1
    QUALITY_THRESHOLD = 4.0
    MAX_READ_PER_ITERATION = 10
    BUDGET_CEILING_USD = 5.0


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def make_results() -> list[SearchResult]:
    """Three distinct search results with URLs the judge script scores."""
    return [
        SearchResult(
            title=f"Paper {i}",
            url=f"https://example.org/paper-{i}",
            snippet=f"Snippet about protest mobilization {i}",
            source_type=SourceType.ACADEMIC,
            authors=[f"Author {i}"],
            year=2019 + i,
            citation_count=10 * i,
        )
        for i in (1, 2, 3)
    ]


def make_search_engine(results: list[SearchResult]) -> SearchEngine:
    return SearchEngine(backends=[MockSearchBackend(name="mock", results=results)])


def default_scripts(
    **overrides: list[FakeLLMResponse],
) -> dict[str, list[FakeLLMResponse]]:
    results = make_results()
    scripts: dict[str, list[FakeLLMResponse]] = {
        "expander": [
            expander_response(
                ["protest social media effects", "digital activism review"]
            )
        ],
        "judge": [judge_response([r.url for r in results])],
        "reader": [reader_response()],
        "analyzer": [analyzer_response()],
        "reviewer": [reviewer_complete()],
    }
    scripts.update(overrides)
    return scripts


def capture_state(monkeypatch) -> dict[str, PipelineState]:
    """Monkeypatch the runner's PipelineState to capture the instance."""
    captured: dict[str, PipelineState] = {}

    def _factory(*args: Any, **kwargs: Any) -> PipelineState:
        state = PipelineState(*args, **kwargs)
        captured["state"] = state
        return state

    monkeypatch.setattr(runner_module, "PipelineState", _factory)
    return captured


async def run_with(
    scripts: dict[str, list[FakeLLMResponse]],
    audit_log: AuditLog,
    config: StubConfig | None = None,
    event_bus: EventBus | None = None,
) -> tuple[Any, ScriptedLLM]:
    llm = ScriptedLLM(**scripts)
    engine = make_search_engine(make_results())
    result = await run_pipeline(
        query=QUERY,
        config=config or StubConfig(),
        search_engine=engine,
        llm=llm,
        audit_log=audit_log,
        event_bus=event_bus,
    )
    return result, llm


def audit_event_types(audit_log: AuditLog) -> list[str]:
    return [entry["event_type"] for entry in audit_log.dump()]


# ---------------------------------------------------------------------------
# (a) Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    """Full EXPAND -> ... -> REVIEW -> COMPLETE run."""

    async def test_pipeline_completes_with_report(self, tmp_audit_log):
        result, llm = await run_with(default_scripts(), tmp_audit_log)

        assert result.success is True
        # The analyzer's prose is preserved verbatim; an APA reference list
        # is appended after REVIEW, so this is no longer an exact match.
        assert result.report.startswith(REPORT_TEXT)
        assert "## References" in result.report
        assert result.iterations_used == 1
        # One call each for expander/judge/analyzer/reviewer; one reader
        # call per judged result.
        assert len(llm.requests["expander"]) == 1
        assert len(llm.requests["judge"]) == 1
        assert len(llm.requests["reader"]) == 3
        assert len(llm.requests["analyzer"]) == 1
        assert len(llm.requests["reviewer"]) == 1

    async def test_result_fields_populated(self, tmp_audit_log):
        result, _ = await run_with(default_scripts(), tmp_audit_log)

        # Summaries: one per search result, linked back to the source URL.
        assert len(result.summaries) == 3
        urls = {s.source.url for s in result.summaries}
        assert urls == {r.url for r in make_results()}
        assert all(s.quality_score == 8.0 for s in result.summaries)
        assert all(s.extraction["key_claims"] for s in result.summaries)

        # Gate log: the five linear gates all passed.
        assert result.gate_log
        gate_names = [g.gate_name for g in result.gate_log]
        for expected in (
            "queries_generated",
            "results_obtained",
            "quality_threshold",
            "summaries_ready",
            "report_submitted",
        ):
            assert expected in gate_names
        assert all(g.passed for g in result.gate_log)

        # Budget: the fake reports usage, so the meter accumulated it.
        assert result.budget.total_tokens > 0
        assert result.budget.prompt_tokens > 0
        assert result.budget.cost_usd > 0

    async def test_event_bus_receives_agent_events(self, tmp_audit_log):
        bus = EventBus()
        seen: list[Any] = []
        bus.subscribe(None, seen.append)

        result, _ = await run_with(default_scripts(), tmp_audit_log, event_bus=bus)

        assert result.success is True
        types = {e.event_type for e in seen}
        assert EventType.AGENT_UPDATE in types
        assert EventType.AGENT_COMPLETE in types
        agents = {e.data.get("agent") for e in seen}
        assert {"Query Expander", "Reviewer"} <= agents


# ---------------------------------------------------------------------------
# (b) NEEDS_REWORK loop
# ---------------------------------------------------------------------------


class TestNeedsReworkLoop:
    """Reviewer rejects once, analyzer reruns with feedback."""

    async def test_rework_then_complete(self, tmp_audit_log, monkeypatch):
        captured = capture_state(monkeypatch)
        feedback = "Cite Source 2 in the conclusion."
        scripts = default_scripts(
            reviewer=[reviewer_rework(feedback), reviewer_complete()],
        )

        result, llm = await run_with(scripts, tmp_audit_log)

        assert result.success is True
        # Analyzer called twice: initial + rework; expander only once.
        assert len(llm.requests["analyzer"]) == 2
        assert len(llm.requests["expander"]) == 1
        assert result.iterations_used == 1

        # state.feedback flowed into the second analyzer prompt.
        second_prompt = llm.requests["analyzer"][1].messages[1]["content"]
        assert feedback in second_prompt
        assert "REVISION NOTES" in second_prompt
        first_prompt = llm.requests["analyzer"][0].messages[1]["content"]
        assert feedback not in first_prompt

        # IterationRecord captured the verdict and feedback.
        state = captured["state"]
        assert len(state.iterations) == 2
        assert state.iterations[0].reviewer_verdict == ReviewerVerdict.NEEDS_REWORK
        assert state.iterations[0].feedback == feedback
        assert state.iterations[1].reviewer_verdict == ReviewerVerdict.APPROVED

    async def test_rework_verdicts_in_audit_log(self, tmp_audit_log):
        scripts = default_scripts(
            reviewer=[reviewer_rework("fix it"), reviewer_complete()],
        )
        await run_with(scripts, tmp_audit_log)

        verdicts = [
            e["data"]["verdict"]
            for e in tmp_audit_log.dump()
            if e["event_type"] == "review_decision"
        ]
        assert verdicts == ["needs_rework", "approved"]


# ---------------------------------------------------------------------------
# (c) NEEDS_REWORK forever: bounded by MAX_REWORKS_PER_ITERATION
# ---------------------------------------------------------------------------


class TestReworkBound:
    """An always-rework reviewer cannot hang the pipeline."""

    async def test_rework_bound_forces_completion(self, tmp_audit_log):
        # Single-entry script: ScriptedLLM reuses the last response, so
        # the reviewer requests rework forever.
        scripts = default_scripts(reviewer=[reviewer_rework("never good enough")])
        config = StubConfig()
        config.MAX_ITERATIONS = 3
        config.MAX_REWORKS_PER_ITERATION = 2

        result, llm = await run_with(scripts, tmp_audit_log, config=config)

        # Terminates: reworks 1 and 2 loop back to ANALYZE; the third
        # NEEDS_REWORK exceeds the bound and is force-accepted.
        assert result.success is True
        assert len(llm.requests["reviewer"]) == 3
        assert len(llm.requests["analyzer"]) == 3
        assert len(llm.requests["expander"]) == 1

        events = audit_event_types(tmp_audit_log)
        assert "rework_bound_exhausted" in events

    async def test_rework_bound_records_metrics(self, tmp_audit_log):
        scripts = default_scripts(reviewer=[reviewer_rework("still no")])
        await run_with(scripts, tmp_audit_log)

        exhausted = [
            e for e in tmp_audit_log.dump()
            if e["event_type"] == "rework_bound_exhausted"
        ]
        assert len(exhausted) == 1
        assert exhausted[0]["data"]["reworks"] == 3
        assert exhausted[0]["data"]["max_reworks"] == 2


# ---------------------------------------------------------------------------
# (d) NEEDS_MORE_INFO loops back through EXPAND
# ---------------------------------------------------------------------------


class TestNeedsMoreInfoLoop:
    """Reviewer requests more info once; pipeline re-expands and re-searches."""

    async def test_more_info_reruns_expand(self, tmp_audit_log, monkeypatch):
        captured = capture_state(monkeypatch)
        first_variants = ["first round query"]
        second_variants = ["second round query", "fresh grey literature angle"]
        scripts = default_scripts(
            expander=[
                expander_response(first_variants),
                expander_response(second_variants),
            ],
            reviewer=[
                reviewer_more_info(["reviewer suggested query"]),
                reviewer_complete(),
            ],
        )

        result, llm = await run_with(scripts, tmp_audit_log)

        assert result.success is True
        # Two full search rounds, but only ONE expansion: the second round
        # searches the reviewer's own queries instead of re-expanding the
        # original question (re-expansion used to clobber them).
        assert len(llm.requests["expander"]) == 1
        assert len(llm.requests["judge"]) == 2
        assert result.iterations_used == 2

        state = captured["state"]
        assert state.iterations[0].reviewer_verdict == ReviewerVerdict.NEEDS_MORE_INFO
        assert state.iterations[1].reviewer_verdict == ReviewerVerdict.APPROVED

        # The reviewer's queries are exactly what round two searched.
        assert state.queries == ["reviewer suggested query"]
        assert first_variants[0] not in state.queries


# ---------------------------------------------------------------------------
# (e) MAX_ITERATIONS exhaustion forces final-round accept
# ---------------------------------------------------------------------------


class TestMaxIterations:
    """On the final round any non-approve verdict is force-accepted."""

    async def test_final_round_forces_accept(self, tmp_audit_log):
        config = StubConfig()
        config.MAX_ITERATIONS = 1
        # Reviewer wants more info forever -- but iteration 1 is already
        # the final round, so the report is accepted instead of looping.
        scripts = default_scripts(reviewer=[reviewer_more_info(["another query"])])

        result, llm = await run_with(scripts, tmp_audit_log, config=config)

        assert result.success is True
        # The analyzer's prose is preserved verbatim; an APA reference list
        # is appended after REVIEW, so this is no longer an exact match.
        assert result.report.startswith(REPORT_TEXT)
        assert "## References" in result.report
        assert result.iterations_used == 1
        assert len(llm.requests["expander"]) == 1
        assert len(llm.requests["reviewer"]) == 1

        # The reviewer was invoked in final-round mode: request_more_info
        # is stripped from its tool set.
        reviewer_tools = _request_tool_names(llm.requests["reviewer"][0])
        assert reviewer_tools == {"mark_as_complete", "request_rework"}

        decisions = [
            e["data"] for e in tmp_audit_log.dump()
            if e["event_type"] == "review_decision"
        ]
        assert decisions[0]["is_final"] is True
        assert decisions[0]["verdict"] == "needs_more_info"


# ---------------------------------------------------------------------------
# (f) Audit log contents
# ---------------------------------------------------------------------------


class TestAuditTrail:
    """The audit JSONL records the run's key events."""

    async def test_audit_log_records_key_events(self, tmp_audit_log):
        result, _ = await run_with(default_scripts(), tmp_audit_log)
        assert result.success is True

        entries = tmp_audit_log.dump()
        types = [e["event_type"] for e in entries]

        assert types[0] == "pipeline_start"
        assert entries[0]["data"]["query"] == QUERY
        assert types[-1] == "pipeline_end"
        assert entries[-1]["data"]["success"] is True

        # Metering layer: one llm_call per scripted call (expander, judge,
        # 3 readers, analyzer, reviewer).
        llm_calls = [e for e in entries if e["event_type"] == "llm_call"]
        assert len(llm_calls) == 7
        # agent_name is the agent that made the call. It used to be the
        # client class for every call, so cost could not be attributed.
        assert [c["data"]["agent_name"] for c in llm_calls] == [
            "query_expander",
            "source_judge",
            "reader",
            "reader",
            "reader",
            "analyzer",
            "reviewer",
        ]
        # extra is spread into the event payload, not nested under "extra".
        assert all(c["data"]["phase"] for c in llm_calls)
        assert all(c["data"]["backend"] == "ScriptedLLM" for c in llm_calls)
        assert all(c["data"]["input_tokens"] == 100 for c in llm_calls)
        called_tools = {t for c in llm_calls for t in c["data"]["tool_calls"]}
        assert "submit_queries" in called_tools
        assert "submit_report" in called_tools

        # Phase completions and the review decision.
        phases = [
            e["data"]["phase"] for e in entries
            if e["event_type"] == "phase_complete"
        ]
        assert phases == ["expand", "search", "judge", "read", "analyze"]
        assert "review_decision" in types

    async def test_gate_decisions_recorded(self, tmp_audit_log):
        # Every per-transition gate decision reaches BOTH the in-memory
        # gate_log and the audit file. The audit file previously received a
        # gate_decision event only when the cross-cutting budget gate failed,
        # so a run's JSONL could not explain why it ended -- contradicting
        # BLUEPRINT.md and the README, which both promise it can.
        result, _ = await run_with(default_scripts(), tmp_audit_log)

        assert len(result.gate_log) == 5
        audit_gates = [
            e for e in tmp_audit_log.dump() if e["event_type"] == "gate_decision"
        ]
        assert len(audit_gates) == len(result.gate_log)
        recorded = [e["data"]["gate_name"] for e in audit_gates]
        assert recorded == [d.gate_name for d in result.gate_log]
        for entry in audit_gates:
            assert "passed" in entry["data"]
            assert "reason" in entry["data"]
            assert "from_phase" in entry["data"]
            assert "to_phase" in entry["data"]

    async def test_budget_exceeded_gate_reaches_audit(self, tmp_audit_log):
        # Force the ceiling to zero: the pre-phase budget gate fails
        # immediately and IS written to the audit log.
        config = StubConfig()
        config.BUDGET_CEILING_USD = 0.0

        result, llm = await run_with(default_scripts(), tmp_audit_log, config=config)

        assert result.success is False
        assert llm.requests == {}  # aborted before any agent ran
        audit_gates = [
            e for e in tmp_audit_log.dump() if e["event_type"] == "gate_decision"
        ]
        assert len(audit_gates) == 1
        assert audit_gates[0]["data"]["gate_name"] == "budget_remaining"
        assert audit_gates[0]["data"]["passed"] is False


class TestReviewFailsClosed:
    """A review that could not produce a verdict must not pass as approval.

    The runner previously caught any reviewer exception and set the verdict to
    "complete", so a reviewer that never ran returned an unreviewed report as a
    successful run -- the exact outcome the reviewer exists to prevent.
    """

    async def test_reviewer_without_tool_call_fails_the_run(self, tmp_audit_log):
        scripts = default_scripts(reviewer=[FakeLLMResponse(content="Looks fine.")])

        result, _ = await run_with(scripts, tmp_audit_log)

        assert result.success is False
        errors = [e for e in tmp_audit_log.dump() if e["event_type"] == "error"]
        assert any(e["data"]["error_type"] == "reviewer" for e in errors)

    async def test_report_is_still_returned_for_inspection(self, tmp_audit_log):
        """Failing the run must not destroy the artifact it produced."""
        scripts = default_scripts(reviewer=[FakeLLMResponse(content="Looks fine.")])

        result, _ = await run_with(scripts, tmp_audit_log)

        assert result.success is False
        assert result.report  # the unreviewed report is available, just not blessed


class TestBibliography:
    """The report ends with a reference list built from its own sources.

    `citations.py` was implemented and imported by nothing, so reports carried
    inline [Source N] markers with no bibliography behind them.
    """

    async def test_report_gains_a_reference_section(self, tmp_audit_log):
        result, _ = await run_with(default_scripts(), tmp_audit_log)

        assert result.success is True
        assert "## References" in result.report
        # The analyzer's own prose is untouched and comes first.
        assert result.report.index(REPORT_TEXT[:40]) < result.report.index("## References")

    async def test_citations_are_returned_structured(self, tmp_audit_log):
        result, _ = await run_with(default_scripts(), tmp_audit_log)

        assert result.citations, "structured citations must accompany the report"
        assert len(result.citations) == len(result.summaries)
        for citation in result.citations:
            assert citation["url"]
            assert citation["title"]

    async def test_reviewer_never_sees_the_reference_list(self, tmp_audit_log):
        """References are appended after REVIEW, so the reviewer judges the
        analyzer's prose rather than a list it did not write."""
        _, llm = await run_with(default_scripts(), tmp_audit_log)

        reviewed = str(llm.requests.get("reviewer", ""))
        assert "## References" not in reviewed
