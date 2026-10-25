"""A shared end-to-end world for Phase 3 tests: the Doc 07
golden chat pasted into a real group, extracted by the fixture
driver through the real job queue.

The fixture recordings are keyed by the prompts the pipeline
renders from the DATABASE chunks (the paste parser's own output),
so the whole spine — ingest, job, pipeline, queue — is exercised
exactly as production runs it.
"""

from gpa.ai import client as ai_client
from gpa.ai import promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import build_prompt
from gpa.services import jobs as job_service
from gpa.services import sources as source_service
from conftest import bearer, make_group, register
from test_extraction import DECISION_ANSWER, EVENT_ANSWER, QUESTION_ANSWER, TASK_ANSWER

CHAT_TEXT = "\n".join(
    [
        "[21/10/2026, 23:02:00] Ana: Club night is on! this Saturday at the hall, doors 7pm",
        "[21/10/2026, 23:03:00] Ben: I can bring the projector",
        "[21/10/2026, 23:04:00] Cleo: I'll sort the playlist again",
        "[21/10/2026, 23:05:00] Ana: Agreed with the committee: the quiz is postponed to November",
        "[21/10/2026, 23:06:00] Ben: Quick question — who is doing the door rota this time?",
    ]
)

ANSWERS = {
    "event": ("extraction", EVENT_ANSWER),
    "task": ("extraction_tasks", TASK_ANSWER),
    "decision": ("extraction_decisions", DECISION_ANSWER),
    "question": ("extraction_questions", QUESTION_ANSWER),
}

GROUP_TZ = "America/New_York"


def record_answers(driver: FixtureDriver, chunks, answers=None, *, usage=None):
    """Record answers for the given chunks under the exact
    prompt+schema keys the pipeline will compute."""
    for _kind, (prompt_name, answer) in (answers or ANSWERS).items():
        schema = promptlib.load_prompt(prompt_name).schema
        driver.add_json(
            build_prompt(prompt_name, chunks, GROUP_TZ), schema, answer,
            usage=usage,
        )


def build_world(client, app, db_session, *, answers=None, usage=None):
    """Owner + group (America/New_York) + pasted golden chat +
    fixture client installed. Returns the world dict; extraction
    itself is run with run_extraction_job()."""
    owner = register(client)
    group = make_group(client, owner, home_timezone=GROUP_TZ)
    pasted = client.post(
        f"/api/v1/groups/{group['id']}/sources/paste",
        json={"title": "Club chat", "text": CHAT_TEXT},
        headers=bearer(owner),
    )
    assert pasted.status_code == 201, pasted.text
    source_id = pasted.json()["id"]
    from gpa.models.groups import Group
    from gpa.models.sources import Source

    group_row = db_session.get(Group, group["id"])
    source_row = db_session.get(Source, source_id)
    chunks = source_service.list_chunks(db_session, source_row)
    driver = FixtureDriver()
    record_answers(driver, chunks, answers, usage=usage)
    ai_client.set_client(driver)
    return {
        "owner": owner,
        "group": group,
        "group_row": group_row,
        "source_id": source_id,
        "source": source_row,
        "chunks": chunks,
        "driver": driver,
    }


def run_extraction_job(client, app, db_session, world, *, kinds=None):
    """POST the extract request as the owner and run the job the
    way the worker would. Returns the job id."""
    body = {} if kinds is None else {"kinds": kinds}
    response = client.post(
        f"/api/v1/groups/{world['group']['id']}/sources/{world['source_id']}/extract",
        json=body, headers=bearer(world["owner"]),
    )
    assert response.status_code == 202, response.text
    assert job_service.process_one(db_session, app.state.settings, "test-worker") is True
    return response.json()["job_id"]


def candidates_by_kind(client, world):
    """The queue as the owner sees it, keyed by kind."""
    response = client.get(
        f"/api/v1/groups/{world['group']['id']}/candidates",
        headers=bearer(world["owner"]),
    )
    assert response.status_code == 200, response.text
    return {row["kind"]: row for row in response.json()}
