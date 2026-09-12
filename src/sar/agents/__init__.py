"""Agent definitions — stateless LLM-powered processing units.

Each agent is a stateless async function that receives context and
returns a typed result. Agents depend only on contracts and the
LLMClient protocol — never on search or persistence internals.

Agent inventory:
- query_expander: Expands a user query into complementary search variants.
- source_judge: Scores and filters search results by quality/relevance.
- reader: Generic structured reader (parametrized by extraction schema).
- analyzer: Synthesizes a Markdown report from paper summaries.
- reviewer: Evaluates report quality and decides next action.
- campaign_synthesizer: Produces codebook-compliant campaign dossiers.
"""
