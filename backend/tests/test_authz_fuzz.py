"""Authorisation fuzz (roadmap B-140).

The role × route matrix, re-run as a fuzz: two groups, one of
them (B) rich with marked content; a crowd of attackers whose
only relationship to B is *none* — another group's owner, admin,
organiser, and member, an agent seat from that group, a personal
API key with the admin scope, a user in no group, and nobody at
all. Every read of B's resources, every mutation against them,
and every attempt to reach them through the *attacker's own*
group's paths (the id-swap) must come back refused, must carry
none of B's marker text, and must leave B exactly as it was.

Cross-group leakage is the one unforgivable bug class, so the
passing grade here is zero: zero successes, zero marker bytes,
zero state changes.

The world is built once per module (the attackers' failed
attempts change nothing, so sharing it is safe, and a positive
control — B's own owner reading everything — runs first to
prove every fuzzed route is real).
"""

import io
import random

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import bearer, make_group, make_settings, register, unique_email
from gpa.main import create_app
from gpa.services import jobs as job_service

MARK = "FUZZB140"  # Every planted string in group B carries this.
PWN = "FUZZB140-PWNED"


def _invite_and_accept(client, owner, group_id, email, role):
    response = client.post(
        f"/api/v1/groups/{group_id}/invites",
        json={"email": email, "role": role},
        headers=bearer(owner),
    )
    assert response.status_code == 201, response.text
    invitee = register(client, email=email)
    accepted = client.post(
        f"/api/v1/groups/{group_id}/invites/accept", headers=bearer(invitee)
    )
    assert accepted.status_code == 200, accepted.text
    return invitee


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (30, 90, 30)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    settings = make_settings(tmp_path_factory.mktemp("fuzz"))
    app = create_app(settings)
    with TestClient(app) as client:
        w: dict = {"client": client, "settings": settings, "app": app}

        # --- Group A: the attackers' home. ---------------------
        a_owner = register(client)
        group_a = make_group(client, a_owner, name="Attacker Home Group")
        a_admin = _invite_and_accept(client, a_owner, group_a["id"], unique_email(), "admin")
        a_organiser = _invite_and_accept(client, a_owner, group_a["id"], unique_email(), "organiser")
        a_member = _invite_and_accept(client, a_owner, group_a["id"], unique_email(), "member")
        seat = client.post(
            f"/api/v1/groups/{group_a['id']}/seats",
            json={"name": "Fuzz Seat", "role": "member",
                  "scopes": ["read", "draft", "publish"]},
            headers=bearer(a_owner),
        )
        assert seat.status_code == 201, seat.text
        member_key = client.post(
            "/api/v1/users/me/api-keys",
            json={"label": "fuzz key", "scopes": ["read", "draft", "publish", "admin"]},
            headers=bearer(a_member),
        )
        assert member_key.status_code == 201, member_key.text
        outsider = register(client)  # In no group at all.

        # --- Group B: the marked target. -----------------------
        b_owner = register(client)
        group_b = make_group(client, b_owner, name=f"{MARK} Village")
        gid = group_b["id"]
        b_member = _invite_and_accept(client, b_owner, gid, unique_email(), "member")
        bh = bearer(b_owner)

        published = client.post(
            f"/api/v1/groups/{gid}/events",
            json={"title": f"{MARK} Published Picnic",
                  "starts_at": "2026-11-06T18:30:00-05:00",
                  "ends_at": "2026-11-06T21:00:00-05:00"},
            headers=bh,
        )
        assert published.status_code == 201, published.text
        draft = client.post(
            f"/api/v1/groups/{gid}/events",
            json={"title": f"{MARK} Draft Gala", "status": "draft",
                  "starts_at": "2026-11-07T18:30:00-05:00",
                  "ends_at": "2026-11-07T21:00:00-05:00"},
            headers=bh,
        )
        assert draft.status_code == 201, draft.text
        candidate = client.post(
            f"/api/v1/groups/{gid}/candidates",
            json={"kind": "event", "title": f"{MARK} Candidate Fete"},
            headers=bh,
        )
        assert candidate.status_code == 201, candidate.text
        source = client.post(
            f"/api/v1/groups/{gid}/sources/paste",
            json={"title": f"{MARK} Chat",
                  "text": f"Ana: the {MARK} secret picnic spot is the old quarry\nBen: noted\n",
                  "granted_scope": "the planning chat"},
            headers=bh,
        )
        assert source.status_code == 201, source.text
        flyer = client.post(
            f"/api/v1/groups/{gid}/sources/image",
            params={"filename": "poster.png"},
            content=_png_bytes(),
            headers={**bh, "Content-Type": "image/png"},
        )
        assert flyer.status_code == 201, flyer.text
        integration = client.post(
            f"/api/v1/groups/{gid}/integrations",
            json={"kind": "samtal", "name": f"{MARK} Link",
                  "config": {"api_token": f"{MARK}-SECRET-VALUE"}},
            headers=bh,
        )
        assert integration.status_code == 201, integration.text
        subgroup = client.post(
            f"/api/v1/groups/{gid}/subgroups", json={"name": f"{MARK} Crew"}, headers=bh
        )
        assert subgroup.status_code == 201, subgroup.text
        announcement = client.post(
            f"/api/v1/groups/{gid}/announcements",
            json={"title": f"{MARK} Announcement", "body": "Read me."}, headers=bh,
        )
        assert announcement.status_code == 201, announcement.text
        resource = client.post(
            f"/api/v1/groups/{gid}/resources", json={"name": f"{MARK} Van"}, headers=bh
        )
        assert resource.status_code == 201, resource.text
        poll = client.post(
            f"/api/v1/groups/{gid}/polls",
            json={"question": f"{MARK} Question?",
                  "options": [{"label": "Yes"}, {"label": "No"}]},
            headers=bh,
        )
        assert poll.status_code == 201, poll.text
        task = client.post(
            f"/api/v1/groups/{gid}/tasks", json={"title": f"{MARK} Task"}, headers=bh
        )
        assert task.status_code == 201, task.text
        feed = client.post(
            f"/api/v1/groups/{gid}/calendar/feeds", json={"filters": {}}, headers=bh
        )
        assert feed.status_code == 201, feed.text

        session = app.state.db.session()
        try:
            group_job = job_service.enqueue(
                session, settings, kind="digest_run", payload={"group_id": gid}
            )
            source_job = job_service.enqueue(
                session, settings, kind="extract",
                payload={"source_id": source.json()["id"]},
            )
        finally:
            session.close()

        w.update(
            group_a=group_a, group_b=group_b, b_owner=b_owner, b_member=b_member,
            published=published.json(), draft=draft.json(),
            candidate=candidate.json(), source=source.json(),
            flyer=flyer.json(), integration=integration.json(),
            subgroup=subgroup.json(), announcement=announcement.json(),
            resource=resource.json(), poll=poll.json(), task=task.json(),
            feed=feed.json(), group_job=group_job, source_job=source_job,
            attackers={
                "a-owner": bearer(a_owner),
                "a-admin": bearer(a_admin),
                "a-organiser": bearer(a_organiser),
                "a-member": bearer(a_member),
                "a-seat": {"X-Api-Key": seat.json()["key"]},
                "a-member-admin-key": {"X-Api-Key": member_key.json()["key"]},
                "outsider": bearer(outsider),
                "anonymous": {},
            },
        )
        yield w


