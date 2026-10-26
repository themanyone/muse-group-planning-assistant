"""B-181 documentation gate: index present, diagrams sound.

The acceptance B-181 names: a deliberately broken diagram fails
the gate, by file. These tests pin that against the real checker
(scripts/check_mermaid.py) — the same module test.sh invokes —
plus the standing law that every Mermaid diagram already in the
shipped documentation surface parses.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_mermaid", ROOT / "scripts" / "check_mermaid.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def test_docs_index_orders_every_reader():
    """The B-181 index exists and orders readers user-first."""
    index = ROOT / "docs" / "README.md"
    assert index.exists()
    text = index.read_text(encoding="utf-8")
    # Reader order in section 6A.8: user -> admin -> frontend ->
    # backend -> operator surfaces named in the index body.
    positions = [
        text.index("People who use the app"),
        text.index("Group admins"),
        text.index("Frontend developers"),
        text.index("Backend developers"),
    ]
    assert positions == sorted(positions)
    # Every Phase 18 chapter slice is reachable from the index.
    for slice_id in ("B-182", "B-188", "B-189", "B-196", "B-197", "B-199"):
        assert slice_id in text


def test_shipped_diagrams_all_parse():
    """Every mermaid block in docs/, submission/ and root .md is sound."""
    failures: list[str] = []
    for target in [ROOT / "docs", ROOT / "submission", *sorted(ROOT.glob("*.md"))]:
        for md in checker.iter_markdown([target]):
            failures.extend(str(f) for f in checker.check_file(md))
    assert failures == [], "\n".join(failures)


def test_broken_diagram_fails_by_file(tmp_path):
    """Acceptance: an unclosed bracket fails, naming its file."""
    bad = tmp_path / "broken.md"
    bad.write_text(
        "# Fixture\n\n```mermaid\ngraph TD\n  A[Start --> B\n```\n",
        encoding="utf-8",
    )
    failures = checker.check_file(bad)
    assert failures, "the broken fixture must fail the gate"
    assert str(bad) in failures[0]


def test_unknown_diagram_type_fails_by_file(tmp_path):
    """A first line Mermaid cannot render is a failure, by file."""
    bad = tmp_path / "not_a_diagram.md"
    bad.write_text(
        "```mermaid\nbananaDiagram\n  A --> B\n```\n", encoding="utf-8"
    )
    failures = checker.check_file(bad)
    assert failures and str(bad) in failures[0]


def test_unterminated_fence_fails_by_file(tmp_path):
    """A fence that never closes is a failure, by file."""
    bad = tmp_path / "open_fence.md"
    bad.write_text("```mermaid\ngraph TD\n  A --> B\n", encoding="utf-8")
    failures = checker.check_file(bad)
    assert failures and str(bad) in failures[0]


def test_user_guide_1_covers_its_roadmap_topics():
    """B-182: the start + groups chapter exists and covers every
    topic its roadmap line names — account, sign-in incl. MFA,
    creating a group, inviting, roles, group settings,
    notification preferences, quiet hours — as tasks."""
    chapter = ROOT / "docs" / "01-user-guide-start-and-groups.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "register", "magic link", "two-factor", "create a group",
        "invite", "roles", "group settings", "notification preferences",
        "quiet hours", "last-owner",
    ):
        assert topic in text, f"B-182 chapter is missing its topic: {topic}"
    # Task-shaped, per the B-181 rules: a completable checklist.
    assert "first success" in text and "- [ ]" in text
    # Honesty markers the code demands: no MFA recovery codes
    # exist in this build, and the Messenger driver is unavailable.
    assert "no mfa recovery codes" in text
    assert "driver_unavailable" in text
    # The index links the built chapter.
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "01-user-guide-start-and-groups.md" in index


def test_user_guide_2_covers_every_way_in():
    """B-183: the sources chapter covers every way in its
    roadmap line names, the permission record in plain words,
    and what revoking a source does to its data."""
    chapter = ROOT / "docs" / "02-user-guide-sources.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "paste a chat", "upload a file", "newsletter", "flyer",
        "noticeboard", "paste a link", "forward an email",
        "voice note", "live chat", "granted_by", "granted_scope",
        "source_deleted_at", "30 days",
    ):
        assert topic in text, f"B-183 chapter is missing its topic: {topic}"
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "02-user-guide-sources.md" in index


def test_user_guide_3_covers_the_trust_workflow():
    """B-184: the review-queue chapter covers reading a
    candidate, confidence and the flag vocabulary, provenance
    quotes and flyer regions, editing, approve / reject /
    merge, change candidates, and contradiction resolution."""
    chapter = ROOT / "docs" / "03-user-guide-review-queue.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "candidate", "confidence", "provenance", "supported: false",
        "region", "low_confidence", "ambiguous_date", "unresolved_date",
        "conflicting", "possible_duplicate", "edited", "approve",
        "reject", "merge", "event_change", "change_target_unknown",
        "contradiction", "seat_cannot_approve", "review_min_role",
        "approvals_required", "source_deleted_at",
    ):
        assert topic in text, f"B-184 chapter is missing its topic: {topic}"
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "03-user-guide-review-queue.md" in index


def test_user_guide_4_covers_calendar_and_events():
    """B-185: the calendar chapter covers the views, agenda,
    the tonight view, event pages, discussion, RSVP and the
    waitlist, .ics, Google add, the calendar feed, print."""
    chapter = ROOT / "docs" / "04-user-guide-calendar-and-events.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "agenda", "month", "tonight", "15:00", "discussion",
        "rsvp", "waitlist", "next-in-line", ".ics",
        "google", "calendar feed", "print", "conflicts",
        "if-match", "cancel", "clone", "recurrence",
    ):
        assert topic in text, f"B-185 chapter is missing its topic: {topic}"
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "04-user-guide-calendar-and-events.md" in index


def test_user_guide_5_covers_planning():
    """B-186: the planning chapter covers tasks and the owner
    law, the decisions log, open questions and gaps, polls,
    checklists and roles, supplies, budget-lite, the
    availability finder, and templates."""
    chapter = ROOT / "docs" / "05-user-guide-planning.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "tasks", "suggested owner", "never assigns a silent victim",
        "decisions", "supersede", "open question", "gaps",
        "polls", "apply the winner", "checklists", "role slots",
        "supplies", "integer cents", "availability",
        "templates", "club night",
    ):
        assert topic in text, f"B-186 chapter is missing its topic: {topic}"
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "05-user-guide-planning.md" in index


def test_user_guide_6_covers_reminders_digests_memory():
    """B-187: the reminders/digests/memory chapter covers the
    reminder rules and their defaults, change alerts, the
    weekly digest, asking the group memory with verified
    citations and its honest refusal, briefings and recaps,
    and what the AI may and may not do."""
    chapter = ROOT / "docs" / "06-user-guide-reminders-digests-memory.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "reminder rules", "7 days", "morning of", "quiet hours hold",
        "urgent", "verbatim", "wording_source", "change alerts",
        "reannounce-drafts", "weekly digest", "digest_weekday",
        "digest/run", "approved history only", "not embedding rag",
        "budget_stopped", "i couldn't verify this", "briefing",
        "recap", "memory/runs", "may not",
    ):
        assert topic in text, f"B-187 chapter is missing its topic: {topic}"
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "06-user-guide-reminders-digests-memory.md" in index


def test_admin_guide_8_covers_dangerous_buttons_consequence_first():
    """B-188: the admin + operator guide covers group
    administration, agent seats, integrations (Facebook,
    Google, Samtal, webhooks), the AI budget, audit reading,
    data export, group erasure, and member data requests —
    dangerous buttons with consequences before instructions."""
    chapter = ROOT / "docs" / "07-admin-and-operator-guide.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "settings pack", "agent seats", "shown once",
        "seat_cannot_approve", "integrations", "facebook",
        "google", "samtal", "webhooks", "health-board",
        "reconcile", "ai_daily_budget_micros", "audit/verify",
        "users/me/data", "export", "erase", "exact name",
        "irreversible", "consequence first",
    ):
        assert topic in text, f"B-188 chapter is missing its topic: {topic}"
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "07-admin-and-operator-guide.md" in index


def test_architecture_pack_covers_its_six_diagrams():
    """B-189: the architecture pack carries the six diagrams
    its roadmap line names — system context, process view,
    module map, request lifecycle, job lifecycle, deployment
    topology — each with a prose walking tour, verified
    against the module map's own names and the job chassis'
    real states."""
    chapter = ROOT / "docs" / "08-architecture-diagram-pack.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "system context", "process view", "module map",
        "request lifecycle", "job lifecycle",
        "deployment topology", "walking tour",
        "gpa-worker", "gpa-arq-worker", "claim",
        "backoff", "dead", "commit-before-answer",
        "deps.py", "sqlite", "postgresql",
        "no router imports a model", "project_memory_map",
    ):
        assert topic in text, f"B-189 chapter is missing its topic: {topic}"
    # Six diagrams, no fewer: the roadmap line names six.
    assert text.count("```mermaid") >= 6
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "08-architecture-diagram-pack.md" in index


def test_module_atlas_covers_every_module():
    """B-190: the module atlas carries one chapter per module
    in the B-161 map — each with its job, public surface,
    tables owned, internal operations offered and consumed,
    invariants, failure modes, and its diagram — verified
    against the registry's own operation names and the
    models' own table names."""
    chapter = ROOT / "docs" / "09-module-atlas.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "identity", "groups", "sources", "extraction",
        "events", "calendar", "planning", "nonprofit",
        "notifications", "integrations", "memory", "truth",
        "jobs", "audit", "crypto", "internal", "storage",
        "platform", "privacy", "demo", "composition",
        "tables owned", "internal operations",
        "offered", "consumed", "invariants",
        "failure modes", "public surface",
        "candidates.create", "events.publish",
        "notify.request", "notify.rsvp_promotion",
        "jobs.enqueue", "ai.submit_job", "integrations.emit",
        "no single `api.py`", "seat_cannot_approve",
        "commit-before-answer",
    ):
        assert topic in text, f"B-190 chapter is missing its topic: {topic}"
    # One diagram per module chapter: 22 chapters.
    assert text.count("```mermaid") >= 22
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "09-module-atlas.md" in index


