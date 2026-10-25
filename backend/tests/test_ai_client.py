"""AI adapter tests (roadmap B-035, B-036, B-037).

The protocol, its three drivers, and the disciplines the roadmap
pins on them: drivers swap by configuration alone; the mock's
output is schema-valid; the fixture driver replays by prompt hash
and refuses to invent; the live driver retries 429/5xx with
backoff, never duplicates a succeeded call, and accounts usage +
cost. The live driver is exercised through a stubbed httpx
transport — the suite never touches a real network.
"""

import json

import httpx
import pytest

from conftest import make_settings
from gpa.ai import client as ai_client
from gpa.ai.client import FixtureMissError, MetaModelClient, ModelError
from gpa.ai.drivers_fixture import FixtureDriver, prompt_key
from gpa.ai.drivers_live import LiveDriver
from gpa.ai.drivers_mock import MockDriver, synthesize

EVENT_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["fields"],
                "properties": {
                    "fields": {
                        "type": "object",
                        "properties": {"title": {"$ref": "#/$defs/field"}},
                    }
                },
            },
        }
    },
    "$defs": {
        "field": {
            "type": "object",
            "required": ["value", "confidence", "quote"],
            "properties": {
                "value": {"type": ["string", "null"]},
                "confidence": {"type": "number"},
                "quote": {"type": "string"},
            },
        }
    },
}


# --- B-035: the protocol + config swap ---------------------------------


def test_all_drivers_satisfy_the_protocol(settings):
    """Every driver is a MetaModelClient — the pipelines' one door."""
    assert isinstance(MockDriver(), MetaModelClient)
    assert isinstance(FixtureDriver(), MetaModelClient)
    live_settings = make_settings(
        settings.storage_dir.parent, ai_driver="live",
        ai_api_base="https://models.example.test/v1", ai_api_key="test-key",
    )
    assert isinstance(LiveDriver(live_settings), MetaModelClient)


def test_driver_swap_is_a_config_change(tmp_path):
    """B-035 acceptance: build_client follows settings, nothing else."""
    mock = ai_client.build_client(make_settings(tmp_path, ai_driver="mock"))
    fixture = ai_client.build_client(make_settings(tmp_path, ai_driver="fixture"))
    live = ai_client.build_client(
        make_settings(
            tmp_path, ai_driver="live",
            ai_api_base="https://models.example.test/v1", ai_api_key="k",
        )
    )
    assert mock.driver_name == "mock"
    assert fixture.driver_name == "fixture"
    assert live.driver_name == "live"
    with pytest.raises(ModelError):
        ai_client.build_client(make_settings(tmp_path, ai_driver="carrier-pigeon"))


def test_live_driver_refuses_to_start_without_env_secrets(tmp_path):
    """B-037: no base URL or no key is a startup error in plain
    words — never a silent unauthenticated call later."""
    with pytest.raises(ModelError, match="GPA_AI_API_BASE"):
        LiveDriver(make_settings(tmp_path, ai_api_base="", ai_api_key="k"))
    with pytest.raises(ModelError, match="GPA_AI_API_KEY"):
        LiveDriver(make_settings(tmp_path, ai_api_base="https://x.test", ai_api_key=""))


# --- B-036: mock + fixture drivers --------------------------------------


def test_mock_complete_json_is_schema_valid():
    """The mock synthesises the smallest valid instance: required
    keys present, the items array present (and empty — the mock
    drafts nothing, and that honesty is the point)."""
    result = MockDriver().complete_json(prompt="extract", schema=EVENT_SCHEMA)
    assert result.data == {"items": []}
    assert result.cost_micros == 0
    assert result.input_tokens > 0


def test_synthesize_handles_refs_and_types():
    field = synthesize(EVENT_SCHEMA["$defs"]["field"], EVENT_SCHEMA)
    assert field == {"value": "", "confidence": 0.0, "quote": ""}
    assert synthesize({"type": "array", "items": {"type": "string"}}) == []
    assert synthesize({"type": ["integer", "null"]}) == 0


def test_fixture_driver_replays_by_prompt_hash():
    """Recordings are keyed by the hash of the exact prompt (+
    schema): same call, same answer; the key function is public so
    the pipeline and its tests cannot drift apart."""
    driver = FixtureDriver()
    driver.add_json("PROMPT", EVENT_SCHEMA, {"items": [{"fields": {}}]})
    assert prompt_key("PROMPT", schema=EVENT_SCHEMA) in driver._recordings
    result = driver.complete_json(prompt="PROMPT", schema=EVENT_SCHEMA)
    assert result.data == {"items": [{"fields": {}}]}
    assert "(fixture)" in result.model_id


