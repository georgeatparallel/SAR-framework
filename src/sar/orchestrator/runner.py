"""Pipeline runner: drives the state machine through a complete execution.

The runner is the top-level entry point that wires together the
search engine, LLM client, agents, and persistence to execute a
full research pipeline.

It creates a PipelineMachine, iterates through phases, calls the
appropriate agent for each phase, and handles the reviewer loop
(REVIEW -> EXPAND | ANALYZE | COMPLETE).

Migrated from: agentic_sar/agent_service.py run_agentic_pipeline()
"""

from __future__ import annotations

import logging
import uuid
from functools import partial
from typing import Any

from sar.agents import analyzer, query_expander, reader, reviewer, source_judge
from sar.config import AppConfig
from sar.contracts.pipeline import (
    GateDecision,
    IterationRecord,
    PaperSummary,
    PipelinePhase,
    PipelineResult,
    PipelineState,
    ReviewerVerdict,
)
from sar.events import EventBus
from sar.llm.client import LLMClient
from sar.orchestrator import gates
from sar.orchestrator.machine import PipelineMachine, TransitionEntry
from sar.persistence.audit import AuditLog
from sar.search.engine import SearchEngine

logger = logging.getLogger(__name__)

# Default configuration values used when AppConfig fields are absent.
_DEFAULT_MAX_ITERATIONS = 3
_DEFAULT_MIN_RESULTS = 1
_DEFAULT_QUALITY_THRESHOLD = 4.0
_DEFAULT_MAX_READ = 10
_DEFAULT_MAX_REWORKS = 2

# Command strings _parse_verdict recognizes; anything else maps to APPROVED
# and is flagged in the audit log as review_verdict_unrecognized.
_KNOWN_VERDICT_COMMANDS = {
    None,
    "mark_as_complete", "complete", "approved",
    "request_rework", "needs_rework",
    "request_more_info", "needs_more_info",
    "failed",
}


def _get_config_attr(config: AppConfig, name: str, default: Any) -> Any:
    """Safely retrieve a config attribute with a fallback default.

    Args:
        config: Application configuration object.
        name: Attribute name.
        default: Value to return if attribute is missing.

    Returns:
        The attribute value or the default.
    """
    return getattr(config, name, default)


def build_transitions(config: AppConfig) -> dict[PipelinePhase, list[TransitionEntry]]:
    """Build the transition table with configured thresholds.

    The transition table defines the allowed phase transitions and
    their associated gate functions. Each phase maps to an ordered
    list of (target_phase, gate_function) candidates.

    The REVIEW phase is NOT included here because it is handled
    specially by the runner (the reviewer verdict determines the
    next phase).

    Args:
        config: Application configuration with pipeline tuning params.

    Returns:
        Mapping from source phase to candidate transitions.
    """
    min_results = _get_config_attr(config, "MIN_RESULTS_TO_PROCEED", _DEFAULT_MIN_RESULTS)
    quality_threshold = _get_config_attr(config, "QUALITY_THRESHOLD", _DEFAULT_QUALITY_THRESHOLD)

    return {
        PipelinePhase.EXPAND: [
            (PipelinePhase.SEARCH, gates.check_queries_generated),
        ],
        PipelinePhase.SEARCH: [
            (
                PipelinePhase.JUDGE,
                partial(gates.check_results_obtained, min_results=min_results),
            ),
        ],
        PipelinePhase.JUDGE: [
            (
                PipelinePhase.READ,
                partial(
                    gates.check_quality_threshold,
                    min_score=quality_threshold,
                ),
            ),
        ],
        PipelinePhase.READ: [
            (PipelinePhase.ANALYZE, gates.check_summaries_ready),
        ],
        PipelinePhase.ANALYZE: [
            (PipelinePhase.REVIEW, gates.check_report_submitted),
        ],
        # REVIEW is handled by the runner, not the transition table.
    }


