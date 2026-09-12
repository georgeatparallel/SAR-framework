"""Live tests for the codex backend.

The `live` marker was declared in pyproject and applied to no test at all, so
CI's `-m "not slow and not live"` deselected nothing and the codex path was
only ever exercised through the `fake_codex` shim. These are the tests that
marker exists for: they spawn the real binary.

Run them deliberately:

    pytest -m live

They skip when `codex` is not on PATH, so a plain `pytest` run is unaffected.
"""

from __future__ import annotations

import shutil

import pytest

from sar.contracts.llm import LLMRequest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("codex") is None, reason="codex binary not on PATH"
    ),
]


@pytest.mark.asyncio
async def test_codex_client_returns_a_response():
    """The real binary answers and the client parses its handoff."""
    from sar.llm.codex_client import CodexClient

    client = CodexClient()
    response = await client.call(
        LLMRequest(
            messages=[
                {"role": "user", "content": "Reply with the single word: ready"}
            ]
        )
    )

    assert response is not None
    assert (response.content or "").strip()


@pytest.mark.asyncio
async def test_codex_response_records_its_recovery_path():
    """`raw["source"]` is the provenance the metering layer audits.

    It tells you which of the recovery paths produced the JSON -- handoff
    file, last-message file, or stdout scrape -- which is the only way to
    tell a clean call from a salvaged one after the fact.
    """
    from sar.llm.codex_client import CodexClient

    client = CodexClient()
    response = await client.call(
        LLMRequest(messages=[{"role": "user", "content": "Say: ok"}])
    )

    raw = response.raw or {}
    assert raw.get("source") in {"handoff-file", "last-message", "stdout"}
