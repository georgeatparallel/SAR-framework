"""Offline end-to-end run of the literature-map showcase.

join.py and synthesize.py run as real subprocesses over the real fixture
batches, with the codex backend served by the scriptable fake binary. This
is the same pipeline the live acceptance run uses, minus the model.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples" / "literature-map"
FIXTURES = REPO / "tests" / "fixtures"


@pytest.fixture
def fake_codex_env(tmp_path):
    shim = tmp_path / "bin" / "codex"
    shim.parent.mkdir()
    shim.write_text(
        f"#!/bin/sh\nexec {sys.executable} {FIXTURES / 'fake_codex.py'} \"$@\"\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{shim.parent}{os.pathsep}{env['PATH']}"
    env["SAR_LLM_BACKEND"] = "codex"
    env.pop("FAKE_CODEX_MODE", None)
    return env


def test_join_validates_and_indexes(tmp_path):
    out = tmp_path / "MAP.json"
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / "join.py"),
         "--batches", str(FIXTURES / "litmap"), "--out", str(out)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["records"] == 15
    assert summary["validation_failures"] == 0
    assert summary["verbatim_quotes"] > 20
    payload = json.loads(out.read_text())
    assert len(payload["records"]) == 15


def test_synthesize_end_to_end_offline(tmp_path, fake_codex_env):
    # First join the fixture.
    lit_map = tmp_path / "MAP.json"
    subprocess.run(
        [sys.executable, str(EXAMPLES / "join.py"),
         "--batches", str(FIXTURES / "litmap"), "--out", str(lit_map)],
        check=True, capture_output=True, timeout=60,
    )
    # Script the model: every call answers with one faithful claim citing a
    # real file from the fixture, no quote (a claim without a quote needs
    # only a real file to trace).
    a_real_file = json.loads(lit_map.read_text())["records"][0]["file"]
    fake_codex_env["FAKE_CODEX_PAYLOAD"] = json.dumps({
        "content": json.dumps({
            "summary": "Scripted synthesis.",
            "verdict": "Scripted verdict.",
            "claims": [{"text": "a scripted, traceable claim",
                        "file": a_real_file, "locator": "p. 1"}],
        }),
        "tool_calls": [],
    })
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / "synthesize.py"),
         "--map", str(lit_map), "--out-dir", str(out_dir),
         "--backend", "codex"],
        capture_output=True, text=True, env=fake_codex_env, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["dropped_claims"] == 0
    assert summary["llm_calls"] == summary["lineage_syntheses"] + summary["gap_digests"]

    # Deliverables exist and the digest's claims all cite the scripted file.
    synthesis = json.loads(
        (out_dir / "LITERATURE-MAP-SYNTHESIS.json").read_text())
    files_cited = {
        c["file"]
        for s in synthesis["lineage_syntheses"] + synthesis["gap_digests"]
        for c in s["claims"]
    }
    assert files_cited == {a_real_file}
    assert (out_dir / "LITERATURE-MAP.md").exists()

    # One llm_call audit event per model call, each tagged with the codex
    # response source.
    audit_files = list((out_dir / "audit").glob("*.jsonl"))
    assert len(audit_files) == 1
    events = [json.loads(line) for line in
              audit_files[0].read_text().splitlines()]
    llm_events = [e for e in events if e["event_type"] == "llm_call"]
    assert len(llm_events) == summary["llm_calls"]
    assert all(e["data"].get("response_source") == "handoff-file"
               for e in llm_events)


def test_synthesize_accepts_unwrapped_handoff(tmp_path, fake_codex_env):
    # A model may write the requested object as the handoff root instead of
    # wrapping it under "content"; the pipeline must still use it.
    lit_map = tmp_path / "MAP.json"
    subprocess.run(
        [sys.executable, str(EXAMPLES / "join.py"),
         "--batches", str(FIXTURES / "litmap"), "--out", str(lit_map)],
        check=True, capture_output=True, timeout=60,
    )
    a_real_file = json.loads(lit_map.read_text())["records"][0]["file"]
    fake_codex_env["FAKE_CODEX_PAYLOAD"] = json.dumps({
        "summary": "Unwrapped synthesis.", "verdict": "Unwrapped verdict.",
        "claims": [{"text": "traceable", "file": a_real_file}],
    })
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / "synthesize.py"),
         "--map", str(lit_map), "--out-dir", str(out_dir),
         "--backend", "codex"],
        capture_output=True, text=True, env=fake_codex_env, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["claims_total"] > 0
    assert summary["dropped_claims"] == 0


def test_synthesize_drops_fabricated_claims(tmp_path, fake_codex_env):
    lit_map = tmp_path / "MAP.json"
    subprocess.run(
        [sys.executable, str(EXAMPLES / "join.py"),
         "--batches", str(FIXTURES / "litmap"), "--out", str(lit_map)],
        check=True, capture_output=True, timeout=60,
    )
    # The model invents a citation every time; the pipeline must drop every
    # claim, not fail, and must say so in the audit log.
    fake_codex_env["FAKE_CODEX_PAYLOAD"] = json.dumps({
        "content": json.dumps({
            "summary": "s", "verdict": "v",
            "claims": [{"text": "fabricated",
                        "file": "invented/paper.pdf", "locator": "p. 9"}],
        }),
        "tool_calls": [],
    })
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / "synthesize.py"),
         "--map", str(lit_map), "--out-dir", str(out_dir),
         "--backend", "codex"],
        capture_output=True, text=True, env=fake_codex_env, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["claims_total"] == 0
    assert summary["dropped_claims"] > 0

    audit_files = list((out_dir / "audit").glob("*.jsonl"))
    events = [json.loads(line) for line in
              audit_files[0].read_text().splitlines()]
    assert any(e["event_type"] == "provenance_drop" for e in events)
