"""Background worker entry point (roadmap B-008).

Run standalone as ``gpa-worker`` (or ``python -m gpa.worker``) when
an operator wants the job loop in its own process — the "service in
the background" posture of Phase 20/26. The API process can also
drain the same queue; claim-by-update (services/jobs.py) makes the
two safe to run side by side.
"""

import time
import uuid

from gpa.ai import client as ai_client
from gpa.config import get_settings
from gpa.db import Database
from gpa.services import jobs as job_service
from gpa.services import mailer
from gpa.services import transcribe as _transcribe  # noqa: F401 — registers its handler on import
from gpa.services import extraction as _extraction  # noqa: F401 — registers its handler on import
from gpa.services import notifications as _notifications  # noqa: F401 — registers the notify handler on import (Phase 8 outbox; it superseded the rsvps stub)
from gpa.services import digests as _digests  # noqa: F401 — registers the digest_run handler on import
from gpa.services import vision as _vision  # noqa: F401 — registers the vision handler on import
from gpa.storage import LocalStorage, set_storage


def main() -> None:
    """Poll the queue forever, processing jobs as they come due."""
    settings = get_settings()
    ai_client.set_client(ai_client.build_client(settings))
    mailer.set_transport(mailer.build_transport(settings))
    set_storage(LocalStorage(settings.storage_dir))
    db = Database(settings.database_url)
    db.create_all()
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    session = db.session()
    try:
        while True:
            if not job_service.process_one(session, settings, worker_id):
                time.sleep(1.0)  # Nothing due — idle, don't spin.
    finally:
        session.close()


if __name__ == "__main__":
    main()