def test_internal_api_document_covers_the_protocol():
    """B-191: the internal encrypted API chapter specifies the
    §6A.4 protocol in full — envelope field by field, signing
    and encryption, transport profiles, identity and scope
    registry, replay defence, key rotation — with sequence
    diagrams for a publish, an AI extraction, and a rejected
    impersonation attempt."""
    chapter = ROOT / "docs" / "10-internal-encrypted-api.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "operation", "caller", "scope", "payload", "nonce",
        "timestamp", "idempotency_key", "signature",
        "aes-256-gcm", "ed25519", "hmac-sha256", "canonical",
        "candidates.create", "events.publish", "notify.request",
        "notify.rsvp_promotion", "jobs.enqueue", "ai.submit_job",
        "integrations.emit",
        "internal_unknown_operation", "internal_scope_denied",
        "internal_bad_payload", "internal_tampered",
        "internal_bad_signature", "internal_replayed",
        "internal_bad_key",
        "replay", "5 minutes", "key rotation", "pub.prev",
        "unix domain socket", "mutual tls", "named pipe",
        "in-process", "never the payload", "not run",
    ):
        assert topic in text, f"B-191 chapter is missing its topic: {topic}"
    # The three sequence diagrams its roadmap line names.
    assert text.count("sequencediagram") >= 3
    assert "impersonation" in text
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "10-internal-encrypted-api.md" in index


