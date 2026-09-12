"""Codex CLI implementation of LLMClient.

The Codex CLI is an agentic process rather than a chat-completions endpoint.
Two consequences shape this client:

1. There is no function-calling API. Tool schemas cannot be handed to the
   provider, so they are rendered into the prompt and the model is asked to
   emit a tool call as JSON.
2. Its stdout carries progress narration, and its final message is prose. So
   the structured result is passed through a file the agent writes, not
   through stdout. ``--output-last-message`` is kept only as a fallback.

This mirrors how ``ralph.sh`` drives ``codex exec``: prompt on stdin, a known
output path, and the file read back afterwards.

Token usage is not reported by the CLI, so ``usage`` is zero-filled. Callers
that budget on tokens should treat this backend as unmetered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sar.contracts.llm import LLMRequest, LLMResponse, ToolCall
from sar.llm.client import LLMClientError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.6-terra"

# A prompt larger than this is almost certainly a caller bug (e.g. a whole
# corpus pasted into one request) and would stall the CLI for a long time.
MAX_PROMPT_BYTES = 2_000_000


class CodexProcessError(LLMClientError):
    """The codex process failed in a way worth one retry: it timed out or
    exited nonzero. Parse failures are NOT this class; retrying a model that
    answered, but answered badly, just spends another full call for the same
    likely outcome."""

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _tool_schemas(request: LLMRequest) -> list[dict[str, Any]]:
    """Normalise ToolDefinition objects and raw OpenAI dicts to one shape."""
    out: list[dict[str, Any]] = []
    for t in request.tools or []:
        if isinstance(t, dict):
            fn = t.get("function", t)
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            })
        else:
            out.append({
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            })
    return out


def _render_messages(request: LLMRequest) -> str:
    parts: list[str] = []
    for m in request.messages:
        role = str(m.get("role", "user")).upper()
        content = m.get("content")
        if isinstance(content, list):  # multimodal blocks
            content = " ".join(
                str(b.get("text", "")) for b in content if isinstance(b, dict)
            )
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of prose or a fenced block.

    Also unwraps one level of double encoding: models sometimes emit the
    requested object as a JSON *string* ("{\"content\": ...}"), which is
    valid JSON that parses to str, not dict.
    """
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith('"'):
        try:
            inner = json.loads(stripped)
            if isinstance(inner, str):
                return _extract_json(inner)
        except json.JSONDecodeError:
            pass
    m = _JSON_BLOCK.search(text)
    candidates = [m.group(1)] if m else []
    start = text.find("{")
    if start != -1:
        candidates.append(text[start:])
    for candidate in candidates:
        depth, end = 0, None
        for i, ch in enumerate(candidate):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        try:
            value = json.loads(candidate[:end])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


