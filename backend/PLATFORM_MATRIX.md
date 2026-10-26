# Platform matrix — Group Planning Assistant backend (B-177)

The suite must mean the same thing on Windows, macOS, and Linux
(roadmap §6A.2). This page records, honestly, what has actually
been run where — a named, waived difference is allowed by the
slice; an untested assumption is not.

## The columns

| Column | State | Evidence |
| --- | --- | --- |
| Linux (this builder) | **GREEN** | Full suite incl. `tests/test_platform_matrix.py` run at the B-177 landing; totals in TEST_REPORT's Phase 17 addendum and PROJECT_MEMORY_MAP. |
| Windows | **CI-defined, NOT RUN here** | `.github/workflows/test.yml` runs the identical suite on `windows-latest`. No Windows machine is reachable from this environment, and the roadmap's manual real-Windows run before ZIP-Final remains **NOT RUN** — waived by name here, not assumed. |
| macOS | **CI-defined, NOT RUN here** | Same workflow, `macos-latest`. No macOS machine is reachable from this environment — waived by name, as above. |

## What the fixtures pin (every OS that runs them)

- **Portability audit** (`scripts/platform_audit.py`, in the
  suite): pathlib-only path building, explicit `encoding=` on
  every text read/write, no `sys.platform`/`os.name` branching
  in domain code. Surviving URL joins are pinned in the audit
  with reasons; a new hit fails until fixed or pinned.
- **Encoding**: BOM + CRLF + non-ASCII fixtures round-trip
  through the explicit-encoding roads; storage serves bytes
  untouched. Locale-default decoding is banned by the audit.
- **Timezone/DST**: truth comes from `zoneinfo`; the lockfile
  already carries the `tzdata` package for Windows
  (`sys_platform == 'win32'`), where the OS ships no zone
  database. Spring-forward fixtures assert the one-hour offset
  step on any OS.
- **Transport (B-163)**: loopback TCP + mTLS runs everywhere;
  the Unix-socket door is probed live where the OS offers
  `AF_UNIX` and skipped with its reason named elsewhere.

## Named differences (waived, with the defence)

1. **Windows named pipes** — the B-163 wire form's pipe leg is
   unproven (Linux builder; recorded at B-163). The framing and
   wire form behind the pipe are the same classes the socket
   doors prove. Until a Windows run proves the pipe, Windows
   deployments use the loopback mTLS door.
2. **Windows MAX_PATH (260)** — storage paths are short by
   construction: content-addressed keys are lowercase sha256
   hex under a two-character fan-out; the matrix test asserts
   the code-controlled depth stays under 80 characters.
3. **Case-insensitive filesystems** (Windows, default macOS) —
   nothing the code names differs only by case: storage keys
   are lowercase hex of content, group slugs are lowercased at
   creation (`services/groups.py::slugify`). The matrix test
   probes the running filesystem's behaviour instead of
   assuming it.
4. **File locking** — the code takes no OS file locks: writes
   are atomic renames (mkstemp + `os.replace`, B-007/B-171) and
   SQLite WAL carries cross-process truth, on all three OSes.