def test_data_model_document_covers_every_table():
    """B-192: the data-model chapter documents every entity —
    the model <-> document diff its roadmap line orders. Every
    __tablename__ parsed out of the live model files must
    appear in the chapter, the six ER packs (identity,
    sources, events, planning, comms, system) must each carry
    an erDiagram, and the sealed / hashed inventories must
    name the fields the code actually seals and hashes."""
    import re

    chapter = ROOT / "docs" / "11-data-model-and-er-pack.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8")
    lowered = text.lower()
    # The diff itself: model tables, parsed stdlib-only from
    # the model files (the suite must not need the app import
    # path to prove a documentation fact).
    tables: set[str] = set()
    for model_file in (ROOT / "src" / "gpa" / "models").glob("*.py"):
        tables.update(
            re.findall(
                r'__tablename__\s*=\s*"([^"]+)"',
                model_file.read_text(encoding="utf-8"),
            )
        )
    assert len(tables) >= 60, "the model parse itself must find the store"
    missing = sorted(t for t in tables if f"`{t}`" not in text)
    assert missing == [], f"tables missing from the B-192 chapter: {missing}"
    for topic in (
        "erdiagram", "identity", "sources", "events", "planning",
        "comms", "system", "sealed", "enc:v1:", "chunk.text",
        "chunk.sender", "provenance.quote", "event_link.quote",
        "flyer.transcript", "group_keys", "wrapped_key",
        "config_sealed", "secret_sealed", "totp_secret",
        "password_hash", "argon2id", "token_hash", "key_hash",
        "alembic", "0001", "0021", "checker", "base.metadata",
        "group_id", "varchar(36)", "cost_micros", "amount_cents",
        "tombstones", "audit_log", "row_hash",
    ):
        assert topic in lowered, f"B-192 chapter is missing its topic: {topic}"
    # Six ER packs, one erDiagram each, no fewer.
    assert lowered.count("erdiagram") >= 6
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "11-data-model-and-er-pack.md" in index