async def run_pipeline(
    query: str,
    config: AppConfig,
    search_engine: SearchEngine,
    llm: LLMClient,
    audit_log: AuditLog,
    event_bus: EventBus | None = None,
) -> PipelineResult:
    """Execute a full research pipeline from query to final report.

    Orchestrates the state machine through EXPAND -> SEARCH -> JUDGE ->
    READ -> ANALYZE -> REVIEW cycles until the reviewer approves or
    max iterations / budget is exhausted.

    Args:
        query: The user's research question.
        config: Application configuration.
        search_engine: Configured search engine with backends.
        llm: LLM client for all agent calls.
        audit_log: Audit logger for this run.
        event_bus: Optional event bus for streaming updates.

    Returns:
        PipelineResult with report, citations, iteration history,
        gate log, and budget summary.
    """
    max_iterations = _get_config_attr(config, "MAX_ITERATIONS", _DEFAULT_MAX_ITERATIONS)
    max_read = _get_config_attr(config, "MAX_READ_PER_ITERATION", _DEFAULT_MAX_READ)

    run_id = str(uuid.uuid4())
    state = PipelineState()
    transitions = build_transitions(config)
    machine = PipelineMachine(state, transitions)

    # Meter every LLM call into state.budget so check_budget_remaining is a
    # live gate rather than a check of a never-mutated default.
    from sar.llm.budget import BudgetTracker
    from sar.llm.metering import MeteredClient

    tracker = BudgetTracker(
        budget=state.budget,
        ceiling_usd=_get_config_attr(config, "BUDGET_CEILING_USD", None),
    )
    llm = MeteredClient(llm, tracker, audit_log)

    audit_log.record("pipeline_start", {"run_id": run_id, "query": query})

    reworks_this_iteration = 0
    reviewer_queries_pending = False

    while not machine.is_terminal:
        phase = machine.current_phase

        # Cross-cutting: check budget before every phase
        budget_gate = gates.check_budget_remaining(state)
        if not budget_gate.passed:
            state.gate_log.append(budget_gate)
            audit_log.record_gate_decision(
                budget_gate.gate_name,
                budget_gate.passed,
                budget_gate.reason,
                budget_gate.metrics,
            )
            logger.warning("Budget exceeded -- aborting pipeline")
            machine.force_phase(PipelinePhase.FAILED)
            break

        # -----------------------------------------------------------
        # EXPAND phase: generate search queries
        # -----------------------------------------------------------
        if phase == PipelinePhase.EXPAND:
            if reviewer_queries_pending and state.queries:
                # A NEEDS_MORE_INFO round supplied targeted queries. Searching
                # them is the point of the round; re-expanding the original
                # question here used to overwrite and discard them.
                reviewer_queries_pending = False
                logger.info(
                    "EXPAND: keeping %d reviewer-supplied queries",
                    len(state.queries),
                )
            else:
                try:
                    state.queries = await query_expander.run(
                        query=query,
                        llm=llm,
                        event_bus=event_bus,
                    )
                    logger.info("EXPAND: %d queries generated", len(state.queries))
                except Exception as exc:
                    logger.error("EXPAND: query expansion failed -- %s", exc)
                    audit_log.record_error(
                        "query_expander", str(exc), {"phase": "expand"}
                    )
                    state.queries = [query]  # Fallback to original query

            state.feedback = None
            state.iteration += 1
            audit_log.record("phase_complete", {
                "phase": "expand",
                "iteration": state.iteration,
                "query_count": len(state.queries),
            })
            machine.transition()

        # -----------------------------------------------------------
        # SEARCH phase: search all expanded queries
        # -----------------------------------------------------------
        elif phase == PipelinePhase.SEARCH:
            try:
                from sar.contracts.search import SearchRequest

                all_results: list[Any] = []
                for q in state.queries:
                    req = SearchRequest(query=q)
                    resp = await search_engine.search(req)
                    if resp is not None:
                        results_list = getattr(resp, "results", [])
                        if results_list:
                            all_results.extend(results_list)

                # Deduplicate if the engine provides the method
                dedup = getattr(search_engine, "_deduplicate", None)
                if dedup is not None and all_results:
                    all_results = dedup(all_results)

                state.results = all_results
                logger.info("SEARCH: %d results obtained", len(state.results))
            except Exception as exc:
                logger.error("SEARCH: search failed -- %s", exc)
                audit_log.record_error("search", str(exc), {"phase": "search"})
                state.results = []

            audit_log.record("phase_complete", {
                "phase": "search",
                "result_count": len(state.results),
            })
            machine.transition()

        # -----------------------------------------------------------
        # JUDGE phase: filter results by quality
        # -----------------------------------------------------------
        elif phase == PipelinePhase.JUDGE:
            try:
                state.results = await source_judge.run(
                    results=state.results,
                    query=query,
                    llm=llm,
                    event_bus=event_bus,
                )
                logger.info("JUDGE: %d results after filtering", len(state.results))
            except Exception as exc:
                logger.error("JUDGE: source judge failed -- %s", exc)
                audit_log.record_error("source_judge", str(exc), {"phase": "judge"})
                # Keep unfiltered results on failure

            audit_log.record("phase_complete", {
                "phase": "judge",
                "result_count": len(state.results),
            })
            machine.transition()

        # -----------------------------------------------------------
        # READ phase: fetch content and extract summaries
        # -----------------------------------------------------------
        elif phase == PipelinePhase.READ:
            try:
                top_results = state.results[:max_read]
                extractions = await reader.run_batch(
                    results=top_results,
                    query=query,
                    llm=llm,
                    event_bus=event_bus,
                )

                state.summaries = []
                if extractions:
                    from sar.contracts.search import SearchResult, SourceType

                    for ext in extractions:
                        if ext is None:
                            continue
                        url = ext.get("url") or ext.get("link") or ""
                        # Try to recover the original SearchResult (richer metadata) by URL.
                        original = next(
                            (r for r in top_results if getattr(r, "url", None) == url),
                            None,
                        )
                        if original is None:
                            original = SearchResult(
                                title=ext.get("title", "") or "(no title)",
                                url=url,
                                snippet="",
                                source_type=SourceType.UNKNOWN,
                                authors=ext.get("authors", []) or [],
                                year=ext.get("year"),
                                citation_count=ext.get("citation_count"),
                            )
                        try:
                            score = float(ext.get("relevance_score",
                                                  ext.get("quality_score", 0.0)))
                        except (TypeError, ValueError):
                            score = 0.0
                        extraction = {
                            "key_claims": ext.get("key_claims", []),
                            "methodology": ext.get("methodology", ""),
                            "key_quotes": ext.get("key_quotes", []),
                        }
                        state.summaries.append(
                            PaperSummary(
                                source=original,
                                extraction=extraction,
                                quality_score=score,
                                notes="",
                            )
                        )

                logger.info("READ: %d summaries produced", len(state.summaries))
            except Exception as exc:
                logger.error("READ: reader failed -- %s", exc)
                audit_log.record_error("reader", str(exc), {"phase": "read"})
                state.summaries = []

            audit_log.record("phase_complete", {
                "phase": "read",
                "summary_count": len(state.summaries),
            })
            machine.transition()

        # -----------------------------------------------------------
        # ANALYZE phase: synthesize report
        # -----------------------------------------------------------
        elif phase == PipelinePhase.ANALYZE:
            try:
                result = await analyzer.run(
                    query=query,
                    summaries=state.summaries,
                    llm=llm,
                    feedback=state.feedback,
                    event_bus=event_bus,
                )
                if result is not None:
                    state.report = result.get("report", "")
                else:
                    state.report = ""
                logger.info("ANALYZE: report produced (%d chars)", len(state.report or ""))
            except Exception as exc:
                logger.error("ANALYZE: analyzer failed -- %s", exc)
                audit_log.record_error("analyzer", str(exc), {"phase": "analyze"})
                state.report = ""

            audit_log.record("phase_complete", {
                "phase": "analyze",
                "report_length": len(state.report or ""),
            })
            machine.transition()

        # -----------------------------------------------------------
        # REVIEW phase: evaluate report and decide next action
        # -----------------------------------------------------------
        elif phase == PipelinePhase.REVIEW:
            iter_gate = gates.check_max_iterations(state, max_iterations=max_iterations)
            is_final = not iter_gate.passed

            try:
                verdict_cmd, payload = await reviewer.run(
                    query=query,
                    report=state.report or "",
                    llm=llm,
                    is_final_round=is_final,
                    event_bus=event_bus,
                )
            except Exception as exc:
                logger.error("REVIEW: reviewer failed -- %s", exc)
                audit_log.record_error("reviewer", str(exc), {"phase": "review"})
                # On reviewer failure, accept the current report
                verdict_cmd = "complete"
                payload = None

            # Map command string to ReviewerVerdict enum
            verdict = _parse_verdict(verdict_cmd)
            _cmd_key = verdict_cmd.lower().strip() if isinstance(verdict_cmd, str) else verdict_cmd
            if _cmd_key not in _KNOWN_VERDICT_COMMANDS:
                audit_log.record("review_verdict_unrecognized", {
                    "raw_command": str(verdict_cmd),
                    "mapped_to": verdict.value,
                })

            # Bound the ANALYZE <-> REVIEW rework cycle independently of the
            # iteration counter, which only advances through EXPAND.
            max_reworks = _get_config_attr(
                config, "MAX_REWORKS_PER_ITERATION", _DEFAULT_MAX_REWORKS
            )
            if verdict == ReviewerVerdict.NEEDS_REWORK:
                reworks_this_iteration += 1
                if reworks_this_iteration > max_reworks:
                    audit_log.record("rework_bound_exhausted", {
                        "iteration": state.iteration,
                        "reworks": reworks_this_iteration,
                        "max_reworks": max_reworks,
                    })
                    is_final = True

            # Record iteration
            iteration_record = IterationRecord(
                iteration=state.iteration,
                phase=state.phase,
                queries=state.queries.copy(),
                results_count=len(state.results),
                summaries_count=len(state.summaries),
                gate_decisions=[
                    g for g in state.gate_log
                    if g not in _flatten_iteration_gates(state.iterations)
                ],
                feedback=payload if isinstance(payload, str) else None,
                reviewer_verdict=verdict,
            )
            state.iterations.append(iteration_record)

            audit_log.record("review_decision", {
                "iteration": state.iteration,
                "verdict": verdict.value if verdict else "unknown",
                "is_final": is_final,
                "payload_type": type(payload).__name__,
            })

            # Route based on verdict
            if verdict == ReviewerVerdict.APPROVED:
                machine.force_phase(PipelinePhase.COMPLETE)

            elif verdict == ReviewerVerdict.NEEDS_REWORK and not is_final:
                state.feedback = payload if isinstance(payload, str) else None
                machine.force_phase(PipelinePhase.ANALYZE)

            elif verdict == ReviewerVerdict.NEEDS_MORE_INFO and not is_final:
                state.feedback = payload if isinstance(payload, str) else None
                reworks_this_iteration = 0
                # If the reviewer provided new queries, use them
                if isinstance(payload, list) and payload:
                    state.queries = payload
                    reviewer_queries_pending = True
                machine.force_phase(PipelinePhase.EXPAND)

            else:
                # Final round or unknown verdict -- accept current report
                logger.info(
                    "REVIEW: accepting report (verdict=%s, is_final=%s)",
                    verdict.value if verdict else "unknown",
                    is_final,
                )
                machine.force_phase(PipelinePhase.COMPLETE)

        else:
            # Should not happen -- unknown phase
            logger.error("Unknown phase: %s", phase)
            machine.force_phase(PipelinePhase.FAILED)

    # Build and return final result
    audit_log.record("pipeline_end", {
        "run_id": run_id,
        "success": state.phase == PipelinePhase.COMPLETE,
        "iterations": state.iteration,
        "gate_count": len(state.gate_log),
    })

    return PipelineResult(
        success=state.phase == PipelinePhase.COMPLETE,
        report=state.report,
        summaries=state.summaries,
        iterations_used=state.iteration,
        gate_log=state.gate_log,
        budget=state.budget,
    )


