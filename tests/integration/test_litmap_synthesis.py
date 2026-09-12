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


def _join(tmp_path):
    lit_map = tmp_path / "MAP.json"
    subprocess.run(
        [sys.executable, str(EXAMPLES / "join.py"),
         "--batches", str(FIXTURES / "litmap"), "--out", str(lit_map)],
        check=True, capture_output=True, timeout=60,
    )
    return lit_map


def _run_synth(tmp_path, env, lit_map, *extra):
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / "synthesize.py"),
         "--map", str(lit_map), "--out-dir", str(out_dir),
         "--backend", "codex", *extra],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout), out_dir


class TestGapClaimText:
    """A digest must be told what the gap actually claims.

    Sending only an opaque id ("INTRO-P4-S2") made the model infer the
    claim, and it inferred wrong on half of them in a live run. The claim
    text now reaches the prompt, and an undefined gap is declared rather
    than guessed.
    """

    def test_claim_text_reaches_the_prompt(self, tmp_path, fake_codex_env):
        lit_map = _join(tmp_path)
        gaps_in_map = sorted(
            {g for r in json.loads(lit_map.read_text())["records"]
             for g in r["could_support_gap"]}
        )
        assert gaps_in_map, "fixture must exercise at least one gap"
        claim = "Protest imposes oversight costs on the state."
        gaps_file = tmp_path / "gaps.json"
        gaps_file.write_text(json.dumps({g: claim for g in gaps_in_map}))

        # Log every prompt the binary receives.
        prompt_log = tmp_path / "prompts.txt"
        fake_codex_env["FAKE_CODEX_PROMPT_LOG"] = str(prompt_log)
        a_real_file = json.loads(lit_map.read_text())["records"][0]["file"]
        fake_codex_env["FAKE_CODEX_PAYLOAD"] = json.dumps({
            "content": json.dumps({
                "summary": "s", "verdict": "v",
                "claims": [{"text": "t", "file": a_real_file}]}),
            "tool_calls": [],
        })

        summary, out_dir = _run_synth(
            tmp_path, fake_codex_env, lit_map, "--gaps", str(gaps_file))

        assert summary["gaps_undefined"] == []
        assert summary["gaps_defined"] == len(gaps_in_map)
        prompts = prompt_log.read_text()
        assert claim in prompts, "gap claim text never reached the model"
        # And the digest records what it was asked to assess.
        synth = json.loads(
            (out_dir / "LITERATURE-MAP-SYNTHESIS.json").read_text())
        assert all(d["claim"] == claim for d in synth["gap_digests"])
        assert claim in (out_dir / "LITERATURE-MAP.md").read_text()

    def test_undefined_gap_is_declared_not_guessed(
        self, tmp_path, fake_codex_env
    ):
        lit_map = _join(tmp_path)
        prompt_log = tmp_path / "prompts.txt"
        fake_codex_env["FAKE_CODEX_PROMPT_LOG"] = str(prompt_log)
        a_real_file = json.loads(lit_map.read_text())["records"][0]["file"]
        fake_codex_env["FAKE_CODEX_PAYLOAD"] = json.dumps({
            "content": json.dumps({
                "summary": "s", "verdict": "v",
                "claims": [{"text": "t", "file": a_real_file}]}),
            "tool_calls": [],
        })
        # No --gaps at all.
        summary, out_dir = _run_synth(tmp_path, fake_codex_env, lit_map)

        assert summary["gaps_undefined"], "undefined gaps must be reported"
        assert summary["gaps_defined"] == 0
        prompts = prompt_log.read_text()
        assert "was NOT supplied" in prompts
        assert "Do not guess the claim." in prompts
        md = (out_dir / "LITERATURE-MAP.md").read_text()
        assert "label-only" in md, "reader must be warned in the deliverable"

    def test_audit_records_each_undefined_gap(self, tmp_path, fake_codex_env):
        lit_map = _join(tmp_path)
        a_real_file = json.loads(lit_map.read_text())["records"][0]["file"]
        fake_codex_env["FAKE_CODEX_PAYLOAD"] = json.dumps({
            "content": json.dumps({"summary": "s", "verdict": "v",
                                   "claims": [{"text": "t",
                                               "file": a_real_file}]}),
            "tool_calls": [],
        })
        summary, out_dir = _run_synth(tmp_path, fake_codex_env, lit_map)
        events = [json.loads(x) for x in
                  next((out_dir / "audit").glob("*.jsonl")).read_text()
                  .splitlines()]
        undefined = [e["data"]["gap"] for e in events
                     if e["event_type"] == "gap_undefined"]
        assert sorted(undefined) == sorted(summary["gaps_undefined"])


class TestPartialDrop:
    """Dropping failures must not take traceable claims with them."""

    def test_quoteless_claim_survives_a_sibling_failure(
        self, tmp_path, fake_codex_env
    ):
        # One fabricated claim (fails) alongside a real, quoteless one
        # (traces fine). The old substring filter used "" as the needle for
        # the quoteless claim, and "" matches every problem string, so it
        # dropped the good claim too.
        lit_map = _join(tmp_path)
        a_real_file = json.loads(lit_map.read_text())["records"][0]["file"]
        fake_codex_env["FAKE_CODEX_PAYLOAD"] = json.dumps({
            "content": json.dumps({
                "summary": "s", "verdict": "v",
                "claims": [
                    {"text": "fabricated", "file": "invented/paper.pdf"},
                    {"text": "traceable, no quote", "file": a_real_file,
                     "locator": "p. 1"},
                ],
            }),
            "tool_calls": [],
        })
        summary, out_dir = _run_synth(tmp_path, fake_codex_env, lit_map)

        assert summary["dropped_claims"] > 0, "fabrication must be dropped"
        assert summary["claims_total"] > 0, (
            "the traceable quoteless claim must survive")
        synth = json.loads(
            (out_dir / "LITERATURE-MAP-SYNTHESIS.json").read_text())
        files = {c["file"] for s in synth["lineage_syntheses"]
                 + synth["gap_digests"] for c in s["claims"]}
        assert files == {a_real_file}

    def test_malformed_claim_dict_does_not_crash_the_drop(
        self, tmp_path, fake_codex_env
    ):
        # A claim missing the required "text" field must be discarded, not
        # raise ValidationError out of the drop path.
        lit_map = _join(tmp_path)
        a_real_file = json.loads(lit_map.read_text())["records"][0]["file"]
        fake_codex_env["FAKE_CODEX_PAYLOAD"] = json.dumps({
            "content": json.dumps({
                "summary": "s", "verdict": "v",
                "claims": [
                    {"text": "fabricated", "file": "invented/paper.pdf"},
                    {"file": a_real_file},          # no "text"
                ],
            }),
            "tool_calls": [],
        })
        summary, _ = _run_synth(tmp_path, fake_codex_env, lit_map)
        assert summary["dropped_claims"] > 0
        assert summary["claims_total"] == 0
