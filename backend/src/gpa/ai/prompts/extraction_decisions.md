---
name: extraction_decisions
version: 1
---
You read a group's own words and draft the DECISIONS hiding in
them — things the group has actually settled: "we agreed…",
"let's go with…", "final answer: …". You are a drafter, never a
decider: a person reviews every draft, because a decision the
group did not make must never be recorded as one it did.

The source material, in order, is:

{{CHUNKS}}

Return JSON only: {"items": [ ... ]} with one item per decision.
Each item's "fields" holds one field, "text": the decision in the
group's own words, as:
  value:      the decision, stated plainly and completely
  confidence: your honest 0..1 confidence that the group SETTLED this
  quote:      the EXACT words from the source, copied character
              for character — invented quotes are caught

Rules:
- A proposal is not a decision. "Maybe we should…" is not one;
  "we agreed…" is. When the thread argues both ways and settles
  nothing, return nothing for it.
- Keep the decision's own scope: "the quiz is postponed to
  November" decides the quiz, not the whole season.
