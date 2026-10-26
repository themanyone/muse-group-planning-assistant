"""The agent API pack (roadmap B-129).

The seat experience, completed as a surface a frontend (or
an agent framework) can discover in the contract itself:

- **The agent operations are tagged ``agent`` in the
  OpenAPI schema.** The list below IS the seat's working
  surface: read the group's sources (a hall included), read
  the review queue, draft candidates into it, read events,
  and ask the group memory. Tagging happens on the route
  objects in create_app, so the exported OPENAPI.json and
  the live schema agree by construction (the snapshot test
  holds them to it).
- **Scopes, in one place** (the frontend handoff quotes
  this): an API key carries scopes; a seat's key is checked
  at the door on every call, exactly like a person's key
  (B-014/B-017). ``read`` sees, ``draft`` creates sources
  and candidates, ``publish`` lets its holder decide in the
  review queue — under the ONE approval law (B-203, Phase
  19): approval additionally requires the group's approver
  ROLE, for a seat exactly as for a person, so a member seat
  holding publish still cannot approve, and an organiser
  seat holding publish approves through the same operation
  a human organiser's click calls. ``admin`` administers.
  A session (a signed-in person) carries every scope; the
  ladder only binds key callers.
- **The worked example** lives at
  ``examples/secretary_seat.py``: a secretary seat reads a
  Samtal hall and drafts what it finds into the queue. It
  runs in mock mode in the test suite — no model, no
  network, the seat's own key the only credential.
"""

from fastapi.routing import APIRoute

# The seat's working surface: (method, path template). Kept
# deliberately small — a seat is a colleague with a defined
# job, not a second admin API. Doors a seat must never use
# (member administration, integrations, settings) are absent
# by design. The approval door is absent from this B-129 pack
# too — not because a seat can never approve (since B-203 an
# approver-role seat approves under the one law, through the
# ordinary candidate route), but because this pack is the
# drafting surface; the queue's decision doors carry their
# own tags in the contract.
AGENT_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/groups/{group_id}/sources"),
    ("GET", "/api/v1/groups/{group_id}/sources/{source_id}"),
    ("GET", "/api/v1/groups/{group_id}/sources/{source_id}/speakers"),
    ("GET", "/api/v1/groups/{group_id}/candidates"),
    ("GET", "/api/v1/groups/{group_id}/candidates/{candidate_id}"),
    ("POST", "/api/v1/groups/{group_id}/candidates"),
    ("GET", "/api/v1/groups/{group_id}/events"),
    ("GET", "/api/v1/groups/{group_id}/events/{event_id}"),
    ("POST", "/api/v1/groups/{group_id}/ask"),
    ("GET", "/api/v1/groups/{group_id}/summaries"),
)

AGENT_TAG = "agent"

# The scope ladder, documented once (the handoff links here):
# what each scope lets a KEY caller do at the doors above.
SCOPE_GUIDE: dict[str, str] = {
    "read": "See the group's sources, queue, events, and memory.",
    "draft": "Create sources and draft candidates into the queue.",
    "publish": "Request extractions and syncs, and decide in the "
    "review queue — only at the group's approver role, under the "
    "one approval law (B-203): a seat below that role can never "
    "approve, whatever scopes it holds.",
    "admin": "Administer seats, links, integrations, and webhooks.",
}


def _all_api_routes(app):
    """Every APIRoute the app serves. This FastAPI version
    keeps included routers lazy (``_IncludedRouter`` entries
    in app.routes, holding the original router), so the walk
    looks through them to the route objects the OpenAPI
    builder itself reads — the same objects, so a tag placed
    here is a tag in the exported contract."""
    for entry in app.routes:
        if isinstance(entry, APIRoute):
            yield entry
            continue
        router = getattr(entry, "original_router", None)
        if router is not None:
            for route in router.routes:
                if isinstance(route, APIRoute):
                    yield route


def tag_agent_operations(app) -> int:
    """Append the ``agent`` tag to the pack's operations on
    the app's routes. Returns how many operations were
    tagged — create_app calls this once, after every router
    is in, so the exported contract and the live schema are
    the same document."""
    wanted = set(AGENT_OPERATIONS)
    tagged = 0
    for route in _all_api_routes(app):
        for method in route.methods or set():
            if (method, route.path) in wanted:
                if AGENT_TAG not in route.tags:
                    route.tags.append(AGENT_TAG)
                tagged += 1
                break
    return tagged
