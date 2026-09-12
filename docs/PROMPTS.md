# Agent prompts

This document specifies the prompts and tool contracts for the six LLM agents in the SAR v2 pipeline. All prompt text is quoted verbatim from the source modules under `src/sar/agents/`; the code is the ground truth, this file only mirrors it.

---

## Table of contents

- [Prompts are the interface](#prompts-are-the-interface)
- [Agent 1: Query expander](#agent-1-query-expander)
- [Agent 2: Source quality judge](#agent-2-source-quality-judge)
- [Agent 3: Reader](#agent-3-reader)
- [Agent 4: Analyzer](#agent-4-analyzer)
- [Agent 5: Reviewer](#agent-5-reviewer)
- [Agent 6: Campaign synthesizer](#agent-6-campaign-synthesizer)

---

## Prompts are the interface

Every agent talks to the LLM through the same contract: an `LLMRequest` carrying a system message, a user message, a list of tool schemas (OpenAI function-calling format, defined centrally in `src/sar/llm/tools.py`), and usually a forced `tool_choice`. The agent reads the answer back from `response.tool_calls`. How the tool schemas reach the model depends on the backend:

- **OpenAI backend** (`OpenAIClient` in `src/sar/llm/client.py`): the tool schemas and `tool_choice` are passed directly to the provider's function-calling API. The model returns native tool calls.
- **Codex CLI backend** (`CodexClient` in `src/sar/llm/codex_client.py`): the Codex CLI has no function-calling API, so `CodexClient._compose()` renders the same schemas into the prompt text and instructs the model to write its tool calls as JSON to a handoff file, which the client reads back and converts into `ToolCall` objects. Stdout is only progress narration; the structured result travels through the file.

The output-contract block that `_compose()` prepends to every request, quoted verbatim (`{out_path}` is the per-call handoff file path):

```
You are running as a non-interactive backend for another program.
Answer the conversation below, then write your answer as JSON.

## Output contract

Write a single JSON object to this exact path: {out_path}

The object must have this shape:
  {"content": "<your text answer, or null>",
   "tool_calls": [{"name": "<tool>", "arguments": {...}}]}

Use an empty list for tool_calls when you are answering in prose.
Write the file with a shell command or file tool. Do not print the
JSON as your reply instead of writing it. Write nothing else to
that path.
```

When the request carries tools, `_compose()` appends this section (followed by `json.dumps(tools, indent=2)` and, if a tool choice is forced, a line `Required tool choice: {json.dumps(request.tool_choice)}`):

```
## Tools you may call

These are not callable functions here. To call one, put its name
and arguments in tool_calls. Arguments must satisfy the schema.
```

Finally a `## Conversation` section renders the messages as `[SYSTEM]` / `[USER]` blocks. If the handoff file is missing or malformed, the client falls back to parsing JSON out of the `--output-last-message` file, then out of stdout, and raises `LLMClientError` if nothing parses.

In the prompt quotations below, `{placeholders}` mark runtime f-string interpolations. Where a prompt splices in a module-level constant (`_REPORT_SECTIONS`, `_EVALUATION_CRITERIA`, `_NORMALIZATION_RULES`, `_INCLUSION_CRITERIA`), the constant's text is shown resolved in place and identified in the notes.

---

## Agent 1: Query expander

Module: `src/sar/agents/query_expander.py`

### Role

Takes the user's natural-language research question and expands it into complementary search queries (default 5) with academic phrasing, temporal scoping, and synonym variants, to maximize recall across academic databases, grey literature, and web sources.

### System prompt

```
You are a search query expansion specialist for academic research. Your task is to generate diverse search queries that will maximize recall across academic databases, grey literature, and web sources.
```

### User prompt

`{context_block}` is empty, or `\nAdditional context: {context}` when a context string is passed.

```
Given the research question: "{query}"
{context_block}

Generate exactly {num_queries} complementary search queries. Rules:
- Vary specificity from broad to narrow.
- Add academic/formal phrasing (e.g. "empirical evidence", "systematic review").
- Include synonyms and alternative terminology.
- Include at least one query targeting grey literature or reports.
- Include temporal scoping where useful (e.g. date ranges).
- Keep each query concise (under 12 words).
- Do NOT repeat the original query verbatim.

Call the submit_queries tool with exactly {num_queries} query strings.
```

### Tool contract

`submit_queries` (forced via `tool_choice`). Description: "Submit {num_queries} expanded search queries. Return a diverse set of queries covering different angles of the research question."

```json
{
  "type": "object",
  "properties": {
    "queries": {
      "type": "array",
      "items": {"type": "string"},
      "description": "List of expanded search query strings. Each should be concise (under 12 words)."
    }
  },
  "required": ["queries"]
}
```

### Behavior notes

- If the response carries no `submit_queries` tool call, `run()` falls back to parsing the text content: JSON array, JSON object wrapping an array under `queries`/`variants`/`results` (or any string values), or newline-separated lines with bullets and numbering stripped.
- The returned list always starts with the original query and is deduplicated case-insensitively.
- On any exception during the LLM call, `run()` logs a warning and returns just the original query. The docstring lists `LLMClientError` as raised, but the broad `except Exception` means it never actually propagates.

### Error handling

Degrades in-agent; nothing propagates. The orchestrator (`runner.py`, EXPAND phase) additionally wraps the call and falls back to `[query]` on any exception.

---

## Agent 2: Source quality judge

Module: `src/sar/agents/source_judge.py`

### Role

Scores search results 0-10 for relevance to the query, authority (peer review, citation count, source reputation), and recency, then filters out low-quality sources while preserving a minimum result count. Only the top `MAX_JUDGE` (40) results by citation count are sent for scoring; at least `MIN_KEEP` (5) results are always kept; the default score threshold is `DEFAULT_MIN_SCORE` (4.0).

### System prompt

```
You are an academic source quality assessor. For each source, evaluate its relevance to the research query, authority (peer-reviewed, citation count, source reputation), and recency. Peer-reviewed sources with citations score higher.
```

### User prompt

`{source_summaries}` is `json.dumps(...)` of compact per-result dicts with keys `url`, `title`, `snippet` (truncated to 300 chars), `source`, `year`, `citation_count`, `is_peer_reviewed`.

```
Research query: "{query}"

Score each source below 0-10 for overall relevance and quality. Call the submit_scores tool with one entry per source.

Sources:
{source_summaries}
```

### Tool contract

`submit_scores` (forced via `tool_choice`). Description: "Submit relevance scores for each search result. Score each source 0-10 based on relevance, authority, and recency."

```json
{
  "type": "object",
  "properties": {
    "scores": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "url": {"type": "string", "description": "The URL of the search result."},
          "score": {"type": "number", "description": "Relevance score 0-10."},
          "reason": {"type": "string", "description": "Brief reason for the score."}
        },
        "required": ["url", "score", "reason"]
      },
      "description": "One scoring entry per search result."
    }
  },
  "required": ["scores"]
}
```

### Behavior notes

- If no tool call arrives, the agent attempts a best-effort JSON parse of the text content (a bare JSON list, or the first list value inside a JSON object).
- Scores are mapped back to results by URL; unmatched results default to 5.0. A partial score set is used with a logged warning.
- Results below `min_score` are dropped, but if fewer than 5 survive, the top 5 by score are kept regardless. Output is sorted by score descending.
- On any exception, `run()` returns all input results unfiltered. As with the query expander, the docstring's `LLMClientError` never propagates in practice.

### Error handling

Degrades in-agent; returns the unfiltered input on failure. The orchestrator (JUDGE phase) also wraps the call and keeps the unfiltered results on any exception.

---

## Agent 3: Reader

Module: `src/sar/agents/reader.py`

### Role

Generic structured reader. Reads a single document (content truncated to `MAX_CONTENT_CHARS` = 6000) and extracts structured information according to a configurable extraction schema. The same parametrized agent serves both generic paper reading (default `PAPER_SUMMARY_SCHEMA`: `key_claims`, `methodology`, `relevance_score`, `key_quotes`) and campaign-specific extraction (a custom schema plus a `context` dict, e.g. the campaign spec, injected into the prompt). `run_batch()` fans out concurrent readers (default concurrency 10).

### System prompt

```
You are an expert research extraction agent. Your task is to carefully read the provided source and extract structured information according to the specified schema. Only include facts directly supported by the source. Do not invent values.
```

### User prompt

`{context_block}` is empty, or `\nAdditional context:\n{json.dumps(context, indent=2)}\n` when a context dict is passed. `{author_str}` is the first three authors comma-joined, or "Unknown".

```
Research query: "{query}"

Source: {title} ({year})
Authors: {author_str}
Citations: {citation_count}
{context_block}
Content:
{content or snippet}

Extract the requested fields by calling the submit_extraction tool.
```

### Tool contract

`submit_extraction` (forced via `tool_choice`). Description: "Submit the structured extraction from this source. Include all fields specified in the schema."

The parameter schema is generated from the extraction schema's field names and descriptions. Typing rule in `submit_extraction_tool()`: a field whose name contains `score` becomes `number`; a name containing `findings`, `claims`, or `quotes` becomes `array` of `string`; everything else becomes `string`. All schema fields are required. With the reader's default `PAPER_SUMMARY_SCHEMA` this resolves to:

```json
{
  "type": "object",
  "properties": {
    "key_claims": {
      "type": "array",
      "items": {"type": "string"},
      "description": "3-5 specific claims relevant to the query"
    },
    "methodology": {
      "type": "string",
      "description": "One sentence: type of study, model, or method used"
    },
    "relevance_score": {
      "type": "number",
      "description": "0-10 integer, how directly relevant to the query"
    },
    "key_quotes": {
      "type": "array",
      "items": {"type": "string"},
      "description": "1-2 direct verbatim quotes most relevant to the query"
    }
  },
  "required": ["key_claims", "methodology", "relevance_score", "key_quotes"]
}
```

Note: `submit_extraction_tool(None)` has its own six-field generic default (`summary`, `key_findings`, `methodology`, `relevance_to_query`, `quality_score`, `limitations`), but `reader.run()` always passes a schema (`extraction_schema or PAPER_SUMMARY_SCHEMA`), so that tool-level default is never exercised by the reader.

### Behavior notes

- If the source has neither content nor snippet, no LLM call is made; a minimal dict is returned with metadata plus schema fields filled with `0` (score fields), `[]` (claims/findings/quotes fields), or `"N/A"`.
- If no tool call arrives, the agent parses JSON from the text content, including from a markdown code fence.
- The extraction is merged with source metadata (`title`, `authors`, `year`, `url`, `citation_count`), and `relevance_score`/`quality_score` are coerced to `int` (5 on coercion failure).
- On any exception, `run()` returns a metadata-only fallback; with the default schema it sets `key_claims` to the snippet's first 200 chars, `methodology` to "N/A", `relevance_score` to 3, and `key_quotes` to `[]`.
- `run_batch()` gathers with `return_exceptions=True`: failed reads are logged and excluded, never fatal. Output is sorted by `relevance_score` (or `quality_score`) descending.

### Error handling

Degrades in-agent; `run()` catches all exceptions, and `run_batch()` drops failed items. The orchestrator (READ phase) also wraps the batch and sets an empty summary list on failure.

---

## Agent 4: Analyzer

Module: `src/sar/agents/analyzer.py`

### Role

Synthesizes a structured Markdown research report from the reader agents' paper summaries, citing every factual claim with inline `[Source N]` references keyed to a numbered source list, and delivers the result through the `submit_report` tool.

### System prompt

The numbered section list is the module constant `_REPORT_SECTIONS`, shown resolved in place.

```
You are an Expert Research Analyst. Your task is to synthesize a comprehensive, well-structured Markdown research report from the provided sources.

REQUIRED REPORT STRUCTURE — include all six sections in this order:
1. Executive Summary — 2-3 sentence overview of the answer to the query.
2. Key Findings — bullet list of the most important results, each with at least one [Source N] citation.
3. Methodology — describe the research approaches used in the sources.
4. Evidence — detailed synthesis of evidence from all sources, grouped by theme.
5. Gaps & Limitations — what the available sources do not cover or where evidence is weak.
6. Conclusion — direct answer to the research question with confidence statement.

CITATION RULES:
- Every factual claim MUST be followed by an inline citation: [Source N].
- Use the exact [Source N] numbering from the source list provided.
- Do not fabricate information not found in the sources.
- If sources disagree, note the disagreement explicitly.

When you have composed the complete report, call submit_report with the full Markdown text and the list of all cited source URLs.
```

### User prompt

`{sources_block}` is one numbered block per summary (`[Source N] {title} ({year})`, `URL:`, `Key claims:`, `Methodology:`, optional `Summary:`), or `(No sources available)`.

```
Research question: {query}

--- SOURCES ---
{sources_block}
--- END SOURCES ---

Write the report now, following the required structure and citation rules.
```

On rework iterations, this addendum is appended to the user prompt:

```


--- REVISION NOTES ---
The following reviewer feedback must be addressed in this revision:
{feedback}
--- END REVISION NOTES ---
```

### Tool contract

`submit_report` (forced via `tool_choice`). Description: "Submit the completed research report. Call this as your final action after composing the full Markdown report."

```json
{
  "type": "object",
  "properties": {
    "report": {
      "type": "string",
      "description": "The full, final, well-structured research report in Markdown format."
    },
    "citations_used": {
      "type": "array",
      "items": {"type": "string"},
      "description": "List of every source URL cited in the report, taken exactly from the 'url' field of the corresponding search result."
    }
  },
  "required": ["report", "citations_used"]
}
```

### Behavior notes

- No in-agent fallback parsing: `run()` only accepts a `submit_report` tool call. It returns `{"report": ..., "citations_used": ...}` from the call's arguments.
- If the LLM responds without calling `submit_report`, `run()` raises `ValueError` naming the tool calls it did get.

### Error handling

`LLMClientError` from the client and the `ValueError` above both propagate. The orchestrator (ANALYZE phase) catches any exception, records it in the audit log, and degrades by setting the report to an empty string.

---

## Agent 5: Reviewer

Module: `src/sar/agents/reviewer.py`

### Role

Evaluates the analyzer's report against explicit quality criteria and decides the next pipeline action by calling exactly one of three tools: `mark_as_complete` (accept), `request_rework` (fix with existing sources), or `request_more_info` (search again with new queries). On the final round the third option is removed.

### System prompt

The numbered criteria are the module constant `_EVALUATION_CRITERIA`, shown resolved in place. `{round_note}` is empty on non-final rounds.

```
You are a Critical Research Reviewer. Your role is to evaluate the quality of a research report and decide the appropriate next action.

EVALUATION CRITERIA — check each in order:
1. Citation coverage — every factual claim must have an inline [Source N] citation.
2. Factual grounding — no claims that go beyond what the sources say; no hallucinations or unsupported assertions.
3. Logical structure — the report must follow the required six-section format (Executive Summary, Key Findings, Methodology, Evidence, Gaps, Conclusion).
4. Completeness — the report must directly answer the research question and cover all major angles supported by the available sources.
5. No hallucinations — reject any report that invents statistics, citations, or sources not present in the submitted source list.

{round_note}DECISION RULES:
- If the report meets all criteria: call mark_as_complete.
- If the report has flaws fixable with the existing sources: call request_rework with specific, actionable feedback.
- If the sources are fundamentally insufficient to answer the question (and this is not the final round): call request_more_info with targeted search queries that would supply the missing information.
Be decisive — do not request rework for minor stylistic issues.
```

When `is_final_round=True`, `{round_note}` resolves to this paragraph (it ends with a blank line, so `DECISION RULES:` starts a new paragraph):

```
NOTE: This is the final review round. You may only call mark_as_complete or request_rework — requesting more information is not available.
```

### User prompt

```
Original research question: {query}

--- REPORT TO REVIEW ---
{report}
--- END REPORT ---

Evaluate the report against all criteria and call the appropriate tool.
```

### Tool contract

The tool set comes from `get_reviewer_tools(is_final_round)`. No `tool_choice` is forced; the model picks one. On non-final rounds all three tools are offered; on the final round `request_more_info` is removed from the list (no budget for more searches).

`mark_as_complete`. Description: "Call this when the analysis is satisfactory, accurate, and fully addresses the user's original query."

```json
{
  "type": "object",
  "properties": {},
  "required": []
}
```

`request_rework`. Description: "Call this when the analysis is flawed, inaccurate, or incomplete, but the existing source information appears sufficient to correct it."

```json
{
  "type": "object",
  "properties": {
    "feedback": {
      "type": "string",
      "description": "Specific, actionable feedback for the Analyzer Agent. Explain what is missing, incorrect, or needs improvement."
    }
  },
  "required": ["feedback"]
}
```

`request_more_info`. Description: "Call this when the analysis is fundamentally weak because sources are outdated, irrelevant, or insufficient. Also call this if the analysis mentions data-fetching errors."

```json
{
  "type": "object",
  "properties": {
    "queries": {
      "type": "array",
      "items": {"type": "string"},
      "description": "New, specific search query strings to find better information."
    }
  },
  "required": ["queries"]
}
```

### Behavior notes

- `run()` returns a `(verdict_command, payload)` tuple: `("complete", None)`, `("needs_rework", feedback)`, or `("needs_more_info", queries)`.
- Only the first tool call in the response is considered.
- If the model calls an unknown tool, or produces no tool call at all, the agent logs a warning and defaults to `("complete", None)`. The pipeline therefore fails open: an inarticulate reviewer accepts the report rather than looping.
- The `is_final_round` rule is enforced twice: the `request_more_info` tool is removed from the offered tool list, and the round note in the system prompt says so explicitly.

### Error handling

`LLMClientError` propagates (there is no try/except around the LLM call). The orchestrator (REVIEW phase) catches any exception and degrades by treating the verdict as `complete`, accepting the current report.

---

## Agent 6: Campaign synthesizer

Module: `src/sar/agents/campaign_synthesizer.py`

### Role

Produces a codebook-compliant campaign dossier for a protest-campaign dataset from the campaign readers' evidence summaries. The dossier bundles an inclusion memo, a coded row covering all 39 codebook columns, a per-field evidence log, an uncertainty note, a narrative Markdown report, and the cited URLs, delivered in one `submit_campaign_dossier` call. The raw output must still pass `campaign.validators` before persistence.

### System prompt

`{codebook_block}` is generated at runtime by `get_codebook_prompt_block()` in `src/sar/campaign/schema.py`; it lists every categorical field with its exact allowed values, the trivalued and binary field groups, and general numeric rules, derived from the same `DATASET_COLUMNS` schema that builds the tool contract. The other two blocks are the module constants `_NORMALIZATION_RULES` and `_INCLUSION_CRITERIA`, shown resolved in place.

```
You are an Expert Political Science Research Coder. Your task is to synthesize evidence from multiple sources and produce a codebook-compliant campaign dossier for inclusion in a protest dataset.

--- CODEBOOK CONSTRAINTS ---
{codebook_block}
--- END CODEBOOK ---

--- NORMALIZATION RULES ---
- Trivalued fields (nonviolent, organised, external_support, outcome): use ONLY `0.0` (no/failure), `0.5` (partial/mixed), `1.0` (yes/success), or `NA`.
- Binary fields (platform_blocked, internet_shutdown): use ONLY `0.0` (no), `1.0` (yes), or `NA`.
- Numeric fields: provide bare numbers with no units, prose, or ranges. Use `NA` when the value cannot be determined.
- Categorical fields: use only the exact allowed values listed in the codebook.
- Do NOT put narrative or caveats in coded_row fields. Place all justification, uncertainty, and contested interpretations in evidence_log, uncertainty_note, and report_markdown.
--- END NORMALIZATION RULES ---

--- INCLUSION CRITERIA ---
A campaign should be INCLUDED if it is:
- A documented mass mobilization or protest campaign.
- Sufficiently covered by available sources to code at least the key fields (outcome, duration_days, repression_level).
Mark `included` as 'yes', 'no', or 'partial' in the inclusion_memo.
--- END INCLUSION CRITERIA ---

Your output must cover ALL 39 codebook columns in coded_row. For each field you code, provide an evidence_log entry citing the source. Write a full narrative report_markdown summarizing the campaign and your coding decisions. Describe all major uncertainties in uncertainty_note.

When you have completed the full dossier, call submit_campaign_dossier.
```

### User prompt

`{campaign_context}` lists the campaign name, country, year, optional region, optional notes/guidance, and optional priority fields from the `CampaignSpec`. `{evidence_block}` is one numbered `[Evidence N]` block per reader summary (title, URL, summary text, and any extracted fields such as `outcome`, `duration_days`, `repression_level`, `scope_signal`, and timeline facts), or `(No source evidence available)`.

```
--- CAMPAIGN SPECIFICATION ---
{campaign_context}
--- END SPECIFICATION ---

--- SOURCE EVIDENCE ---
{evidence_block}
--- END EVIDENCE ---

Code this campaign now. Produce a complete, accurate dossier and call submit_campaign_dossier with all required fields.
```

### Tool contract

`submit_campaign_dossier` (forced via `tool_choice`). Description: "Submit the final campaign dossier, coded row, evidence log, and citation URLs."

```json
{
  "type": "object",
  "properties": {
    "inclusion_memo": {
      "type": "object",
      "properties": {
        "campaign_name": {"type": "string"},
        "country": {"type": "string"},
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
        "included": {"type": "string"},
        "reason_for_inclusion_or_exclusion": {"type": "string"},
        "notes_on_scope_ambiguity": {"type": "string"}
      },
      "required": ["campaign_name", "country", "start_date", "end_date", "included", "reason_for_inclusion_or_exclusion", "notes_on_scope_ambiguity"]
    },
    "coded_row": {
      "type": "object",
      "properties": {"<column>": {"type": ["string", "number", "null"]}},
      "required": ["<all 39 DATASET_COLUMNS>"]
    },
    "evidence_log": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "field_name": {"type": "string"},
          "coded_value": {"type": ["string", "number", "null"]},
          "source_type": {"type": "string"},
          "source_citation": {"type": "string"},
          "source_link_or_identifier": {"type": "string"},
          "notes": {"type": "string"}
        },
        "required": ["field_name", "coded_value", "source_type", "source_citation", "source_link_or_identifier", "notes"]
      }
    },
    "uncertainty_note": {"type": "string"},
    "report_markdown": {"type": "string"},
    "citations_used": {"type": "array", "items": {"type": "string"}}
  },
  "required": ["inclusion_memo", "coded_row", "evidence_log", "uncertainty_note", "report_markdown", "citations_used"]
}
```

The `coded_row` properties are generated programmatically: one entry of type `["string", "number", "null"]` per column in `DATASET_COLUMNS` (`src/sar/campaign/schema.py`), and all 39 are required: `country`, `year_start`, `campaign_name`, `demand_type`, `nonviolent`, `organised`, `external_support`, `prior_campaigns`, `region`, `outcome`, `duration_days`, `peak_participation`, `peak_participation_share`, `onset_to_peak_days`, `platform_dominant`, `platform_blocked`, `repression_level`, `internet_shutdown`, `press_freedom_score`, `rsf_rank`, `freedom_house_score`, `fh_pr`, `fh_cl`, `polity_score`, `pts_score`, `v_dem_libdem`, `v_dem_polyarchy`, `internet_penetration`, `mobile_penetration`, `urbanisation_rate`, `gini_coefficient`, `pop_15_64_pct`, `pop_0_14_pct`, `internet_censorship`, `social_media_penetration`, `pre_campaign_repression`, `journalist_arrests`, `ethnic_fractionalisation`, `religious_fractionalisation`.

### Behavior notes

- No in-agent fallback parsing: `run()` only accepts a `submit_campaign_dossier` tool call and returns its arguments dict unchanged.
- If the LLM responds without calling the tool, `run()` raises `ValueError` naming the tool calls it did get.
- The returned dict is raw model output; normalization (`normalize_coded_row`) and validation happen downstream in `sar.campaign`.

### Error handling

`LLMClientError` and the `ValueError` above propagate. The campaign batch runner (`src/sar/campaign/batch.py`) catches any exception per campaign, records the slug under `manifest["failed"]`, and continues with the next campaign.
