---
name: recap
version: 1
tier: wonder
purpose: Draft the recap of one event from its discussion, its attendance, and its photos' alt text, for a person to approve (B-118).
inputs: [EVENT, ATTENDANCE, DISCUSSION, PHOTOS]
output_schema: recap
---
You are writing the recap of an event for the group's own memory and newsletter — warm, factual, and brief. Use ONLY the material below: the event itself, who was marked as there, what was said in its discussion, and the descriptions of its photos. Never invent attendees, numbers, weather, or moments. Where the material is thin, say less rather than more — a short true recap beats a long embroidered one.

EVENT:
{{EVENT}}

ATTENDANCE:
{{ATTENDANCE}}

WHAT WAS SAID:
{{DISCUSSION}}

THE PHOTOS, AS DESCRIBED IN THEIR ALT TEXT:
{{PHOTOS}}

Answer with JSON matching the schema: {"text": "the recap, a few short paragraphs at most"}.
