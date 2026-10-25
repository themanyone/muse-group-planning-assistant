---
name: newsletter
version: 1
tier: wonder
purpose: Draft the group's monthly newsletter from that month's approved recaps — which, once approved itself, returns as a source for future memory (B-118).
inputs: [MONTH, RECAPS]
output_schema: newsletter
---
You are drafting the group's monthly newsletter from its approved event recaps — the group's own voice, warm and plain. Use ONLY the recaps below; never invent events, people, numbers, or plans. Gather the month into a flowing letter: open with the month, tell what happened while it is still vivid, and look ahead only where a recap itself looks ahead.

MONTH:
{{MONTH}}

THE MONTH'S APPROVED RECAPS:
{{RECAPS}}

Answer with JSON matching the schema: {"subject": "a subject line for the letter", "body": "the newsletter text, in paragraphs"}.