class CodexClient:
    """LLMClient backed by the local ``codex exec`` binary.

    Attributes:
        _model: Model slug passed to ``--model``.
        _workdir: Directory handed to ``--cd``; the agent's working root.
        _sandbox: One of read-only, workspace-write, danger-full-access.
        _timeout: Seconds before a single call is abandoned.
        _profile: Optional ``--profile`` config layer.
        _binary: Resolved path to the codex executable.
    """

    def __init__(
        self,
        default_model: str = DEFAULT_MODEL,
        *,
        workdir: str | Path | None = None,
        sandbox: str = "workspace-write",
        timeout: float = 900.0,
        profile: str | None = None,
        binary: str | None = None,
        max_attempts: int = 2,
        retry_delay: float = 5.0,
    ) -> None:
        resolved = binary or shutil.which("codex")
        if not resolved:
            raise LLMClientError(
                "codex executable not found on PATH; install the Codex CLI or "
                "pass binary=..."
            )
        self._binary = resolved
        self._model = default_model
        self._workdir = Path(workdir) if workdir else Path.cwd()
        self._sandbox = sandbox
        self._timeout = timeout
        self._profile = profile
        # Local bounded retry for process-level failures. The shared
        # call_with_backoff helper judges retryability by HTTP status and
        # message heuristics that do not apply to a subprocess, so the retry
        # lives here, scoped to CodexProcessError only.
        self._max_attempts = max(1, max_attempts)
        self._retry_delay = retry_delay

    def _compose(self, request: LLMRequest, out_path: Path) -> str:
        tools = _tool_schemas(request)
        lines = [
            "You are running as a non-interactive backend for another program.",
            "Answer the conversation below, then write your answer as JSON.",
            "",
            "## Output contract",
            "",
            f"Write a single JSON object to this exact path: {out_path}",
            "",
            "The object must have this shape:",
            '  {"content": "<your text answer, or null>",',
            '   "tool_calls": [{"name": "<tool>", "arguments": {...}}]}',
            "",
            "Use an empty list for tool_calls when you are answering in prose.",
            "Write the file with a shell command or file tool. Do not print the",
            "JSON as your reply instead of writing it. Write nothing else to",
            "that path.",
        ]
        if tools:
            lines += [
                "",
                "## Tools you may call",
                "",
                "These are not callable functions here. To call one, put its name",
                "and arguments in tool_calls. Arguments must satisfy the schema.",
                "",
                json.dumps(tools, indent=2),
            ]
            if request.tool_choice:
                lines += ["", f"Required tool choice: {json.dumps(request.tool_choice)}"]
        lines += ["", "## Conversation", "", _render_messages(request)]
        return "\n".join(lines)

    async def _run(self, prompt: str, out_path: Path, last_msg: Path) -> str:
        cmd = [
            self._binary, "exec",
            "--cd", str(self._workdir),
            "--sandbox", self._sandbox,
            "--model", self._model,
            "--output-last-message", str(last_msg),
            "--skip-git-repo-check",
            "--ephemeral",
        ]
        if self._profile:
            cmd += ["--profile", self._profile]
        # The agent must be able to write the handoff file even when it sits
        # outside the working root.
        if out_path.parent.resolve() != self._workdir.resolve():
            cmd += ["--add-dir", str(out_path.parent)]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "CODEX_QUIET": "1"},
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=self._timeout
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise CodexProcessError(
                f"codex exec timed out after {self._timeout}s"
            ) from exc
        text = (stdout or b"").decode("utf-8", "replace")
        if proc.returncode != 0:
            raise CodexProcessError(
                f"codex exec exited {proc.returncode}: {text[-800:]}"
            )
        return text

    async def _run_with_retry(self, prompt: str, out_path: Path, last_msg: Path) -> str:
        """Run codex, retrying process-level failures once with a delay."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._run(prompt, out_path, last_msg)
            except CodexProcessError as exc:
                if attempt >= self._max_attempts:
                    raise
                logger.warning(
                    "codex attempt %d/%d failed (%s); retrying in %.0fs",
                    attempt, self._max_attempts, exc, self._retry_delay,
                )
                await asyncio.sleep(self._retry_delay)

    async def call(self, request: LLMRequest) -> LLMResponse:
        """Run one turn through the Codex CLI and return a structured response."""
        model = request.model or self._model
        work = Path(tempfile.mkdtemp(prefix="sar-codex-"))
        out_path = work / f"response-{uuid.uuid4().hex[:8]}.json"
        last_msg = work / "last-message.txt"
        try:
            prompt = self._compose(request, out_path)
            if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
                raise LLMClientError(
                    f"prompt is {len(prompt.encode('utf-8'))} bytes, over the "
                    f"{MAX_PROMPT_BYTES} limit; batch the input instead"
                )
            stdout = await self._run_with_retry(prompt, out_path, last_msg)

            payload: dict[str, Any] | None = None
            source = "handoff-file"
            if out_path.exists():
                text = out_path.read_text(encoding="utf-8")
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, str):
                        # Double-encoded: a JSON string wrapping the object.
                        parsed = json.loads(parsed)
                        source = "handoff-file(unwrapped)"
                    payload = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    payload = None
                if payload is None:
                    payload = _extract_json(text)
                    if payload is not None and source == "handoff-file":
                        source = "handoff-file(repaired)"
            if payload is None and last_msg.exists():
                payload = _extract_json(last_msg.read_text(encoding="utf-8"))
                source = "last-message"
            if payload is None:
                payload = _extract_json(stdout)
                source = "stdout"
            if payload is None:
                raise LLMClientError(
                    "codex produced no parseable JSON; last 800 chars of output: "
                    f"{stdout[-800:]}"
                )

            logger.debug("codex response recovered from %s", source)
            calls = []
            for i, c in enumerate(payload.get("tool_calls") or []):
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                args = c.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                calls.append(ToolCall(
                    id=c.get("id") or f"codex-{i}",
                    name=str(c["name"]),
                    arguments=args if isinstance(args, dict) else {},
                ))
            content = payload.get("content")
            return LLMResponse(
                content=None if content in ("", None) else str(content),
                tool_calls=calls,
                model=model,
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                raw={"source": source, "payload": payload},
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