def test_ai_pipeline_document_covers_its_pipelines():
    """B-193: the AI pipeline chapter covers extraction, flyer
    reading, board segmentation, de-duplication, change
    detection, and the context-pruning engine — with diagrams,
    the prompt-versioning rules, the evaluation method, and
    worked examples traced through fixture data."""
    chapter = ROOT / "docs" / "12-ai-pipeline.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "extraction", "flyer reading", "board segmentation",
        "de-duplication", "change detection", "context-pruning",
        "not embedding rag", "quote verification", "supported: false",
        "date resolver", "repair retry", "coverage",
        "40 chunks", "12,000 characters",
        "samsegmenter", "visionboxsegmenter", "segmenterunavailable",
        "valid_region", "average-hash", "6 bits",
        "possible_duplicate", "duplicate_of", "match_threshold",
        "change_target_unknown", "extraction_changes",
        "prune", "prune_reconsider", "budget_stopped",
        "max_passes_ceiling", "approved history only",
        "i couldn't verify this",
        "manifest.json", "sha-256", "flyer_reading",
        "precision", "recall", "0.02", "0.8125",
        "fixturemisserror", "worked example",
        "golden-club-night", "harvest supper", "12 candidates",
        "spark", "wonder", "no qr decoder",
    ):
        assert topic in text, f"B-193 chapter is missing its topic: {topic}"
    # Flowcharts and sequence diagrams, per its roadmap line.
    assert text.count("```mermaid") >= 5
    assert "sequencediagram" in text
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "12-ai-pipeline.md" in index


def test_erdiagram_crows_foot_is_not_a_broken_brace(tmp_path):
    """B-192 checker repair: erDiagram cardinality spends
    braces as glyphs (o{ is a 'many' foot, not a block), so a
    sound ER diagram passes — while an entity block whose
    closing brace is truly dropped still fails, by file."""
    good = tmp_path / "er_good.md"
    good.write_text(
        "```mermaid\nerDiagram\n"
        '  A ||--o{ B : "has many"\n'
        "  A {\n    string id PK\n  }\n```\n",
        encoding="utf-8",
    )
    assert checker.check_file(good) == []
    bad = tmp_path / "er_bad.md"
    bad.write_text(
        "```mermaid\nerDiagram\n"
        '  A ||--o{ B : "has many"\n'
        "  A {\n    string id PK\n```\n",
        encoding="utf-8",
    )
    failures = checker.check_file(bad)
    assert failures and str(bad) in failures[0]


def test_sound_diagram_passes(tmp_path):
    """The gate is not a blanket refusal: a sound diagram passes."""
    good = tmp_path / "sound.md"
    good.write_text(
        "```mermaid\ngraph TD\n  A[Start] --> B{Decide}\n```\n",
        encoding="utf-8",
    )
    assert checker.check_file(good) == []


