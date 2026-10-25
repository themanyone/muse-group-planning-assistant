"""The AI layer (roadmap Phase 3, B-035 → B-048).

Everything a model does for this product flows through this
package, and through exactly one door: ``client.MetaModelClient``
(decision D-18). The pipelines (extraction today; flyer reading,
summaries, and the pruning engine in later phases) know the
protocol and nothing else — no vendor SDK is imported anywhere in
domain code, ever (B-035's acceptance).

Modules:

    client.py          the protocol, the result shape, the registry
    drivers_mock.py    schema-valid canned output (offline)
    drivers_fixture.py recorded responses keyed by prompt hash
    drivers_live.py    the Meta Model API over httpx (B-037)
    promptlib.py       the versioned prompt library (B-038)
    prompts/           the templates + their output schemas
    dates.py           the deterministic date resolver (D-22)
    extraction.py      text -> validated candidate drafts (B-040+)
    evaluation.py      the golden-set harness (B-046)
"""