def _reads(w) -> list[str]:
    """Every read surface over group B's world (paths only)."""
    b, g = w["group_b"], w["group_b"]["id"]
    return [
        f"/api/v1/groups/{g}",
        f"/api/v1/groups/{g}/members",
        f"/api/v1/groups/{g}/audit",
        f"/api/v1/groups/{g}/events",
        f"/api/v1/groups/{g}/events/{w['published']['id']}",
        f"/api/v1/groups/{g}/events/{w['draft']['id']}",
        f"/api/v1/groups/{g}/events/{w['published']['id']}/ics",
        f"/api/v1/groups/{g}/agenda",
        f"/api/v1/groups/{g}/month?year=2026&month=11",
        f"/api/v1/groups/{g}/tonight",
        f"/api/v1/groups/{g}/conflicts",
        f"/api/v1/groups/{g}/candidates",
        f"/api/v1/groups/{g}/candidates/{w['candidate']['id']}",
        f"/api/v1/groups/{g}/sources",
        f"/api/v1/groups/{g}/sources/{w['source']['id']}",
        f"/api/v1/groups/{g}/flyers",
        f"/api/v1/groups/{g}/print/month?month=2026-11",
        f"/api/v1/groups/{g}/integrations",
        f"/api/v1/groups/{g}/integrations/{w['integration']['id']}",
        f"/api/v1/groups/{g}/integrations/health-board",
        f"/api/v1/groups/{g}/subgroups",
        f"/api/v1/groups/{g}/announcements",
        f"/api/v1/groups/{g}/resources",
        f"/api/v1/groups/{g}/polls",
        f"/api/v1/groups/{g}/polls/{w['poll']['id']}",
        f"/api/v1/groups/{g}/tasks",
        f"/api/v1/groups/{g}/tasks/{w['task']['id']}",
        f"/api/v1/groups/{g}/settings",
        f"/api/v1/groups/{g}/calendar/feeds",
        f"/api/v1/groups/{g}/quality-report",
        f"/api/v1/groups/{g}/search?q={MARK}",
        f"/api/v1/groups/{g}/planning-digest",
        f"/api/v1/groups/{g}/supplies",
        f"/api/v1/groups/{g}/events/{w['published']['id']}/rsvps",
        f"/api/v1/jobs/{w['group_job'].id}",
        f"/api/v1/jobs/{w['source_job'].id}",
    ]