def test_security_document_covers_threat_crypto_account_audit():
    """B-194: the security + cryptography chapter covers the
    threat model in prose, the key hierarchy, encryption at
    rest and inside, account security, and the audit chain,
    including the honest-boundaries chapter (no member-to-
    member E2E claimed)."""
    chapter = ROOT / "docs" / "13-security-and-cryptography.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "threat model", "case #1", "argon2id", "enc:v1:",
        "aes-256-gcm", "ed25519", "hmac-sha256", "key hierarchy",
        "master key", "group_keys", "wrapped_key",
        "recovery escrow", "scrypt", "gpae1", "blind index",
        "audit chain", "row_hash", "prev_hash", "genesis",
        "seat_cannot_approve", "no member-to-member",
        "end-to-end", "not claimed", "refresh", "totp",
        "mfa_challenge", "account_locked", "shown once",
        "token_hash", "key_hash", "ssrf", "rate",
        "redacted", "internal_tampered", "totp_secret",
        "config_sealed", "secret_sealed",
    ):
        assert topic in text, f"B-194 chapter is missing its topic: {topic}"
    # Key hierarchy + at-rest + account sequence diagrams.
    assert text.count("```mermaid") >= 4
    assert "sequencediagram" in text
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "13-security-and-cryptography.md" in index


def test_durability_document_covers_recovery_machinery():
    """B-195: the durability + recovery chapter covers the
    §6A.5 machinery — transaction discipline, autosave,
    resumable upload, boot recovery with the double-verdict
    guard, watchdog, backup/restore, and the chaos-harness
    method — with diagrams and drills."""
    chapter = ROOT / "docs" / "14-durability-and-recovery.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "transaction discipline", "commit-before-answer",
        "uncommitted_writes", "writetracker",
        "journal_mode=wal", "synchronous=full",
        "foreign_keys=on", "busy_timeout=5000",
        "autosave", "draft_conflict", "base_version",
        "conflict copy", "resumable upload", "offset law",
        "sha-256", "parts_missing", "session stays open",
        "assess_and_repair", "double-verdict guard",
        "serve", "repaired", "refuse", "900",
        "wal checkpoint", "stuck jobs", "orphan upload parts",
        "watchdog", "no separate", "stuck_running",
        "requeue_stuck_jobs", "online backup", "manifest.json",
        "verify-then-place", "restore_drill",
        "chaos", "invariant", "sigkill", "100/100",
        "chaos_report.md", "diskfull", "readonly",
        "integrity_check", "foreign_key_check",
    ):
        assert topic in text, f"B-195 chapter is missing its topic: {topic}"
    # Foundation, tracker, upload-state and recovery diagrams
    # plus the chaos flowchart: at least four, incl. a sequence.
    assert text.count("```mermaid") >= 4
    assert "sequencediagram" in text
    assert "statediagram" in text
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "14-durability-and-recovery.md" in index


def test_file_atlas_covers_every_code_file():
    """B-197: the per-file atlas documents EVERY code file —
    the tree <-> atlas diff its roadmap line orders. Every
    Python file under src/, migrations/, scripts/, tests/ has
    an entry with all eight fields (purpose, module, public
    symbols, dependencies, allowed/forbidden, data, failure
    behaviour, tests, creating slice), and no entry names a
    file that no longer exists."""
    import re

    chapter = ROOT / "docs" / "16-per-file-atlas.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8")
    lowered = text.lower()
    for topic in (
        "build_file_atlas", "checker", "layer law",
        "walking tour", "scope honesty",
    ):
        assert topic in lowered, f"B-197 chapter is missing its topic: {topic}"
    entries = re.findall(r"^### `([^`]+\.py)`", text, flags=re.M)
    assert len(entries) == len(set(entries)), "atlas entries must be unique"
    live = {
        str(p.relative_to(ROOT))
        for base in ("src", "migrations", "scripts", "tests")
        for p in (ROOT / base).rglob("*.py")
        if "__pycache__" not in p.parts
    }
    missing = sorted(live - set(entries))
    stale = sorted(set(entries) - live)
    assert missing == [], f"code files missing from the B-197 atlas: {missing}"
    assert stale == [], f"atlas entries for files that are gone: {stale}"
    # Every entry carries the eight roadmap fields.
    blocks = re.split(r"^### `[^`]+\.py`", text, flags=re.M)[1:]
    assert len(blocks) == len(entries)
    for field in (
        "**Purpose:**", "**Module:**", "**Public symbols:**",
        "**Dependencies (imports):**", "**Allowed / forbidden:**",
        "**Data:**", "**Failure behaviour:**",
        "**Tests that name it:**", "**Created by:**",
    ):
        bad = [e for e, b in zip(entries, blocks) if field not in b]
        assert bad == [], f"atlas entries missing {field}: {bad[:5]}"
    assert text.count("```mermaid") >= 1
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "16-per-file-atlas.md" in index


