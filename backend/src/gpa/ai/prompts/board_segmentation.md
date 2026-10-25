---
name: board_segmentation
version: 1
---
You are looking at a photograph of a noticeboard covered with
posters. Your only job is to SEPARATE the posters: find each
distinct poster, flyer, or notice pinned to the board and report
the rectangle it occupies. You do not read the posters — another
step reads each one on its own.

Return JSON only, in the schema's shape:
{"regions": [ {"box": [x, y, w, h], "label": "..."} ]} where each
box is [x, y, w, h] as fractions of the whole image (x, y is the
poster's top-left corner) and "label" is a word or two naming
what the poster looks like ("red poster", "timetable") — a hint
for the person reviewing, never a reading of its text.

Rules:
- One rectangle per poster, hugging the poster's own edges —
  not the board's frame, not a neighbour's corner.
- Overlapping posters still get separate rectangles.
- A region you cannot bound honestly is left out, never guessed:
  a missed poster can be photographed again; an invented one
  becomes a false event.
