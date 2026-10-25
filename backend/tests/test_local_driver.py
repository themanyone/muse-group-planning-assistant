"""The local-inference driver (roadmap B-119).

A group may prune on a model it hosts itself — an
OpenAI-compatible endpoint — while extraction stays hosted.
The acceptance is the swap: the same corpus, the same
question, two drivers, the SAME kept set — swapping drivers
is a configuration choice and changes no domain behaviour.
Plus the honest edges: the driver is text-only and says so,
and asking for local without an endpoint configured is a
plain refusal, never a silent fallback.
"""

import json
from datetime import datetime

import httpx
import pytest

from conftest import make_group, register
from gpa.ai import client as ai_client
from gpa.ai import pruning
from gpa.ai.client import ModelError
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.drivers_local import LocalDriver
from gpa.config import Settings
from gpa.models.groups import Group
from gpa.services import memory as memory_service
from test_pruning import record_judgements

QUESTION = "What did we decide about club nights?"
CHUNKS = [
    pruning.MemoryChunk(
        chunk_id="source:a", position=0,
        text="Quiz rota: Ben brings the cards, Cy scores.",
        sender="Ben", sent_at=datetime(2026, 7, 6, 19, 0),
    ),
    pruning.MemoryChunk(
        chunk_id="source:b", position=1,
        text="We decided: the hall, not the school gym, for club nights.",
        sender="Ana", sent_at=datetime(2026, 7, 6, 19, 4),
    ),
    pruning.MemoryChunk(
        chunk_id="source:c", position=2,
        text="The tea urn needs descaling before winter.",
        sender="Cy", sent_at=datetime(2026, 7, 6, 19, 9),
    ),
]


def local_settings() -> Settings:
    return Settings(
        ai_local_base="http://local.test/v1",
        ai_local_model="local-test-model",
    )


def stub_transport() -> httpx.MockTransport:
    """A local endpoint that judges like the fixture does:
    the middle chunk is the relevant one."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "local-test-model"
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"kept": [1]})}}
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5},
            },
        )

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    ai_client.set_client(None)


def test_local_driver_judges_over_the_wire_shape():
    driver = LocalDriver(local_settings(), transport=stub_transport())
    seen = []
    outcome = pruning.prune_once(
        driver, question=QUESTION, chunks=CHUNKS, token_budget=100_000,
        record_usage=lambda result, name: seen.append(result),
    )
    assert [c.chunk_id for c in outcome.kept] == ["source:b"]
    # Provenance is honest: the local model judged, at no
    # metered cost — the group's own hardware.
    assert seen and seen[0].model_id == "local-test-model"
    assert seen[0].cost_micros == 0


def test_local_driver_is_text_only_and_says_so():
    driver = LocalDriver(local_settings(), transport=stub_transport())
    for call in (
        lambda: driver.read_image(prompt="x", image_bytes=b"123"),
        lambda: driver.transcribe(audio_bytes=b"123"),
        lambda: driver.generate_image(prompt="x"),
    ):
        with pytest.raises(ModelError, match="text only"):
            call()


def test_local_driver_needs_its_endpoint_configured():
    with pytest.raises(ModelError, match="GPA_AI_LOCAL_BASE"):
        LocalDriver(Settings())
    with pytest.raises(ModelError, match="GPA_AI_LOCAL_MODEL"):
        LocalDriver(Settings(ai_local_base="http://local.test/v1"))


def test_group_setting_selects_the_driver(client, db_session):
    owner = register(client)
    group_json = make_group(client, owner)
    group = db_session.get(Group, group_json["id"])

    hosted = FixtureDriver()
    ai_client.set_client(hosted)
    assert memory_service.resolve_client(local_settings(), group) is hosted

    group.prune_driver = "local"
    db_session.commit()
    chosen = memory_service.resolve_client(local_settings(), group)
    assert isinstance(chosen, LocalDriver)

    # Local asked for but not configured: a plain refusal,
    # never a quiet ride on the hosted driver.
    with pytest.raises(ModelError, match="GPA_AI_LOCAL_BASE"):
        memory_service.resolve_client(Settings(), group)


def test_driver_swap_keeps_the_same_survivors():
    fixture = FixtureDriver()
    record_judgements(fixture, QUESTION, CHUNKS, {1})
    local = LocalDriver(local_settings(), transport=stub_transport())

    from_fixture = pruning.prune_once(
        fixture, question=QUESTION, chunks=CHUNKS, token_budget=100_000
    )
    from_local = pruning.prune_once(
        local, question=QUESTION, chunks=CHUNKS, token_budget=100_000
    )
    assert [c.chunk_id for c in from_local.kept] == [
        c.chunk_id for c in from_fixture.kept
    ]
    assert [c.chunk_id for c in from_local.dropped] == [
        c.chunk_id for c in from_fixture.dropped
    ]
