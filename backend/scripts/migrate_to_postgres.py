"""SQLite → PostgreSQL data mover (roadmap B-152, decision D-06).

Promotion day, as one command:

    uv run python scripts/migrate_to_postgres.py \
        --source sqlite:///./data/gpa.db \
        --target postgresql+psycopg://gpa:SECRET@db.example.org:5432/gpa

What it does, in order:

1. Refuses nonsense plainly: the source must be SQLite, the
   target must be PostgreSQL, and the target must be EMPTY
   (a promotion target with rows in it is somebody's data —
   the mover never merges, never overwrites).
2. Builds the target schema by running the project's own
   Alembic migrations against it — the same code path a fresh
   PostgreSQL deployment boots with, so the promoted schema
   is the migrated schema, version stamp included.
3. Copies every table in foreign-key order, primary keys and
   all (every primary key in this schema is a string id —
   there are no serial sequences to resynchronise).
4. Verifies the copy table by table: row counts AND a
   content hash (SHA-256 over a canonical rendering of every
   row) must agree between source and target before the mover
   calls the move done. Any mismatch is named, and the exit
   code is 1.

Rollback is the B-146 discipline, unchanged: the source
SQLite database is never written to, and the pre-promotion
backup bundle is the way back. DEPLOYMENT.md tells the whole
story.
"""

import argparse
import hashlib
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select

# Allow running from the repo root without installing first:
# the package lives under src/ (uv run installs it editable,
# so this is only a belt for direct `python scripts/…` use).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gpa import models  # noqa: E402,F401 — registers every table
from gpa.db import Base  # noqa: E402


class MoverError(Exception):
    """A plain-words refusal: the move does not happen."""


def _canonical(value) -> str:
    """Render one value the same way on both engines.

    The verification hash must compare MEANING, not storage:
    SQLite and PostgreSQL hand the same fact back in slightly
    different clothes (a timezone-aware moment stored naive,
    a boolean as 0/1 in a raw read). Every rendering below is
    chosen so the same fact hashes identically on both sides.
    """
    if value is None:
        return "\x00"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value)


def table_digest(session, table) -> tuple[int, str]:
    """(row count, content hash) for one table on one engine.

    Rows are read in primary-key order and rendered column by
    column in the table's own column order, so the digest is
    stable for identical content on any engine.
    """
    pk_columns = list(table.primary_key.columns)
    order = pk_columns if pk_columns else list(table.columns)
    rows = session.execute(select(table).order_by(*order)).all()
    digest = hashlib.sha256()
    for row in rows:
        rendered = "\x1f".join(
            _canonical(value)
            for value in (row._mapping[column.name] for column in table.columns)
        )
        digest.update(rendered.encode("utf-8"))
        digest.update(b"\x1e")
    return len(rows), digest.hexdigest()


def verify(source_session, target_session) -> dict:
    """Compare every table between the two engines.

    Returns {table_name: {source_count, target_count,
    source_hash, target_hash, ok}} — the mover's evidence.
    """
    report = {}
    for table in Base.metadata.sorted_tables:
        source_count, source_hash = table_digest(source_session, table)
        target_count, target_hash = table_digest(target_session, table)
        report[table.name] = {
            "source_count": source_count,
            "target_count": target_count,
            "source_hash": source_hash,
            "target_hash": target_hash,
            "ok": source_count == target_count and source_hash == target_hash,
        }
    return report


def _run_migrations(target_url: str) -> None:
    """Build the target schema with the project's migrations.

    Uses Alembic's own machinery against the target URL, so a
    promoted database carries the same alembic_version stamp
    as any migrated deployment.
    """
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parent.parent
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", target_url)
    # env.py reads the app settings' URL by default; the
    # attribute is the explicit override it honours first —
    # this run migrates the TARGET database, not the
    # process's own (see migrations/env.py).
    config.attributes["database_url"] = target_url
    command.upgrade(config, "head")


def migrate(source_url: str, target_url: str, *, log=print) -> dict:
    """Copy a SQLite GPA database into an empty PostgreSQL one.

    Returns the verification report (see :func:`verify`).
    Raises MoverError with a plain-words reason when the move
    must not happen; a verification mismatch is NOT an
    exception — it is in the report, and main() exits 1.
    """
    if not source_url.startswith("sqlite"):
        raise MoverError(
            f"The source must be a SQLite URL (got {source_url!r}). "
            "This mover is the SQLite → PostgreSQL promotion path."
        )
    if not target_url.startswith("postgresql"):
        raise MoverError(
            "The target must be a PostgreSQL URL "
            f"(got {target_url!r}). Refusing to 'promote' anywhere else."
        )

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)
    try:
        from sqlalchemy.orm import sessionmaker

        # Step 1: the target schema, via the project's migrations.
        _run_migrations(target_url)

        source = sessionmaker(bind=source_engine)()
        target = sessionmaker(bind=target_engine)()
        try:
            # Step 2: the target must be empty — every table.
            for table in Base.metadata.sorted_tables:
                existing = target.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
                if existing:
                    raise MoverError(
                        f"The target already holds {existing} row(s) in "
                        f"{table.name}. A promotion target must be empty; "
                        "the mover never merges or overwrites."
                    )

            # Step 3: copy, parents before children (sorted_tables
            # is foreign-key order), values read as plain dicts.
            for table in Base.metadata.sorted_tables:
                rows = [
                    dict(row)
                    for row in source.execute(select(table)).mappings()
                ]
                if rows:
                    target.execute(table.insert(), rows)
                log(f"  {table.name}: {len(rows)} row(s)")
            target.commit()

            # Step 4: prove it — counts and content hashes.
            return verify(source, target)
        finally:
            source.close()
            target.close()
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote a GPA SQLite database to PostgreSQL (B-152)."
    )
    parser.add_argument("--source", required=True, help="SQLite URL of the live database")
    parser.add_argument("--target", required=True, help="PostgreSQL URL of the EMPTY target")
    args = parser.parse_args()

    print("Migrating schema (Alembic, against the target)…")
    try:
        report = migrate(args.source, args.target)
    except MoverError as error:
        print(f"Refused: {error}")
        return 2

    failures = [name for name, row in report.items() if not row["ok"]]
    total = sum(row["source_count"] for row in report.values())
    print(f"\nVerification — {len(report)} tables, {total} source rows:")
    for name, row in sorted(report.items()):
        mark = "ok " if row["ok"] else "MISMATCH"
        print(
            f"  [{mark}] {name}: {row['source_count']} → "
            f"{row['target_count']} rows, hash {row['source_hash'][:12]}…"
        )
    if failures:
        print(f"\nNOT VERIFIED: {', '.join(failures)} differ after the copy.")
        return 1
    print("\nVerified: every table's count and content hash agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
