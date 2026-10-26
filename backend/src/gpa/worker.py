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
from gpa.services import messenger_transport
from gpa.services import transcribe as _transcribe  # noqa: F401 — registers its handler on import
from gpa.services import extraction as _extraction  # noqa: F401 — registers its handler on import
from gpa.services import notifications as _notifications  # noqa: F401 — registers the notify handler on import (Phase 8 outbox; it superseded the rsvps stub)
from gpa.services import digests as _digests  # noqa: F401 — registers the digest_run handler on import
from gpa.services import vision as _vision  # noqa: F401 — registers the vision handler on import
from gpa.services import outbound as _outbound  # noqa: F401 — registers the webhook_deliver handler on import (Phase 11)
from gpa.services import reconcile as _reconcile  # noqa: F401 — registers the reconcile handler on import (Phase 12)
from gpa.services import retention as _retention  # noqa: F401 — registers the retention_sweep handler on import (Phase 13)
from gpa.services import keymgmt as _keymgmt  # noqa: F401 — registers the crypto.rotate_master handler on import (Phase 17)
from gpa.services import repair as _repair  # noqa: F401 — registers the data_repair handler on import (B-173)
from gpa.services import resilience as _resilience  # noqa: F401 — B-173 breakers start closed in a fresh process
from gpa.storage import set_storage


def main() -> None:
    """Poll the queue forever, processing jobs as they come due."""
    settings = get_settings()
    # The internal API's audit hook (B-165), as in the API
    # process: the worker's internal calls keep the same story.
    from gpa.internal import auditing

    auditing.install(settings=settings)
    # The job chassis' sealing settings (B-166).
    from gpa.services import jobs as _jobs

    _jobs.set_settings(settings)
    # B-173: breakers start closed in a fresh process.
    _resilience.reset_all()
    # The field-encryption service's settings (B-167).
    from gpa.crypto import fields as _fields

    _fields.set_settings(settings)
    ai_client.set_client(ai_client.build_client(settings))
    mailer.set_transport(mailer.build_transport(settings))
    messenger_transport.set_transport(messenger_transport.build_transport(settings))
    _outbound.set_settings(settings)
    from gpa.services import audit as _audit

    _audit.set_chain_secret(settings.secret_key)
    from gpa.storage.encrypted import build_storage

    set_storage(build_storage(settings))
    db = Database(settings.database_url)
    db.create_all()
    # B-172: startup recovery — assess and repair before the
    # first job is touched; a "refuse" verdict stops the
    # worker with the repair named rather than working on
    # damaged ground.
    from gpa.services import recovery as _recovery

    _session = db.session()
    try:
        report = _recovery.assess_and_repair(_session, settings)
    finally:
        _session.close()
    for line in report["actions"] + report["notes"]:
        print(f"recovery: {line}")
    if report["verdict"] == "refuse":
        for problem in report["problems"]:
            print(f"recovery REFUSES to start: {problem}")
        raise SystemExit(2)
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
