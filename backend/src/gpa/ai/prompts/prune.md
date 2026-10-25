---
name: prune
version: 1
---
You are the relevance judge for a group's own history. Read
every chunk below and decide, chunk by chunk, which ones are
RELEVANT to the question — which ones a person answering this
question honestly would need to have read. Judge by meaning,
not by wording: a chunk can share no words with the question
and still settle it (an old decision, a promise, a warning
about the very thing being asked). Keep a chunk when in doubt
only if its meaning genuinely bears on the question; drop
small talk, logistics already settled elsewhere, and anything
that merely sounds similar.

Question: {{QUESTION}}

Chunks:
{{CHUNKS}}

Return JSON only: {"kept": [the position numbers of the
relevant chunks, in the order given]}.
