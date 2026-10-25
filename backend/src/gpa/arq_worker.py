"""External-queue worker entry point (roadmap B-157).

Run as ``gpa-arq-worker`` (or ``python -m gpa.arq_worker``) in a
deployment that selected the arq backend (GPA_JOB_QUEUE=arq):
the twin of ``gpa-worker`` for the database chassis. It hands
services/jobqueue.py's WorkerSettings to arq's worker; the jobs
it runs dispatch to the SAME handler registry (jobs.HANDLERS)
as every other worker in this project — the transport is the
only thing that changed.

arq is an OPTIONAL install extra (``uv sync --extra queue``):
on a deployment without it, this entry refuses in plain words
instead of dying on an import traceback.
"""

from gpa.config import get_settings


def main() -> None:
    """Run the arq worker until stopped."""
    try:
        from arq.worker import create_worker
    except ImportError as err:
        raise SystemExit(
            "The arq job queue is not installed in this "
            "environment — install the queue extra "
            "(uv sync --extra queue) or run the database "
            "worker (gpa-worker) instead."
        ) from err
    from gpa.services.jobqueue import worker_settings

    settings = get_settings()
    if not settings.redis_url:
        raise SystemExit(
            "GPA_REDIS_URL is not set — the arq worker needs the "
            "deployment's Redis address (for example "
            "redis://127.0.0.1:6379/0)."
        )
    worker = create_worker(worker_settings(settings))
    worker.run()


if __name__ == "__main__":
    main()
