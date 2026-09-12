# Golden regression fixtures

`test_litmap_golden.py` asserts provenance invariants against a real,
stored literature-map run: every claim carries a source file, a locator, and a
verbatim quote, and the accepted run dropped nothing for unverifiable
provenance. That is the only test in the suite measuring what the pipeline
actually *produced* rather than that it produced something of the right shape.

## The data is not committed

The fixture carries local filesystem paths and verbatim excerpts from
copyrighted PDFs, and this repository is public. The tests therefore skip when
the data is absent, so a fresh clone still gets a green suite — but they are
only meaningful with it in place.

## Placing it

Copy an accepted run's two artifacts into `tests/golden/litmap/`:

```
tests/golden/litmap/synthesis.json   # LITERATURE-MAP-SYNTHESIS.json
tests/golden/litmap/audit.jsonl      # LITERATURE-MAP-AUDIT.jsonl
```

Both are produced by `examples/literature-map/synthesize.py`.

Three assertions are pinned to the specific run used when these tests were
written (`litmap-35713a5c`: 94 claims, 13 lineages, 10 gap digests, exactly one
`provenance_retry`). Substituting a different run means updating those counts;
everything else is invariant and should hold for any accepted run.