def test_testing_quality_document_covers_every_suite():
    """B-198: the testing + quality chapter explains every
    suite — what it guards, how to run it, how to read the
    TEST_REPORT that ships in the ZIP, and how a new failure
    becomes a new fixture. Every test file in the tree is
    named in its inventory."""
    chapter = ROOT / "docs" / "17-testing-and-quality.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8")
    lowered = text.lower()
    for topic in (
        "test.sh", "mermaid gate", "one-page report",
        "basetemp", "test_report.md", "reconciliation",
        "superseded", "deliberately imperfect", "not run",
        "becomes a new fixture", "reproduce it alone",
        "never commit red", "run's own log",
        "comment gate", "architecture", "platform audit",
        "golden", "phase gate",
    ):
        assert topic in lowered, f"B-198 chapter is missing its topic: {topic}"
    missing = sorted(
        p.name for p in (ROOT / "tests").glob("test_*.py")
        if f"`tests/{p.name}`" not in text
    )
    assert missing == [], f"test files missing from B-198: {missing}"
    assert text.count("```mermaid") >= 1
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "17-testing-and-quality.md" in index


def test_deployment_release_document_covers_operations():
    """B-199: the deployment + release chapter covers native
    install on all three OSes (with the supervision honesty
    marker), service supervision, TLS, backups in operations,
    upgrades and rollback, and the ZIP's anatomy with its
    signature check."""
    chapter = ROOT / "docs" / "18-deployment-and-release.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8")
    lowered = text.lower()
    for topic in (
        "install.sh", "no docker", "0640", "systemd",
        "run.ps1", "macos", "not run", "gpa-worker",
        "gpa-backup.timer", "03:15", "tls 1.3", "hsts",
        "caddy", "nginx", "raw body", "verify-then-place",
        "upgrade.py", ".previous-", "rollback",
        "release-manifest.json", "sha256sums.txt",
        "sbom.spdx.json", "ed25519", "fingerprint",
        "release_signing.py verify", ".env",
        "freeze drill", "known limits",
    ):
        assert topic in lowered, f"B-199 chapter is missing its topic: {topic}"
    assert text.count("```mermaid") >= 1
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "18-deployment-and-release.md" in index


def test_api_frontend_guide_covers_its_roadmap_topics():
    """B-196: the API + frontend chapter covers auth flows as
    sequence diagrams, pagination, every error code in a
    table, the SSE events, idempotency told honestly, the
    Bruno walkthrough, and the mock-mode guide."""
    chapter = ROOT / "docs" / "15-api-and-frontend-guide.md"
    assert chapter.exists()
    text = chapter.read_text(encoding="utf-8").lower()
    for topic in (
        "magic link", "mfa_challenge", "refresh", "family is revoked",
        "x-api-key", "authorization: bearer", "seat_cannot_approve",
        "pagination", "next_cursor", "opaque", "limit",
        "application/problem+json", "branch on **`code`**",
        "snapshot", "job.progress", "comment.created",
        "notification.created", "polled",
        "idempotency-key", "no `idempotency-key` header store",
        "bruno", "b-010 skeleton",
        "mock", "fixture", "gpa_ai_driver", "demo-password-123",
        "etag", "if-match", "retry-after", "raw request bodies",
        "202 paths, 230 schemas", "integer cents",
    ):
        assert topic in text, f"B-196 chapter is missing its topic: {topic}"
    # Every code in the live registry appears in the table.
    import re

    errors_src = (ROOT / "src" / "gpa" / "errors.py").read_text(encoding="utf-8")
    registry = errors_src.split("ERROR_CODES", 1)[1].split("}", 1)[0]
    codes = re.findall(r'"([a-z_]+)": \(', registry)
    assert len(codes) >= 30, "registry parse must find the code table"
    for code in codes:
        assert f"`{code}`" in text, f"B-196 error table is missing code: {code}"
    # Auth as sequence diagrams: at least three, all sequences.
    assert text.count("```mermaid") >= 3
    assert text.count("sequencediagram") >= 3
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "15-api-and-frontend-guide.md" in index
