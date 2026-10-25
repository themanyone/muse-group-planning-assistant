# Group Planning Assistant — Documentation Set

**Edition:** 1 — authored 2026-10-24, alongside the feature proposal
and the Python backend roadmap (340 slices, B-001 → B-340).
**Status of the system described:** the backend is *designed* in full
and *scheduled* (build window Mon 2026-10-26 → Wed 2026-11-04); these
documents describe the system as designed. Where a capability is a
later phase, the document says so at the point of use.
**Companion documents (outside this folder):**
`../GROUP_PLANNING_ASSISTANT_FEATURE_PROPOSAL_2026-10-24.md` (what the
product does and why) and
`../GROUP_PLANNING_ASSISTANT_PYTHON_BACKEND_ROADMAP_2026-10-24.md`
(how it is built, slice by slice). Inside the shipped product, this
folder lives at `docs/` and travels inside every release ZIP.

## How this set is organised — one set, four readers

| # | Document | Written for | What it answers |
|---|---|---|---|
| 01 | `01-USER-GUIDE.md` | **Group members and organisers** — no technical knowledge assumed | "How do I *use* this, step by step?" |
| 02 | `02-ADMIN-AND-OPERATOR-GUIDE.md` | **Group owners/admins and whoever runs the server** | "How do I run it, connect it, back it up, and stay safe?" |
| 03 | `03-ARCHITECTURE-OVERVIEW.md` | **Any developer joining the project** | "What is the whole system, and how do its parts fit together?" — the long Mermaid atlas of the whole |
| 04 | `04-MODULE-ATLAS.md` | **Backend developers** | "What does each module do, what may it touch, what does it offer the others?" |
| 05 | `05-INTERNAL-ENCRYPTED-API.md` | **Backend developers** | "How do the parts talk to each other — exactly, field by field, encrypted how?" |
| 06 | `06-DATA-MODEL.md` | **Backend developers** | "What data exists, where does it live, what is sealed, how is it related?" — with the full ER diagrams |
| 07 | `07-AI-PIPELINES.md` | **Backend developers, evaluators, judges** | "How does talk become events? How are flyers read? How does the pruning engine work?" |
| 08 | `08-SECURITY-CRYPTOGRAPHY-AND-DURABILITY.md` | **Backend developers, operators, reviewers** | "How is user data protected, and why does a crash lose nothing?" |
| 09 | `09-API-AND-FRONTEND-GUIDE.md` | **Geoffrey and Henry — the frontend crew** | "How do we build screens against this backend?" |
| 10 | `10-CODE-FILE-ATLAS.md` | **Backend developers and maintainers** | "What is *every single code file* for, in detail?" |
| 11 | `11-TESTING-DEPLOYMENT-AND-RELEASE.md` | **Backend developers and operators** | "How is quality proven, and how does a release ZIP get made and installed?" |
| 12 | `12-GLOSSARY-DECISIONS-AND-FAQ.md` | **Everyone** | "What do these words mean, why was it built this way, and what do people always ask?" |
| 13 | `13-MCP-AND-AGENT-GUIDE.md` | **AI agents and whoever seats them** | "How does an AI move in and work — through MCP, the API, or a terminal — as an equal?" |
| 14 | `14-TERMINAL-APP-GUIDE.md` | **Humans and AIs who choose text** | "How do I do everything from a terminal — TUI, CLI, and agent machine mode?" |
| 15 | `15-CALENDAR-AND-OFFICE-INTEROPERABILITY.md` | **Everyone, evaluators** | "Which calendar and office apps does it truly work with — proven how, app by app?" |
| 16 | `16-EMBEDDING-AND-HOST-APPS.md` | **Samtal and any app that wants these features** | "How does this capability get built into another app — modes, identity bridge, widgets, SDKs, plugins?" |
| 17 | `17-USER-FRIENDLINESS-HELP-AND-DOCTOR.md` | **Everyone** | "How is it kept really user friendly? How does the massive built-in help work? What is the Doctor?" |

## Documentation laws (roadmap D-26 / D-34 / §6A.8)

1. **One truth, three depths.** A fact lives in the code comments
   first, in the per-file atlas (Doc 10) second, in the guides third.
   Each layer points up at the one above it.
2. **Diagrams are Mermaid, lengthy, and rendered-checked.** A
   diagram that does not parse blocks a release, like a failing test.
3. **Every diagram has a walking tour.** The prose under each
   diagram names every box and every arrow, so the diagram is
   readable as text — including by a screen reader.
4. **User documents are tasks, not topics.** "Photograph a flyer"
   beats "The Flyer Subsystem".
5. **Honesty markers matter.** Anything described here that is a
   later phase says so. Anything the system deliberately does *not*
   do (for example: member-to-member end-to-end encryption, per-person
   attendance rankings) says so, with the reason.
6. **Equal principals, many doors.** Humans and AI agents are equal
   users of this system (roadmap D-35). These documents never treat
   the agent road (API, MCP, terminal machine mode) as a footnote to
   the human road (web, TUI) — each is documented in full, because
   each is a real way in: web frontends the team builds, our terminal
   app, the REST API, and MCP.
7. **Original words for original code — and no licence assumptions.**
   Everything in this set, like the code it describes, is the team's
   own work for this project (roadmap D-46). The project's licence
   is **undecided**, and no document here assumes one in either
   direction. Business and licence decisions rest with the team's
   business side — Geoffrey and Henry; the builders' job is to keep
   every option open until they decide.

## Conventions used across the set

- **Mermaid** diagrams use `flowchart`, `sequenceDiagram`,
  `erDiagram`, and `stateDiagram-v2`. Node ids are short; labels are
  plain words.
- **Roles** always mean: owner > admin > organiser > member > guest,
  plus *agent seat* (an AI participant acting through an API key).
- **"Sealed"** means encrypted at rest with the envelope scheme of
  Doc 08. **"Internal API"** always means the encrypted internal API
  of Doc 05. **"The queue"** always means the human review queue.
- Slice ids (`B-123`) refer to the backend roadmap; decision ids
  (`D-28`) refer to its decision table, explained in Doc 12.