def test_fixture_driver_miss_is_loud_not_invented():
    driver = FixtureDriver()
    with pytest.raises(FixtureMissError, match="No fixture recording"):
        driver.complete_json(prompt="unrecorded", schema=EVENT_SCHEMA)
    with pytest.raises(FixtureMissError):
        driver.complete_text(prompt="also unrecorded")


def test_fixture_recordings_load_from_directory(tmp_path):
    """A recordings directory is the demo's offline fuel: files
    named by key, replayed verbatim."""
    key = prompt_key("hello")
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"text": "recorded answer", "usage": {"input_tokens": 5}}),
        encoding="utf-8",
    )
    driver = FixtureDriver(directory=tmp_path)
    result = driver.complete_text(prompt="hello")
    assert result.text == "recorded answer"
    assert result.input_tokens == 5


# --- B-037: the live driver, stubbed transport ---------------------------


def _live(tmp_path, handler, **overrides):
    """A live driver wired to a stub transport + no real sleeping."""
    settings = make_settings(
        tmp_path, ai_api_base="https://models.example.test/v1",
        ai_api_key="test-key", ai_live_max_attempts=3,
        ai_price_spark_micros_per_1k=2000, **overrides,
    )
    sleeps: list[float] = []
    driver = LiveDriver(
        settings,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        jitter=lambda: 0.0,
    )
    return driver, sleeps


def _ok_body(text: str, prompt_tokens=100, completion_tokens=50):
    return {
        "model": "muse-spark",
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def test_live_driver_success_accounts_usage_and_cost(tmp_path):
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json=_ok_body("the answer"))

    driver, _ = _live(tmp_path, handler)
    result = driver.complete_text(prompt="a question", tier="spark")
    assert result.text == "the answer"
    assert (result.input_tokens, result.output_tokens) == (100, 50)
    # (100 + 50) tokens at 2000 micros per 1k -> 300 micros, exact.
    assert result.cost_micros == 300


def test_live_driver_429_storm_backs_off_then_succeeds(tmp_path):
    """B-037 acceptance: a 429 storm is ridden out with backoff —
    and the call that finally succeeds is made exactly once."""
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json=_ok_body("through"))

    driver, sleeps = _live(tmp_path, handler)
    result = driver.complete_text(prompt="q")
    assert result.text == "through"
    assert calls["count"] == 3
    assert len(sleeps) == 2  # backed off between attempts, not after success


def test_live_driver_never_duplicates_a_succeeded_call(tmp_path):
    """One 2xx = one request, even though the transport would have
    answered again. The paid call is never repeated."""
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(200, json=_ok_body("once"))

    driver, _ = _live(tmp_path, handler)
    assert driver.complete_text(prompt="q").text == "once"
    assert calls["count"] == 1


def test_live_driver_does_not_retry_client_errors(tmp_path):
    """A 400 is an answer, not an accident: no retry, a clear error."""
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    driver, _ = _live(tmp_path, handler)
    with pytest.raises(ModelError, match="400"):
        driver.complete_text(prompt="q")
    assert calls["count"] == 1


def test_live_driver_gives_up_after_max_attempts(tmp_path):
    def handler(request):
        return httpx.Response(503, json={"error": "down"})

    driver, sleeps = _live(tmp_path, handler)
    with pytest.raises(ModelError, match="after 3 attempts"):
        driver.complete_text(prompt="q")
    assert len(sleeps) == 2


def test_live_driver_complete_json_parses_and_unwraps_fences(tmp_path):
    def handler(request):
        body = _ok_body('```json\n{"items": []}\n```')
        return httpx.Response(200, json=body)

    driver, _ = _live(tmp_path, handler)
    result = driver.complete_json(prompt="extract", schema=EVENT_SCHEMA)
    assert result.data == {"items": []}


def test_live_driver_bad_json_is_a_failed_call(tmp_path):
    def handler(request):
        return httpx.Response(200, json=_ok_body("not json at all"))

    driver, _ = _live(tmp_path, handler)
    with pytest.raises(ModelError, match="valid JSON"):
        driver.complete_json(prompt="extract", schema=EVENT_SCHEMA)
