# Fixture recordings

Recorded model responses for the fixture driver (roadmap B-036),
keyed by prompt hash. Each file here is `<key>.json`, holding
`{"text": ..., "data": ..., "usage": {...}}` — `data` for
structured (complete_json) calls, `text` for the rest, `usage`
optional token/cost counts.

The key for a call is `gpa.ai.drivers_fixture.prompt_key(prompt,
schema=..., media=...)`. Tests mostly record in code instead
(`FixtureDriver.add_json` / `add_text`), using the exact prompt
the pipeline renders; files here serve the offline demo.

A missing recording is a loud error (FixtureMissError), never an
invented answer.