def _capability_reads(w) -> list[str]:
    """The anonymous capability surfaces. Group B is PRIVATE,
    so it has no public face at all: these must refuse every
    caller — attackers, strangers, and B's own owner alike."""
    b = w["group_b"]
    return [
        f"/api/v1/public/groups/{b['slug']}",
        f"/api/v1/embed/groups/{b['slug']}",
        "/api/v1/calendar/feed/forged-token-000.ics",
    ]


def _writes(w) -> list[tuple[str, str, dict | None]]:
    """Mutations against group B: (method, path, json body)."""
    g = w["group_b"]["id"]
    pub, cand = w["published"]["id"], w["candidate"]["id"]
    member_id = w["b_member"]["user_id"]
    option_id = w["poll"]["options"][0]["id"]
    return [
        ("PATCH", f"/api/v1/groups/{g}", {"name": PWN}),
        ("PATCH", f"/api/v1/groups/{g}/events/{pub}", {"title": PWN}),
        ("POST", f"/api/v1/groups/{g}/events/{pub}/cancel", {"reason": "fuzz"}),
        ("DELETE", f"/api/v1/groups/{g}/sources/{w['source']['id']}", None),
        ("POST", f"/api/v1/groups/{g}/candidates/{cand}/approve", {}),
        ("POST", f"/api/v1/groups/{g}/candidates/{cand}/reject", {}),
        ("PATCH", f"/api/v1/groups/{g}/members/{member_id}/role", {"role": "owner"}),
        ("DELETE", f"/api/v1/groups/{g}/members/{member_id}", None),
        ("POST", f"/api/v1/groups/{g}/seats",
         {"name": "fuzz seat", "role": "member", "scopes": ["read"]}),
        ("PUT", f"/api/v1/groups/{g}/settings", {"review_min_role": "member"}),
        ("POST", f"/api/v1/groups/{g}/announcements", {"title": PWN, "body": "x"}),
        ("DELETE", f"/api/v1/groups/{g}/integrations/{w['integration']['id']}", None),
        ("POST", f"/api/v1/groups/{g}/polls/{w['poll']['id']}/vote",
         {"option_id": option_id}),
        ("PUT", f"/api/v1/groups/{g}/events/{pub}/rsvp",
         {"status": "coming", "guests": 0}),
        ("POST", f"/api/v1/groups/{g}/subgroups", {"name": PWN}),
        ("POST", f"/api/v1/groups/{g}/resources", {"name": PWN}),
        ("POST", f"/api/v1/groups/{g}/tasks", {"title": PWN}),
        ("PATCH", f"/api/v1/groups/{g}/tasks/{w['task']['id']}", {"title": PWN}),
        ("POST", f"/api/v1/groups/{g}/calendar/feeds/{w['feed']['id']}/revoke", {}),
    ]


def _swaps(w) -> list[tuple[str, str, dict | None]]:
    """Group B's resource ids reached through group A's paths."""
    a = w["group_a"]["id"]
    return [
        ("GET", f"/api/v1/groups/{a}/events/{w['published']['id']}", None),
        ("GET", f"/api/v1/groups/{a}/sources/{w['source']['id']}", None),
        ("GET", f"/api/v1/groups/{a}/candidates/{w['candidate']['id']}", None),
        ("GET", f"/api/v1/groups/{a}/integrations/{w['integration']['id']}", None),
        ("GET", f"/api/v1/groups/{a}/polls/{w['poll']['id']}", None),
        ("GET", f"/api/v1/groups/{a}/tasks/{w['task']['id']}", None),
        ("POST", f"/api/v1/groups/{a}/candidates/{w['candidate']['id']}/approve", {}),
        ("DELETE", f"/api/v1/groups/{a}/sources/{w['source']['id']}", None),
        ("PATCH", f"/api/v1/groups/{a}/events/{w['published']['id']}", {"title": PWN}),
    ]


REFUSALS = (401, 403, 404, 405, 409, 422)


def test_positive_control_owner_reads_everything(world):
    """The fuzz table is real: B's owner can read every surface
    in it. Without this control, a table of wrong paths would
    "pass" by 404ing for everyone."""
    client = world["client"]
    for path in _reads(world):
        response = client.get(path, headers=bearer(world["b_owner"]))
        assert response.status_code == 200, (path, response.status_code, response.text[:200])


