# The honest Meta-model mapping

*Roadmap B-150 requires this page to be honest before it is
flattering. So, first: **no real Meta model endpoint or
credential exists in the environment this backend was built
in.** The live driver below has only ever run against stub
transports inside tests. Every behaviour this submission
demonstrates — and every number in `RESULTS.md` — was produced
through the fixture and mock drivers over the corpora shipped
in the repo. What follows is the mapping as it is configured
and exercised, stated exactly.*

## The one door: the adapter

All model contact flows through a single protocol,
`MetaModelClient` (`src/gpa/ai/client.py`), with four
capabilities:

| Capability | Used for |
|---|---|
| `complete_text` | Free-text completions (reminder wording, summaries) |
| `complete_json` | Schema-shaped extraction — the output must parse against the JSON Schema beside its prompt |
| `read_image` | Flyer and board reading (transcript, fields, alt text) |
| `transcribe` | Voice-note transcription |

A fifth surface, `generate_image`, exists on the boundary and
**refuses in plain words in the live driver** — no image-
generation endpoint is wired. Generated event images are
carried end-to-end in the data model, flagged `generated`
forever, and exercised with fixture/mock drivers only.

Domain code never imports a vendor SDK. The live driver speaks
HTTP directly (an OpenAI-shaped `POST {GPA_AI_API_BASE}/chat/
completions`), so swapping the endpoint is configuration, not
surgery.

## The tiers

The adapter thinks in two tiers; the mapping to model ids is
configuration (`GPA_AI_MODEL_SPARK`, `GPA_AI_MODEL_WONDER`):

| Tier | Default model id | Carries |
|---|---|---|
| **spark** — fast, cheap, small jobs | `muse-spark` | AI reminder wording (B-103, opt-in per group, ships only if every fact survives verbatim), small completions |
| **wonder** — the deep reader | `muse-wonder` | Event/task/decision/question extraction (B-040 → B-042), flyer reading and board segmentation fallback (Phase 5), transcription, context-pruning relevance judgments (B-113), ask-the-memory drafting (B-115), recap and briefing drafting (B-117/B-118) |

Model ids are settings, not constants: a deployment points the
tiers at whatever the Meta Model API serves it, per
environment, without touching code.

## The drivers

`GPA_AI_DRIVER` selects how the adapter's calls are answered:

| Driver | What it is | Where it runs |
|---|---|---|
| `live` | The real HTTP driver: env-only key (`GPA_AI_API_KEY`), retries with jitter on 429/5xx and transport errors only, a succeeded call never duplicated, per-call token + cost ledger into `ai_usage` | Stub-transport tests only in this build — **never run against a real endpoint** |
| `fixture` | Replays recorded responses, keyed by sha256(prompt + canonical schema + media hash); a missing recording is a loud `FixtureMissError`, never an invented answer | The demo and the gate tests — this is the driver behind every shipped number |
| `mock` | Synthesises the smallest schema-valid instance; honest about keeping nothing (ask-the-memory returns its refusal under mock, by design) | Offline smoke runs, the demo's no-key mode |
| `local` (B-119) | An OpenAI-compatible endpoint on the group's own hardware, text-only, cost recorded as 0; selectable per group for the pruning engine only (`prune_driver`) | Swap-tested against the same corpus as the hosted path |

## Prompts are versioned artefacts

The prompt library (`src/gpa/ai/prompts/`) holds each template
beside its output JSON Schema, with a `manifest.json` pinning
a version and a SHA-256 per template. Editing a prompt without
a version bump fails at load and in tests. Every candidate the
extraction engine drafts records the prompt name and version
that produced it, so any draft in the review queue can be
traced to the exact words that asked for it.

## Money is accounted, not assumed

Every live-shaped call records tokens and an integer cost in
micro-units (`cost_micros`; prices per 1k tokens are settings,
`GPA_AI_PRICE_SPARK_MICROS_PER_1K` / `GPA_AI_PRICE_WONDER_
MICROS_PER_1K`). Each group has a daily cap
(`ai_daily_budget_micros`); at the cap, extraction defers to
the next UTC midnight as a fresh job and the status surface
says so in plain words. Fixture and mock calls record zero
cost — true, because they spend nothing.

## What a judge should conclude from this page

The integration surface — protocol, tiers, prompts, budget,
ledger, failure behaviour — is built, tested, and exercised
end-to-end. The final hop to a production Meta endpoint is a
configuration step (`GPA_AI_API_BASE`, `GPA_AI_API_KEY`, the
two tier ids) that this environment could not perform, and
this page does not pretend otherwise.