async def run_campaign_pipeline(
    campaign_spec: Any,
    config: AppConfig,
    search_engine: SearchEngine,
    llm: LLMClient,
    audit_log: AuditLog,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Execute a campaign-specific research pipeline.

    Similar to run_pipeline but uses campaign-specific query expansion,
    reader schema, and the campaign_synthesizer agent instead of the
    generic analyzer.

    The function first runs the standard pipeline to gather sources,
    then passes the summaries through the campaign synthesizer to
    produce a codebook-compliant dossier.

    Args:
        campaign_spec: Campaign specification (CampaignSpec or dict).
        config: Application configuration.
        search_engine: Configured search engine.
        llm: LLM client.
        audit_log: Audit logger.
        event_bus: Optional event bus.

    Returns:
        Campaign dossier dict with keys: inclusion_memo, coded_row,
        evidence_log, uncertainty_note, report_markdown, citations_used.
    """
    # Build a research query from campaign spec (supports both CampaignSpec
    # pydantic model and plain dict). The CampaignSpec field is `year`, but
    # legacy callers may pass `year_start`.
    def _spec_get(key: str, *aliases: str, default: Any = "") -> Any:
        for k in (key, *aliases):
            if hasattr(campaign_spec, k):
                v = getattr(campaign_spec, k)
                if v not in (None, ""):
                    return v
            if isinstance(campaign_spec, dict) and campaign_spec.get(k) not in (None, ""):
                return campaign_spec[k]
        return default

    campaign_name = _spec_get("campaign_name")
    country = _spec_get("country")
    year_start = _spec_get("year", "year_start")

    query = f"{campaign_name} {country} {year_start} protest social media"

    # Run the standard pipeline
    result = await run_pipeline(
        query=query,
        config=config,
        search_engine=search_engine,
        llm=llm,
        audit_log=audit_log,
        event_bus=event_bus,
    )

    # Synthesize into campaign dossier
    try:
        from sar.agents import campaign_synthesizer

        reader_summaries = [
            {
                "title": s.source.title,
                "authors": s.source.authors,
                "year": s.source.year,
                "url": s.source.url,
                "key_claims": s.extraction.get("key_claims", []),
                "methodology": s.extraction.get("methodology", ""),
                "relevance_score": s.quality_score,
                "key_quotes": s.extraction.get("key_quotes", []),
            }
            for s in result.summaries
        ]
        dossier_data = await campaign_synthesizer.run(
            campaign_spec=campaign_spec,
            reader_summaries=reader_summaries,
            llm=llm,
            event_bus=event_bus,
        )

        if dossier_data is None:
            dossier_data = {}

        return {
            "campaign_spec": campaign_spec,
            "coded_row": dossier_data.get("coded_row", {}),
            "evidence_log": dossier_data.get("evidence_log", []),
            "inclusion_memo": dossier_data.get("inclusion_memo", {}),
            "uncertainty_note": dossier_data.get("uncertainty_note", ""),
            "report_markdown": dossier_data.get("report_markdown", result.report or ""),
            "citations_used": dossier_data.get("citations_used", []),
            "pipeline_result": result,
        }

    except Exception as exc:
        logger.error("Campaign synthesis failed -- %s", exc)
        audit_log.record_error("campaign_synthesizer", str(exc), {
            "campaign_name": campaign_name,
        })
        return {
            "campaign_spec": campaign_spec,
            "coded_row": {},
            "evidence_log": [],
            "inclusion_memo": {},
            "uncertainty_note": f"Synthesis failed: {exc}",
            "report_markdown": result.report or "",
            "citations_used": [],
            "pipeline_result": result,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_verdict(command: str | None) -> ReviewerVerdict:
    """Map a reviewer command string to a ReviewerVerdict enum.

    Args:
        command: Raw command string from reviewer agent (e.g., "complete",
            "needs_rework", "needs_more_info").

    Returns:
        The corresponding ReviewerVerdict. Defaults to APPROVED for
        unrecognized commands (the pipeline accepts the report rather than
        looping on a verdict it cannot interpret); the caller audit-logs
        the unrecognized command.
    """
    if command is None:
        return ReviewerVerdict.APPROVED

    normalized = command.lower().strip()

    # Map tool names → verdict values (reviewer tools return tool names)
    _tool_to_verdict = {
        "mark_as_complete": ReviewerVerdict.APPROVED,
        "complete": ReviewerVerdict.APPROVED,
        "request_rework": ReviewerVerdict.NEEDS_REWORK,
        "needs_rework": ReviewerVerdict.NEEDS_REWORK,
        "request_more_info": ReviewerVerdict.NEEDS_MORE_INFO,
        "needs_more_info": ReviewerVerdict.NEEDS_MORE_INFO,
    }
    if normalized in _tool_to_verdict:
        return _tool_to_verdict[normalized]

    for verdict in ReviewerVerdict:
        if verdict.value == normalized:
            return verdict

    logger.warning("Unknown reviewer command '%s' -- treating as APPROVED", command)
    return ReviewerVerdict.APPROVED


def _flatten_iteration_gates(
    iterations: list[IterationRecord],
) -> list[GateDecision]:
    """Collect all gate decisions already recorded in previous iterations.

    Args:
        iterations: List of previous iteration records.

    Returns:
        Flat list of all gate decisions from previous iterations.
    """
    result: list[GateDecision] = []
    for it in iterations:
        result.extend(it.gate_decisions)
    return result
