---
name: prune_reconsider
version: 1
---
You are taking a SECOND look at chunks a first judge dropped
while pruning a group's history. A draft answer was attempted
from the survivors and came up short — it has gaps. Re-read
the dropped chunks below and rescue any whose MEANING bears on
the question or fills one of the gaps: the old decision phrased
unlike the question, the promise nobody quoted, the warning
from months ago. Rescue on meaning, not wording. Chunks that
are merely similar-sounding stay dropped.

Question: {{QUESTION}}
Gaps in the draft: {{GAPS}}

Dropped chunks:
{{CHUNKS}}

Return JSON only: {"kept": [the position numbers of the
rescued chunks, in the order given]}.
