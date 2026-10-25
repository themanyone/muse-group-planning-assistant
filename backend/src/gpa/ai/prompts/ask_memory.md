---
name: ask_memory
version: 1
---
Answer the member's question from the group's own surviving
history below — and ONLY from it. Every claim in your answer must
be backed by a quote copied exactly from the history, listed in
"citations". If the history does not settle the question, say so
in the answer, plainly ("I couldn't verify this") and cite what
little was found, labelled as unverified. A confident answer with
no evidence is the one failure this product never forgives.

Question: {{QUESTION}}
History:
{{CHUNKS}}

Return JSON only: {"answer": ..., "citations": [{"quote": ...}]}.
