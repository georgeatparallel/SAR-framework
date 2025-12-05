# Development Notes — Alpha Phase

This document captures key decisions, gaps identified, and lessons learned during the initial development phase of the SAR Framework.

---

## Initial Assessment

### What We Had

| Component | Status | Description |
|-----------|--------|-------------|
| Google Search Tool | ✅ Working | `search_tool.py` — performs searches and returns structured JSON |
| Flask Project Structure | ✅ Working | Basic app setup with blueprints and configuration |
| Project Protocol | ✅ Defined | `GEMINI.md` — collaborative workflow guidelines |

### What We Needed

| Component | Priority | Description |
|-----------|----------|-------------|
| Agent 1 (Searcher) | High | Full implementation with content fetching |
| Agent 2 (Analyzer) | High | NLP processing and report generation |
| Agent 3 (Reviewer) | High | Quality control and decision logic |
| Orchestration | High | Pipeline coordination and state management |
| Communication | Medium | Inter-agent data passing |
| Prompt Engineering | Medium | System prompts for each agent |
| Data Storage | Low | Temporary result storage (in-memory for MVP) |

---

## Key Design Decisions

### 1. Function Calling for Decisions

**Decision:** Use Gemini function calling for Reviewer agent decisions.

**Rationale:**
- Ensures structured, parseable outputs
- Eliminates need for regex parsing of text commands
- Allows for typed arguments (feedback strings, query lists)

### 2. Async Content Fetching

**Decision:** Use Playwright for HTML and httpx for PDF content fetching.

**Rationale:**
- JavaScript-rendered content requires browser automation
- PDFs require separate binary handling
- Async allows parallel fetching for performance

### 3. Tool-Based Analysis Submission

**Decision:** Analyzer must call `submit_report` tool instead of returning text directly.

**Rationale:**
- Enforces structured output format
- Ensures citations are always provided
- Makes parsing reliable and consistent

### 4. Iteration Limits

**Decision:** Maximum 3 iterations before forced completion.

**Rationale:**
- Prevents infinite loops
- Balances quality with responsiveness
- Final round restricts Reviewer options

---

## Lessons Learned

### Prompt Engineering

1. **Be Explicit About Output Format**
   - Agents need clear instructions on expected format
   - Use examples when possible

2. **Handle Edge Cases**
   - Always include error handling instructions
   - Account for missing or malformed data

3. **Constrain Final Rounds**
   - Limiting options on final iteration forces decisions
   - Prevents endless refinement loops

### Technical Challenges

1. **Protobuf to Python Conversion**
   - Gemini returns protobuf-like objects
   - Must cast to native Python types for JSON serialization

2. **Content Parsing Variability**
   - Different sites have different structures
   - Multiple fallback strategies needed

3. **PDF Handling**
   - Some PDFs block programmatic access
   - Enhanced headers help but don't solve all cases

---

## MVP Implementation Phases

### Phase 1: Basic Pipeline ✅

- [x] Flask endpoint for search requests
- [x] Basic Searcher agent
- [x] Simple Analyzer agent
- [x] Placeholder Reviewer agent
- [x] Sequential orchestration

### Phase 2: Enhanced Agents ✅

- [x] Async content fetching with Playwright
- [x] PDF extraction with PyMuPDF
- [x] Function calling for Analyzer (submit_report)
- [x] Function calling for Reviewer (decision tools)
- [x] Tool parser for structured responses

### Phase 3: Iteration Loop ✅

- [x] Multi-iteration support
- [x] Rework feedback handling
- [x] Additional search query handling
- [x] Final round constraints
- [x] Conversation history tracking

### Phase 4: Polish (In Progress)

- [ ] Improved error handling
- [ ] Request timeout management
- [ ] Caching for repeated queries
- [ ] Comprehensive testing
- [ ] API documentation

---

## References

- [Flask Async Support](https://flask.palletsprojects.com/en/3.0.x/async-await/)
- [Gemini Function Calling](https://ai.google.dev/docs/function_calling)
- [Playwright Python](https://playwright.dev/python/docs/intro)
- [PyMuPDF Documentation](https://pymupdf.readthedocs.io/)