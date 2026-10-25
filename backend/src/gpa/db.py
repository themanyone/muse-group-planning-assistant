"""Database chassis (roadmap B-003).

What this file is for: the one place that knows how to open the
database. It builds the SQLAlchemy engine, applies the durability
pragmas on *every* SQLite connection — WAL journal, ``synchronous=
FULL`` (a committed transaction must survive a power cut, not merely
a process crash; roadmap D-28), foreign keys on, and a busy timeout
so concurrent writers queue instead of erroring — and hands out
sessions.

Durability discipline (carried from Samtal, roadmap §6A.5): handlers
and services COMMIT first and only then answer. Nothing in this
codebase returns a response from inside the transaction that was
supposed to make its result visible.
"""

from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    """Base class every model inherits — one metadata for Alembic."""

    pass


class Database:
    """An engine plus its session factory, owned by one app instance.

    Keeping this as an object (instead of module globals) lets tests
    run many isolated apps in one process, each on its own file.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        if url.startswith("sqlite"):
            self._ensure_sqlite_parent_dir(url)
        # check_same_thread=False: FastAPI + the job worker use the
        # engine from several threads; SQLAlchemy's pool keeps each
        # connection safe, SQLite serialises writers via the WAL.
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        )
        if url.startswith("sqlite"):
            self._install_sqlite_pragmas()
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    @staticmethod
    def _ensure_sqlite_parent_dir(url: str) -> None:
        """Create the database file's parent directory if missing.

        A cold tree — the release ZIP unzipped on a clean profile —
        has no ``data/`` directory (the ZIP never carries runtime
        data), and SQLite refuses to open a file whose parent does
        not exist: "unable to open database file". The seed script
        meets the database before storage/outbox creation can make
        the directory as a side effect, so the cold demo died at
        its first step until this landed (found by the B-151
        freeze drill, run 2, 2026-10-25). The one place that knows
        how to open the database is the place that guarantees its
        home exists. In-memory databases have no home to make.
        """
        database = make_url(url).database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)

    def _install_sqlite_pragmas(self) -> None:
        """Apply the durability pragmas to every new connection."""

        @event.listens_for(self.engine, "connect")
        def _set_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            # WAL: readers never block the writer and vice versa.
            cursor.execute("PRAGMA journal_mode=WAL")
            # FULL sync: commit means the data is on disk, even if
            # the power dies a millisecond later (D-28).
            cursor.execute("PRAGMA synchronous=FULL")
            # Foreign keys are off by default in SQLite — turn them
            # on so the relationships in models/ are real law.
            cursor.execute("PRAGMA foreign_keys=ON")
            # Wait up to 5s for a writer instead of failing fast
            # with "database is locked" under normal contention.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    def session(self):
        """Open a new session. Callers close it (or use the dependency)."""
        return self.session_factory()

    def create_all(self) -> None:
        """Create every table — dev/demo convenience and test fixture.

        Production uses Alembic migrations instead; the models are
        the single source both paths build from.
        """
        # Importing models registers every table on Base.metadata.
        from gpa import models  # noqa: F401

        Base.metadata.create_all(self.engine)


def get_db(request: Request):
    """FastAPI dependency: one session per request, always closed.

    It deliberately does NOT commit for the handler — services own
    their transactions, so "commit, then answer" stays explicit
    (roadmap B-003 discipline).
    """
    session = request.app.state.db.session()
    try:
        yield session
    finally:
        session.close()
