"""API parity register + checker (roadmap B-201, Phase 19).

- Every registered user-facing action exists as an external
  API route on the live app, served by the handler the
  register names, and that handler resolves as a callable
  internal operation;
- the reverse direction holds too: no live API route stands
  outside the register;
- a deliberately UI-only fixture action fails the checker
  BY NAME (the slice's acceptance test), as does a fixture
  whose internal operation does not exist;
- the parity report counts from the live app, names the
  B-162-backed actions, and reports the not-yet-built MCP
  and terminal surfaces as pending, never as passed.
"""

from gpa.services import parity
from gpa.services.parity import ParityAction


def test_every_registered_action_has_its_route_and_operation(app):
    failures = parity.check_parity(app)
    assert failures == [], f"parity failures: {failures[:5]}"


def test_register_covers_every_live_route(app):
    live = parity._live_routes(app)
    registered = {(a.method, a.path) for a in parity.ACTIONS}
    assert registered == set(live)
    assert len(parity.ACTIONS) == len(live) == len(registered)


def test_ui_only_fixture_action_fails_by_name(app):
    fixture = ParityAction(
        "dance_the_ui_only_dance",
        "POST",
        "/api/v1/groups/{group_id}/ui-only-dance",
        "gpa.routers.groups:ui_only_dance",
        "",
    )
    failures = parity.check_parity(app, parity.ACTIONS + (fixture,))
    named = [f for f in failures if "dance_the_ui_only_dance" in f]
    # Both legs name it: no API route, and no internal operation.
    assert len(named) == 2, failures


def test_missing_internal_operation_fails_by_name(app):
    # A real route wearing a handler that does not exist: the
    # route leg passes on path alone only if the handler also
    # matches, so this fails on the handler legs, by name.
    real = next(a for a in parity.ACTIONS if a.name == "read_group")
    broken = ParityAction(
        real.name, real.method, real.path,
        "gpa.routers.groups:no_such_handler", "",
    )
    actions = tuple(broken if a.name == real.name else a for a in parity.ACTIONS)
    failures = parity.check_parity(app, actions)
    assert any("read_group" in f and "no_such_handler" in f for f in failures)


def test_bus_backed_actions_name_registered_operations(app):
    backed = [a for a in parity.ACTIONS if a.internal_api]
    assert {a.internal_api for a in backed} == {
        "candidates.create", "events.publish",
    }
    # check_parity already verified each name is registered on
    # the B-162 bus (it imports the handlers to register them).


def test_parity_report_counts_and_pending_surfaces(app):
    report = parity.parity_report(app)
    assert report["parity_ok"] is True
    assert report["actions_registered"] == len(parity.ACTIONS)
    assert report["api_routes_live"] == len(parity.ACTIONS)
    assert report["internal_api_backed"] == 2
    text = parity.format_parity_report(report)
    assert "PASS" in text
    # Superseded by B-206: the MCP surface is no longer pending —
    # its leg of this checker landed and the report counts it as
    # covered (9 tools, 7 operations). The terminal surface is
    # still reported pending, never passed.
    # Grown by B-207: the groups/members/profile family brought
    # the surface to 14 tools covering 12 operations; B-208's
    # queue family to 19 tools / 17 operations; B-209's events
    # family to 25 tools / 23 operations; B-210's sources
    # family to 30 tools / 28 operations; B-211's planning
    # family to 36 tools / 34 operations; B-212's family to
    # 44 tools / 42 operations (B-216's bridge and B-217's
    # sampling policy each added one of each).
    assert report["mcp_tools"] == 44
    assert report["mcp_operations_covered"] == 42
    assert "MCP tool registry (B-206)" in text
    assert "Pending surface, not yet built: terminal (B-221)" in text
