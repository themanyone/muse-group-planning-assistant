"""Multi-pass pruning (roadmap B-114).

The engine reapplies: pass 1 prunes, a second pass re-judges
ONLY the dropped set with fresh eyes, and a chunk the first
judge missed can be rescued. Pass count and token budget are
settings with HARD caps — the engine enforces them itself:
ask for ten passes, get at most the ceiling; spend the budget,
and unjudged chunks stay dropped, visibly, with the run marked
budget-stopped rather than quietly thin.
"""

from conftest import bearer, make_group, register
from gpa.ai import pruning, promptlib
from gpa.ai.drivers_fixture import FixtureDriver
from gpa.ai.extraction import chunk_windows

QUESTION = "Why did we stop using the school gym?"

TEXTS = [
    "Quiz night moves to Thursdays from next month, same time.",
    "We agreed club nights belong in the village hall this year.",
    "The raffle raised forty pounds for the roof fund.",
    "Ben brings the question cards; Cy keeps the scores.",
    "Tea urn needs descaling before the winter social.",
    # Position 5: the landmine — phrased like none of the
    # question's words, and exactly what settles it. The first
    # judge (in this recording) overlooks it; the second look
    # rescues it. That recovery IS the acceptance.
    "Twice this spring we arrived to find another club already "
    "set up in the sports hall, our booking nowhere on their sheet.",
    "The hall key lives with Ana when she is not travelling.",
    "Poster ink is ordered; the blue cartridge is the urgent one.",
]
LANDMINE = 5


def corpus() -> list[pruning.MemoryChunk]:
    return [
        pruning.MemoryChunk(
            chunk_id=f"multi-{i}", position=i, text=text, sender="Ana",
        )
        for i, text in enumerate(TEXTS)
    ]


def record(driver: FixtureDriver, chunks, *, pass1_kept: set[int], rescued: set[int]):
    """Record the two passes' judgements, keyed by the engine's
    own prompt renderings (pass 1 and the reconsider pass hash
    differently by design)."""
    schema1 = promptlib.load_prompt("prune").schema
    for window in chunk_windows(chunks):
        kept = [c.position for c in window if c.position in pass1_kept]
        driver.add_json(
            pruning.build_prune_prompt(QUESTION, window), schema1, {"kept": kept}
        )
    dropped = [c for c in chunks if c.position not in pass1_kept]
    schema2 = promptlib.load_prompt("prune_reconsider").schema
    for window in chunk_windows(dropped):
        kept = [c.position for c in window if c.position in rescued]
        driver.add_json(
            pruning.build_prune_prompt(QUESTION, window, reconsider=True),
            schema2, {"kept": kept},
        )
    # A third pass (the ceiling test asks for ten) re-judges
    # what the rescue left behind — a smaller, different window.
    still_dropped = [c for c in dropped if c.position not in rescued]
    for window in chunk_windows(still_dropped):
        driver.add_json(
            pruning.build_prune_prompt(QUESTION, window, reconsider=True),
            schema2, {"kept": []},
        )


def test_pass_two_recovers_a_chunk_pass_one_dropped():
    chunks = corpus()
    driver = FixtureDriver()
    record(driver, chunks, pass1_kept={1}, rescued={LANDMINE})

    outcome = pruning.prune_multi(driver, question=QUESTION, chunks=chunks, max_passes=2)

    kept = {c.position for c in outcome.kept}
    assert 1 in kept
    assert LANDMINE in kept  # the recovery itself
    assert outcome.passes == 2
    # Survivors are back in corpus order after the rescue.
    assert [c.position for c in outcome.kept] == sorted(kept)


def test_pass_cap_one_means_no_recovery():
    chunks = corpus()
    driver = FixtureDriver()
    record(driver, chunks, pass1_kept={1}, rescued={LANDMINE})

    outcome = pruning.prune_multi(driver, question=QUESTION, chunks=chunks, max_passes=1)

    assert LANDMINE not in {c.position for c in outcome.kept}
    assert outcome.passes == 1


def test_the_pass_ceiling_is_hard_whatever_the_caller_asks():
    chunks = corpus()
    driver = FixtureDriver()
    record(driver, chunks, pass1_kept={1}, rescued={LANDMINE})

    outcome = pruning.prune_multi(driver, question=QUESTION, chunks=chunks, max_passes=10)

    assert outcome.passes <= pruning.MAX_PASSES_CEILING


def test_token_budget_stops_the_engine_before_it_spends():
    chunks = corpus()
    driver = FixtureDriver()  # no recordings: any call would raise

    outcome = pruning.prune_multi(
        driver, question=QUESTION, chunks=chunks, max_passes=2, token_budget=10
    )

    # Ten tokens cannot pay for even one window's prompt, so
    # nothing was judged, everything stands dropped, and the
    # run says WHY it is thin: the budget, not the model.
    assert outcome.kept == []
    assert {c.position for c in outcome.dropped} == set(range(len(TEXTS)))
    assert outcome.budget_stopped is True
    assert outcome.input_tokens == 0


def test_group_settings_carry_the_caps_and_refuse_past_them(client):
    owner = register(client)
    group = make_group(client, owner)
    url = f"/api/v1/groups/{group['id']}/settings"

    before = client.get(url, headers=bearer(owner)).json()
    assert before["prune_max_passes"] == 2
    assert before["prune_token_budget"] == 120_000
    assert before["prune_driver"] == "hosted"

    updated = client.put(
        url,
        json={"prune_max_passes": 3, "prune_token_budget": 5000,
              "prune_driver": "local"},
        headers=bearer(owner),
    )
    assert updated.status_code == 200, updated.text
    pack = updated.json()
    assert pack["prune_max_passes"] == 3
    assert pack["prune_token_budget"] == 5000
    assert pack["prune_driver"] == "local"

    # Past the hard cap is refused at the door, not ignored.
    refused = client.put(
        url, json={"prune_max_passes": 9}, headers=bearer(owner)
    )
    assert refused.status_code in (400, 422), refused.text
    refused = client.put(
        url, json={"prune_driver": "somewhere-else"}, headers=bearer(owner)
    )
    assert refused.status_code in (400, 422), refused.text
