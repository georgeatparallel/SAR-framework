#!/usr/bin/env python3
"""A scriptable stand-in for the codex CLI, used by the CodexClient tests.

The real ``codex exec`` reads a prompt on stdin, works, and (per our
contract) writes a JSON handoff file whose path is named inside the prompt.
This fake honours the same interface and is scripted through environment
variables, so every recovery path of the client can be exercised offline:

FAKE_CODEX_MODE:
  handoff          write valid JSON to the handoff path (default)
  handoff-double   write the payload double-encoded (a JSON string of JSON)
  handoff-fenced   write the JSON wrapped in a ```json fence (repair path)
  last-message     write nothing to the handoff; valid JSON to --output-last-message
  stdout           write nothing anywhere; print prose + JSON to stdout
  garbage          write unparseable bytes everywhere
  exit-2           exit nonzero after printing an error
  sleep            sleep FAKE_CODEX_SLEEP seconds (timeout testing)
  flaky            exit 2 on the first run, behave as ``handoff`` afterwards
                   (uses FAKE_CODEX_STATE file as the attempt counter)
FAKE_CODEX_PAYLOAD: JSON to hand back (default: {"content": "ok", "tool_calls": []})
FAKE_CODEX_ARGS_LOG: when set, append argv (one JSON line) for assertion.
FAKE_CODEX_PROMPT_LOG: when set, append each received prompt for assertion.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time


def _arg_value(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main() -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()

    if os.environ.get("FAKE_CODEX_PROMPT_LOG"):
        with open(os.environ["FAKE_CODEX_PROMPT_LOG"], "a",
                  encoding="utf-8") as f:
            f.write(prompt + "\n\n===PROMPT-BOUNDARY===\n\n")

    if os.environ.get("FAKE_CODEX_ARGS_LOG"):
        with open(os.environ["FAKE_CODEX_ARGS_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps(argv) + "\n")

    mode = os.environ.get("FAKE_CODEX_MODE", "handoff")
    payload = os.environ.get(
        "FAKE_CODEX_PAYLOAD", '{"content": "ok", "tool_calls": []}'
    )

    # The client names the handoff path inside the prompt.
    m = re.search(r"exact path: (\S+)", prompt)
    handoff = m.group(1) if m else None
    last_msg = _arg_value(argv, "--output-last-message")

    if mode == "flaky":
        state = os.environ["FAKE_CODEX_STATE"]
        attempts = 0
        if os.path.exists(state):
            with open(state) as f:
                attempts = int(f.read() or 0)
        with open(state, "w") as f:
            f.write(str(attempts + 1))
        if attempts == 0:
            print("transient explosion", file=sys.stderr)
            return 2
        mode = "handoff"

    if mode == "sleep":
        time.sleep(float(os.environ.get("FAKE_CODEX_SLEEP", "5")))
        return 0
    if mode == "exit-2":
        print("codex blew up", file=sys.stderr)
        return 2
    if mode == "garbage":
        if handoff:
            with open(handoff, "w") as f:
                f.write("{not json at all")
        if last_msg:
            with open(last_msg, "w") as f:
                f.write("also } not { json")
        print("no structure here")
        return 0
    if mode == "handoff":
        assert handoff, "prompt did not name a handoff path"
        with open(handoff, "w") as f:
            f.write(payload)
        print("I wrote the file as instructed.")
        return 0
    if mode == "handoff-double":
        assert handoff
        with open(handoff, "w") as f:
            f.write(json.dumps(payload))  # payload str -> JSON string literal
        return 0
    if mode == "handoff-fenced":
        assert handoff
        with open(handoff, "w") as f:
            f.write(f"Here you go:\n```json\n{payload}\n```\n")
        return 0
    if mode == "last-message":
        assert last_msg, "--output-last-message not passed"
        with open(last_msg, "w") as f:
            f.write(f"All done. Result:\n{payload}")
        print("progress narration only")
        return 0
    if mode == "stdout":
        print(f"Thinking...\nFinal answer follows\n{payload}\nGoodbye")
        return 0

    print(f"unknown FAKE_CODEX_MODE {mode}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
