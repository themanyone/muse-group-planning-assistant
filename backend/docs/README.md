# Group Planning Assistant — Documentation

*The documentation set (roadmap §6A.8, D-34). This index is slice
B-181: the architecture every later Phase 18 slice builds inside.
The set lives in this repo, is reviewed like code, and ships in
every release ZIP. A first edition of the full set was authored
with the roadmap itself (2026-10-24) and is kept beside the
roadmap in the team's goal files; the slices of Phase 18
(B-182 → B-200) bring each chapter into this `docs/` tree and
keep it true as the code lands, file by file.*

## Reading order — by reader

Read top to bottom in your role's column; the set is ordered so
each reader meets what they need before what they merely want.

### 1. People who use the app (no technical knowledge assumed)

| Order | Chapter | Slice | State |
|---|---|---|---|
| 1 | [User guide I — start + groups](01-user-guide-start-and-groups.md) | B-182 | ✅ built |
| 2 | [User guide II — sources](02-user-guide-sources.md) | B-183 | ✅ built |
| 3 | [User guide III — the review queue](03-user-guide-review-queue.md) | B-184 | ✅ built |
| 4 | [User guide IV — calendar + events](04-user-guide-calendar-and-events.md) | B-185 | ✅ built |
| 5 | [User guide V — planning](05-user-guide-planning.md) | B-186 | ✅ built |
| 6 | [User guide VI — reminders, digests, memory](06-user-guide-reminders-digests-memory.md) | B-187 | ✅ built |

### 2. Group admins and whoever runs the server

| Order | Chapter | Slice | State |
|---|---|---|---|
| 7 | [Admin + operator guide](07-admin-and-operator-guide.md) | B-188 | ✅ built |
| 8 | [Deployment + release](18-deployment-and-release.md) | B-199 | ✅ built |

### 3. Frontend developers (Geoffrey + Henry's surface)

| Order | Chapter | Slice | State |
|---|---|---|---|
| 9 | [API + frontend guide](15-api-and-frontend-guide.md) | B-196 | ✅ built |

### 4. Backend developers and maintainers

| Order | Chapter | Slice | State |
|---|---|---|---|
| 10 | [Architecture diagram pack](08-architecture-diagram-pack.md) | B-189 | ✅ built |
| 11 | [Module atlas](09-module-atlas.md) | B-190 | ✅ built |
| 12 | [Internal encrypted API](10-internal-encrypted-api.md) | B-191 | ✅ built |
| 13 | [Data model + ER pack](11-data-model-and-er-pack.md) | B-192 | ✅ built |
| 14 | [AI pipeline documents](12-ai-pipeline.md) | B-193 | ✅ built |
| 15 | [Security + cryptography](13-security-and-cryptography.md) | B-194 | ✅ built |
| 16 | [Durability + recovery](14-durability-and-recovery.md) | B-195 | ✅ built |
| 17 | [Per-code-file atlas](16-per-file-atlas.md) | B-197 | ✅ built |
| 18 | [Testing + quality](17-testing-and-quality.md) | B-198 | ✅ built |

## Docs-as-code rules (binding on every Phase 18 slice)

1. **Same repo, same review, same ZIP.** Documentation changes
   are commits like code changes; `scripts/make_release_zip.py`
   ships `docs/` in the train anatomy, and no release is cut
   from a tree whose docs gate is red.
2. **One truth, three depths (§6A.8).** A fact lives in the
   code's comments first (D-26), in the per-file atlas second
   (B-197), in the guides third — each layer points up at the
   one above it. A disagreement between layers is a bug with a
   slice number, fixed in the release that found it.
3. **Diagrams are Mermaid, lengthy, and checked.** Every
   diagram carries a prose walking tour naming every box and
   arrow (readable by a screen reader, per the alt-text law's
   spirit). The render check below fails a release on a broken
   diagram, by file and line.
4. **User documents are tasks, not topics.** "Photograph a
   flyer and get it on the calendar" beats "The Flyer
   Subsystem". Every user chapter states the steps, what the
   reader should see, what can go wrong, and how to undo it.
5. **Honesty markers.** A capability that belongs to a later
   phase says so at the point of use; a thing the system
   deliberately does not do says so, with the reason.

## The Mermaid gate (B-181 acceptance)

```sh
python scripts/check_mermaid.py            # whole shipped surface
python scripts/check_mermaid.py docs/      # one tree
```

`test.sh` runs the checker before the suite, and
`tests/test_docs_gate.py` pins the same law inside pytest: a
deliberately broken diagram fails the gate **by file** — see
that test's broken-fixture cases. The checker is stdlib-only
(no Node, no network): it proves fence closure, a known
diagram type on the first line, and balanced brackets/quotes —
the breakage class that actually ships. A full JS render is
not claimed.