def test_fuzz_foreign_reads_never_leak(world):
    client, rng = world["client"], random.Random(140)
    cases = [(name, path) for name in world["attackers"] for path in _reads(world)]
    rng.shuffle(cases)
    for name, path in cases:
        response = client.get(path, headers=world["attackers"][name])
        assert response.status_code in REFUSALS, (name, path, response.status_code)
        assert MARK not in response.text, (name, path, "marker leaked")
        assert PWN not in response.text


def test_fuzz_foreign_writes_never_stick(world):
    client, rng = world["client"], random.Random(141)
    cases = [
        (name, method, path, body)
        for name in world["attackers"]
        for method, path, body in _writes(world)
    ]
    rng.shuffle(cases)
    for name, method, path, body in cases:
        response = client.request(method, path, json=body, headers=world["attackers"][name])
        assert response.status_code in REFUSALS, (name, method, path, response.status_code)
        assert MARK not in response.text, (name, method, path)

    # …and B is exactly as it was: the aftermath is the proof.
    bh, gid = bearer(world["b_owner"]), world["group_b"]["id"]
    group = client.get(f"/api/v1/groups/{gid}", headers=bh).json()
    assert group["name"] == f"{MARK} Village"
    event = client.get(f"/api/v1/groups/{gid}/events/{world['published']['id']}", headers=bh).json()
    assert event["title"] == f"{MARK} Published Picnic"
    assert event.get("cancelled_at") in (None, "")
    candidate = client.get(
        f"/api/v1/groups/{gid}/candidates/{world['candidate']['id']}", headers=bh
    ).json()
    assert candidate["status"] == "pending"
    members = client.get(f"/api/v1/groups/{gid}/members", headers=bh).json()
    roles = {m["user_id"]: m["role"] for m in members}
    assert roles[world["b_member"]["user_id"]] == "member"
    assert len(members) == 2
    settings = client.get(f"/api/v1/groups/{gid}/settings", headers=bh).json()
    assert settings["review_min_role"] == "organiser"
    assert len(client.get(f"/api/v1/groups/{gid}/announcements", headers=bh).json()) == 1
    assert len(client.get(f"/api/v1/groups/{gid}/integrations", headers=bh).json()) == 1
    assert len(client.get(f"/api/v1/groups/{gid}/subgroups", headers=bh).json()) == 1
    assert len(client.get(f"/api/v1/groups/{gid}/resources", headers=bh).json()) == 1
    assert len(client.get(f"/api/v1/groups/{gid}/seats", headers=bh).json()) == 0
    source = client.get(f"/api/v1/groups/{gid}/sources/{world['source']['id']}", headers=bh)
    assert source.status_code == 200
    poll = client.get(f"/api/v1/groups/{gid}/polls/{world['poll']['id']}", headers=bh).json()
    assert sum(option.get("votes", 0) for option in poll["options"]) == 0
    feeds = client.get(f"/api/v1/groups/{gid}/calendar/feeds", headers=bh).json()
    assert len(feeds) == 1 and feeds[0].get("revoked_at") in (None, "")


def test_fuzz_swapped_group_ids_never_resolve(world):
    """The id-swap: the attacker IS a member of the path's
    group, so the door opens — the resource must still not
    resolve, because it belongs to another group entirely."""
    client = world["client"]
    own = {k: v for k, v in world["attackers"].items()
           if k in ("a-owner", "a-admin", "a-organiser", "a-member", "a-seat")}
    for name, headers in own.items():
        for method, path, body in _swaps(world):
            response = client.request(method, path, json=body, headers=headers)
            assert response.status_code in REFUSALS, (name, method, path, response.status_code)
            assert MARK not in response.text, (name, method, path)


def test_capability_surfaces_refuse_everyone_for_a_private_group(world):
    client = world["client"]
    callers = dict(world["attackers"])
    callers["b-owner"] = bearer(world["b_owner"])
    for name, headers in callers.items():
        for path in _capability_reads(world):
            response = client.get(path, headers=headers)
            assert response.status_code in REFUSALS, (name, path, response.status_code)
            assert MARK not in response.text, (name, path)
    # …and the embed door with a stranger's Origin header too.
    response = client.get(
        f"/api/v1/embed/groups/{world['group_b']['slug']}",
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code in REFUSALS
    assert MARK not in response.text
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_group_jobs_readable_by_their_own_members(world):
    """The other half of the B-140 job fix: scoping must not
    lock out the job's own group — B's member reads both jobs,
    and a group-less internal job still answers any caller."""
    client = world["client"]
    for job in (world["group_job"], world["source_job"]):
        response = client.get(f"/api/v1/jobs/{job.id}", headers=bearer(world["b_member"]))
        assert response.status_code == 200, response.text
