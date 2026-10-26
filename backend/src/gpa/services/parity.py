"""API parity register + checker (roadmap B-201, Phase 19).

What this file is for
---------------------
Phase 19 opens with Volmarr's order (D-35/D-36, section 6A.9):
parity is total, and parity that is not tested is not parity.
This module is that test made into code:

* ``ACTIONS`` is the machine-readable register of every
  user-facing action the backend serves today -- one entry per
  live API operation (258 since B-202 added the seat profile
  doors). Each entry names the
  action, its external API route (method + path template), and
  the internal operation that backs it: the route handler that
  implements it, resolved by dotted path at check time.
* ``check_parity`` asserts, for every registered action, that
  (a) the external API route really exists on the live app and
  is served by the handler the register names, and (b) that
  handler really exists as a callable internal operation.
  It also asserts the reverse: a live route with no register
  entry is a failure too, so a new surface cannot quietly
  appear outside the register. A UI-only action -- a name in
  the register with no route and no handler behind it -- fails
  the checker BY NAME, which is this slice's acceptance test.
* ``parity_report`` is the report that ships with the test
  report: counts per surface, plus the honest pending lines.

Honest boundaries, stated at the point of use
---------------------------------------------
* Cross-module work already travels through the encrypted
  internal API (B-162): the seven registered operations there
  (``candidates.create``, ``events.publish``, ``notify.request``,
  ``notify.rsvp_promotion``, ``jobs.enqueue``, ``ai.submit_job``,
  ``integrations.emit``) are recorded on the actions they
  genuinely back in the ``internal_api`` field. The other
  actions are module-internal service operations behind their
  handlers; this register does NOT claim they travel the
  encrypted bus -- claiming that would be inventing coverage.
  B-207 registered five more operations (``groups.get``,
  ``groups.list_members``, ``groups.update``, ``profile.get``,
  ``profile.update``) for the MCP surface's domain tools,
  B-208 five more for the review queue (``candidates.list``,
  ``candidates.get``, ``candidates.approve``,
  ``candidates.reject``, ``candidates.edit``), B-209 six
  more for events (``events.list``, ``events.get``,
  ``events.cancel``, ``rsvps.list``, ``rsvps.set``,
  ``calendar.agenda``), B-210 five more for sources
  (``sources.list``, ``sources.get``, ``sources.create``,
  ``sources.append``, ``extraction.request``), and B-211 six
  more for planning (``planning.list_tasks``,
  ``planning.list_decisions``, ``planning.decision_chain``,
  ``planning.list_questions``, ``planning.update_task``,
  ``planning.resolve_question``), and B-212 six more for
  how the group hears from itself (``announcements.list``,
  ``announcements.create``, ``notifications.inbox``,
  ``notifications.preferences``, ``digests.list``,
  ``digests.preview``); they back no action in this
  register and are counted by the MCP leg below, not by
  the ``internal_api`` field here.
* MCP and the terminal (Phase 20) are the other surfaces.
  B-206 extended this same checker with its MCP leg (below):
  the generated tool registry in ``gpa.mcp.tools`` is checked
  against the internal operation registry — every operation
  has its tool, every tool its operation, scopes agree — and
  the report counts that surface as covered, with its failures
  named like any other leg's. The terminal (B-221) does not
  exist yet: it is reported pending, never reported passed.
* There is no separate UI codebase in this repository: the
  team's frontends are clients of this API (section 6A.10).
  A capability that existed only in a frontend would have no
  route here, and that is exactly what this checker catches
  when such a name is entered in the register.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from fastapi.routing import APIRoute


@dataclass(frozen=True)
class ParityAction:
    """One user-facing action and the doors that must serve it.

    ``handler`` is the internal operation in dotted form
    (``package.module:function``) -- the callable that backs
    the route. ``internal_api`` names a B-162 encrypted-bus
    operation when this action's cross-module work genuinely
    travels that bus, and is empty otherwise (see the header's
    honesty note -- empty is a fact, not a gap in this field).
    """

    name: str
    method: str
    path: str
    handler: str
    internal_api: str = ""


# The register. One entry per live API operation, in route
# order. Adding a route without adding its entry here fails
# the reverse check in ``check_parity``; adding an entry with
# no route behind it fails by name. Both directions are the
# point: the register and the app may not drift apart.
ACTIONS: tuple[ParityAction, ...] = (
    ParityAction("delete_account_link", "DELETE", "/api/v1/groups/{group_id}/account-links/{link_id}", "gpa.routers.integrations:delete_account_link", ""),
    ParityAction("delete_announcement", "DELETE", "/api/v1/groups/{group_id}/announcements/{announcement_id}", "gpa.routers.nonprofit:delete_announcement", ""),
    ParityAction("delete_comment", "DELETE", "/api/v1/groups/{group_id}/comments/{comment_id}", "gpa.routers.discussion:delete_comment", ""),
    ParityAction("delete_event", "DELETE", "/api/v1/groups/{group_id}/events/{event_id}", "gpa.routers.events:delete_event", ""),
    ParityAction("unmark_attendance", "DELETE", "/api/v1/groups/{group_id}/events/{event_id}/attendance/{user_id}", "gpa.routers.nonprofit:unmark_attendance", ""),
    ParityAction("facebook_unpublish", "DELETE", "/api/v1/groups/{group_id}/events/{event_id}/facebook", "gpa.routers.events:facebook_unpublish", ""),
    ParityAction("delete_recurrence", "DELETE", "/api/v1/groups/{group_id}/events/{event_id}/recurrence", "gpa.routers.events:delete_recurrence", ""),
    ParityAction("remove_rsvp", "DELETE", "/api/v1/groups/{group_id}/events/{event_id}/rsvp", "gpa.routers.rsvps:remove_rsvp", ""),
    ParityAction("delete_integration", "DELETE", "/api/v1/groups/{group_id}/integrations/{integration_id}", "gpa.routers.integrations:delete_integration", ""),
    ParityAction("remove_member", "DELETE", "/api/v1/groups/{group_id}/members/{user_id}", "gpa.routers.groups:remove_member", ""),
    ParityAction("purge_all_runs", "DELETE", "/api/v1/groups/{group_id}/memory/runs", "gpa.routers.memory:purge_all_runs", ""),
    ParityAction("purge_run", "DELETE", "/api/v1/groups/{group_id}/memory/runs/{run_id}", "gpa.routers.memory:purge_run", ""),
    ParityAction("delete_outbound_webhook", "DELETE", "/api/v1/groups/{group_id}/outbound-webhooks/{webhook_id}", "gpa.routers.integrations:delete_outbound_webhook", ""),
    ParityAction("unbook_resource", "DELETE", "/api/v1/groups/{group_id}/resource-bookings/{booking_id}", "gpa.routers.nonprofit:unbook_resource", ""),
    ParityAction("delete_resource", "DELETE", "/api/v1/groups/{group_id}/resources/{resource_id}", "gpa.routers.nonprofit:delete_resource", ""),
    ParityAction("delete_source", "DELETE", "/api/v1/groups/{group_id}/sources/{source_id}", "gpa.routers.sources:delete_source", ""),
    ParityAction("delete_subgroup", "DELETE", "/api/v1/groups/{group_id}/subgroups/{subgroup_id}", "gpa.routers.nonprofit:delete_subgroup", ""),
    ParityAction("unenroll_member", "DELETE", "/api/v1/groups/{group_id}/subgroups/{subgroup_id}/members/{user_id}", "gpa.routers.nonprofit:unenroll_member", ""),
    ParityAction("revoke_webhook_endpoint", "DELETE", "/api/v1/groups/{group_id}/webhook-endpoints/{endpoint_id}", "gpa.routers.integrations:revoke_webhook_endpoint", ""),
    ParityAction("revoke_api_key", "DELETE", "/api/v1/users/me/api-keys/{key_id}", "gpa.routers.users:revoke_api_key", ""),
    ParityAction("revoke_session", "DELETE", "/api/v1/users/me/sessions/{family_id}", "gpa.routers.users:revoke_session", ""),
    ParityAction("fetch_feed", "GET", "/api/v1/calendar/feed/{token}.ics", "gpa.routers.calendar:fetch_feed", ""),
    ParityAction("embed_page", "GET", "/api/v1/embed/groups/{slug}", "gpa.routers.calendar:embed_page", ""),
    ParityAction("read_group", "GET", "/api/v1/groups/{group_id}", "gpa.routers.groups:read_group", ""),
    ParityAction("list_account_links", "GET", "/api/v1/groups/{group_id}/account-links", "gpa.routers.integrations:list_account_links", ""),
    ParityAction("agenda", "GET", "/api/v1/groups/{group_id}/agenda", "gpa.routers.calendar:agenda", ""),
    ParityAction("list_announcements", "GET", "/api/v1/groups/{group_id}/announcements", "gpa.routers.nonprofit:list_announcements", ""),
    ParityAction("read_announcement", "GET", "/api/v1/groups/{group_id}/announcements/{announcement_id}", "gpa.routers.nonprofit:read_announcement", ""),
    ParityAction("attendance_summary", "GET", "/api/v1/groups/{group_id}/attendance/summary", "gpa.routers.nonprofit:attendance_summary", ""),
    ParityAction("read_audit", "GET", "/api/v1/groups/{group_id}/audit", "gpa.routers.groups:read_audit", ""),
    ParityAction("verify_audit_chain", "GET", "/api/v1/groups/{group_id}/audit/verify", "gpa.routers.groups:verify_audit_chain", ""),
    ParityAction("read_autosave", "GET", "/api/v1/groups/{group_id}/autosaves/{target_kind}/{target_id}", "gpa.routers.uploads:read_autosave", ""),
    ParityAction("ranked_availability", "GET", "/api/v1/groups/{group_id}/availability", "gpa.routers.polls:ranked_availability", ""),
    ParityAction("list_feeds", "GET", "/api/v1/groups/{group_id}/calendar/feeds", "gpa.routers.calendar:list_feeds", ""),
    ParityAction("list_candidates", "GET", "/api/v1/groups/{group_id}/candidates", "gpa.routers.candidates:list_candidates", ""),
    ParityAction("queue_status", "GET", "/api/v1/groups/{group_id}/candidates/status", "gpa.routers.candidates:queue_status", ""),
    ParityAction("get_candidate", "GET", "/api/v1/groups/{group_id}/candidates/{candidate_id}", "gpa.routers.candidates:get_candidate", ""),
    ParityAction("list_parent_comments", "GET", "/api/v1/groups/{group_id}/comments/{parent_type}/{parent_id}", "gpa.routers.discussion:list_parent_comments", ""),
    ParityAction("conflicts", "GET", "/api/v1/groups/{group_id}/conflicts", "gpa.routers.calendar:conflicts", ""),
    ParityAction("list_decisions", "GET", "/api/v1/groups/{group_id}/decisions", "gpa.routers.planning:list_decisions", ""),
    ParityAction("get_decision", "GET", "/api/v1/groups/{group_id}/decisions/{decision_id}", "gpa.routers.planning:get_decision", ""),
    ParityAction("get_decision_chain", "GET", "/api/v1/groups/{group_id}/decisions/{decision_id}/chain", "gpa.routers.planning:get_decision_chain", ""),
    ParityAction("list_digests", "GET", "/api/v1/groups/{group_id}/digests", "gpa.routers.notifications:list_digests", ""),
    ParityAction("list_embed_origins", "GET", "/api/v1/groups/{group_id}/embed-origins", "gpa.routers.integrations:list_embed_origins", ""),
    ParityAction("list_events", "GET", "/api/v1/groups/{group_id}/events", "gpa.routers.events:list_events", ""),
    ParityAction("get_event", "GET", "/api/v1/groups/{group_id}/events/{event_id}", "gpa.routers.events:get_event", ""),
    ParityAction("read_event_attendance", "GET", "/api/v1/groups/{group_id}/events/{event_id}/attendance", "gpa.routers.nonprofit:read_event_attendance", ""),
    ParityAction("get_budget", "GET", "/api/v1/groups/{group_id}/events/{event_id}/budget", "gpa.routers.checklists:get_budget", ""),
    ParityAction("list_changes", "GET", "/api/v1/groups/{group_id}/events/{event_id}/changes", "gpa.routers.events:list_changes", ""),
    ParityAction("list_checklist_items", "GET", "/api/v1/groups/{group_id}/events/{event_id}/checklist", "gpa.routers.checklists:list_checklist_items", ""),
    ParityAction("list_event_comments", "GET", "/api/v1/groups/{group_id}/events/{event_id}/comments", "gpa.routers.discussion:list_event_comments", ""),
    ParityAction("list_contradictions", "GET", "/api/v1/groups/{group_id}/events/{event_id}/contradictions", "gpa.routers.events:list_contradictions", ""),
    ParityAction("list_cost_lines", "GET", "/api/v1/groups/{group_id}/events/{event_id}/costs", "gpa.routers.checklists:list_cost_lines", ""),
    ParityAction("facebook_state", "GET", "/api/v1/groups/{group_id}/events/{event_id}/facebook", "gpa.routers.events:facebook_state", ""),
    ParityAction("facebook_draft", "GET", "/api/v1/groups/{group_id}/events/{event_id}/facebook-draft", "gpa.routers.events:facebook_draft", ""),
    ParityAction("get_event_gaps", "GET", "/api/v1/groups/{group_id}/events/{event_id}/gaps", "gpa.routers.planning:get_event_gaps", ""),
    ParityAction("download_event_ics", "GET", "/api/v1/groups/{group_id}/events/{event_id}/ics", "gpa.routers.calendar:download_event_ics", ""),
    ParityAction("list_invites", "GET", "/api/v1/groups/{group_id}/events/{event_id}/invites", "gpa.routers.invites:list_invites", ""),
    ParityAction("list_occurrences", "GET", "/api/v1/groups/{group_id}/events/{event_id}/occurrences", "gpa.routers.events:list_occurrences", ""),
    ParityAction("list_reannouncement_drafts", "GET", "/api/v1/groups/{group_id}/events/{event_id}/reannounce-drafts", "gpa.routers.events:list_reannouncement_drafts", ""),
    ParityAction("get_recurrence", "GET", "/api/v1/groups/{group_id}/events/{event_id}/recurrence", "gpa.routers.events:get_recurrence", ""),
    ParityAction("list_event_bookings", "GET", "/api/v1/groups/{group_id}/events/{event_id}/resource-bookings", "gpa.routers.nonprofit:list_event_bookings", ""),
    ParityAction("list_role_slots", "GET", "/api/v1/groups/{group_id}/events/{event_id}/roles", "gpa.routers.checklists:list_role_slots", ""),
    ParityAction("list_rsvps", "GET", "/api/v1/groups/{group_id}/events/{event_id}/rsvps", "gpa.routers.rsvps:list_rsvps", ""),
    ParityAction("list_shifts", "GET", "/api/v1/groups/{group_id}/events/{event_id}/shifts", "gpa.routers.nonprofit:list_shifts", ""),
    ParityAction("list_event_sources", "GET", "/api/v1/groups/{group_id}/events/{event_id}/sources", "gpa.routers.events:list_event_sources", ""),
    ParityAction("list_event_tasks", "GET", "/api/v1/groups/{group_id}/events/{event_id}/tasks", "gpa.routers.planning:list_event_tasks", ""),
    ParityAction("flyer_wall", "GET", "/api/v1/groups/{group_id}/flyers", "gpa.routers.flyers:flyer_wall", ""),
    ParityAction("list_integrations", "GET", "/api/v1/groups/{group_id}/integrations", "gpa.routers.integrations:list_integrations", ""),
    ParityAction("integration_health_board", "GET", "/api/v1/groups/{group_id}/integrations/health-board", "gpa.routers.integrations:integration_health_board", ""),
    ParityAction("read_integration", "GET", "/api/v1/groups/{group_id}/integrations/{integration_id}", "gpa.routers.integrations:read_integration", ""),
    ParityAction("list_members", "GET", "/api/v1/groups/{group_id}/members", "gpa.routers.groups:list_members", ""),
    ParityAction("export_members", "GET", "/api/v1/groups/{group_id}/members/export", "gpa.routers.nonprofit:export_members", ""),
    ParityAction("list_runs", "GET", "/api/v1/groups/{group_id}/memory/runs", "gpa.routers.memory:list_runs", ""),
    ParityAction("month", "GET", "/api/v1/groups/{group_id}/month", "gpa.routers.calendar:month", ""),
    ParityAction("get_failures", "GET", "/api/v1/groups/{group_id}/notification-failures", "gpa.routers.notifications:get_failures", ""),
    ParityAction("list_outbound_webhooks", "GET", "/api/v1/groups/{group_id}/outbound-webhooks", "gpa.routers.integrations:list_outbound_webhooks", ""),
    ParityAction("list_outbound_deliveries", "GET", "/api/v1/groups/{group_id}/outbound-webhooks/{webhook_id}/deliveries", "gpa.routers.integrations:list_outbound_deliveries", ""),
    ParityAction("planning_digest", "GET", "/api/v1/groups/{group_id}/planning-digest", "gpa.routers.planning:planning_digest", ""),
    ParityAction("list_polls", "GET", "/api/v1/groups/{group_id}/polls", "gpa.routers.polls:list_polls", ""),
    ParityAction("get_poll", "GET", "/api/v1/groups/{group_id}/polls/{poll_id}", "gpa.routers.polls:get_poll", ""),
    ParityAction("print_month", "GET", "/api/v1/groups/{group_id}/print/month", "gpa.routers.flyers:print_month", ""),
    ParityAction("quality_report", "GET", "/api/v1/groups/{group_id}/quality-report", "gpa.routers.truth:quality_report", ""),
    ParityAction("list_questions", "GET", "/api/v1/groups/{group_id}/questions", "gpa.routers.planning:list_questions", ""),
    ParityAction("get_question", "GET", "/api/v1/groups/{group_id}/questions/{question_id}", "gpa.routers.planning:get_question", ""),
    ParityAction("list_resources", "GET", "/api/v1/groups/{group_id}/resources", "gpa.routers.nonprofit:list_resources", ""),
    ParityAction("search", "GET", "/api/v1/groups/{group_id}/search", "gpa.routers.planning:search", ""),
    ParityAction("list_seats", "GET", "/api/v1/groups/{group_id}/seats", "gpa.routers.groups:list_seats", ""),
    ParityAction("read_seat", "GET", "/api/v1/groups/{group_id}/seats/{seat_id}", "gpa.routers.groups:read_seat", ""),
    ParityAction("update_seat_profile", "PATCH", "/api/v1/groups/{group_id}/seats/{seat_id}", "gpa.routers.groups:update_seat_profile", ""),
    ParityAction("read_settings", "GET", "/api/v1/groups/{group_id}/settings", "gpa.routers.nonprofit:read_settings", ""),
    ParityAction("shift_gaps", "GET", "/api/v1/groups/{group_id}/shift-gaps", "gpa.routers.nonprofit:shift_gaps", ""),
    ParityAction("list_sources", "GET", "/api/v1/groups/{group_id}/sources", "gpa.routers.sources:list_sources", ""),
    ParityAction("source_permissions", "GET", "/api/v1/groups/{group_id}/sources/permissions", "gpa.routers.privacy:source_permissions", ""),
    ParityAction("read_source", "GET", "/api/v1/groups/{group_id}/sources/{source_id}", "gpa.routers.sources:read_source", ""),
    ParityAction("list_speakers", "GET", "/api/v1/groups/{group_id}/sources/{source_id}/speakers", "gpa.routers.integrations:list_speakers", ""),
    ParityAction("group_stream", "GET", "/api/v1/groups/{group_id}/stream", "gpa.routers.sources:group_stream", ""),
    ParityAction("list_subgroups", "GET", "/api/v1/groups/{group_id}/subgroups", "gpa.routers.nonprofit:list_subgroups", ""),
    ParityAction("read_subgroup", "GET", "/api/v1/groups/{group_id}/subgroups/{subgroup_id}", "gpa.routers.nonprofit:read_subgroup", ""),
    ParityAction("list_subgroup_members", "GET", "/api/v1/groups/{group_id}/subgroups/{subgroup_id}/members", "gpa.routers.nonprofit:list_subgroup_members", ""),
    ParityAction("list_summaries", "GET", "/api/v1/groups/{group_id}/summaries", "gpa.routers.memory:list_summaries", ""),
    ParityAction("merged_supplies", "GET", "/api/v1/groups/{group_id}/supplies", "gpa.routers.checklists:merged_supplies", ""),
    ParityAction("list_tasks", "GET", "/api/v1/groups/{group_id}/tasks", "gpa.routers.planning:list_tasks", ""),
    ParityAction("get_task", "GET", "/api/v1/groups/{group_id}/tasks/{task_id}", "gpa.routers.planning:get_task", ""),
    ParityAction("list_templates", "GET", "/api/v1/groups/{group_id}/templates", "gpa.routers.templates:list_templates", ""),
    ParityAction("get_template", "GET", "/api/v1/groups/{group_id}/templates/{template_id}", "gpa.routers.templates:get_template", ""),
    ParityAction("list_tombstones", "GET", "/api/v1/groups/{group_id}/tombstones", "gpa.routers.truth:list_tombstones", ""),
    ParityAction("tonight", "GET", "/api/v1/groups/{group_id}/tonight", "gpa.routers.calendar:tonight", ""),
    ParityAction("upload_status", "GET", "/api/v1/groups/{group_id}/uploads/{upload_id}", "gpa.routers.uploads:upload_status", ""),
    ParityAction("list_webhook_endpoints", "GET", "/api/v1/groups/{group_id}/webhook-endpoints", "gpa.routers.integrations:list_webhook_endpoints", ""),
    ParityAction("view_invite", "GET", "/api/v1/invites/{token}", "gpa.routers.invites:view_invite", ""),
    ParityAction("read_job", "GET", "/api/v1/jobs/{job_id}", "gpa.routers.jobs:read_job", ""),
    ParityAction("meta", "GET", "/api/v1/meta", "gpa.routers.meta:meta", ""),
    ParityAction("public_page", "GET", "/api/v1/public/groups/{slug}", "gpa.routers.calendar:public_page", ""),
    ParityAction("public_events", "GET", "/api/v1/public/groups/{slug}/events", "gpa.routers.calendar:public_events", ""),
    ParityAction("me", "GET", "/api/v1/users/me", "gpa.routers.users:me", ""),
    ParityAction("list_api_keys", "GET", "/api/v1/users/me/api-keys", "gpa.routers.users:list_api_keys", ""),
    ParityAction("my_data", "GET", "/api/v1/users/me/data", "gpa.routers.users:my_data", ""),
    ParityAction("get_preferences", "GET", "/api/v1/users/me/groups/{group_id}/notification-preferences", "gpa.routers.notifications:get_preferences", ""),
    ParityAction("get_inbox", "GET", "/api/v1/users/me/inbox", "gpa.routers.notifications:get_inbox", ""),
    ParityAction("mfa_status", "GET", "/api/v1/users/me/mfa", "gpa.routers.users:mfa_status", ""),
    ParityAction("list_sessions", "GET", "/api/v1/users/me/sessions", "gpa.routers.users:list_sessions", ""),
    ParityAction("health", "GET", "/health", "gpa.routers.meta:health", ""),
    ParityAction("update_group", "PATCH", "/api/v1/groups/{group_id}", "gpa.routers.groups:update_group", ""),
    ParityAction("update_announcement", "PATCH", "/api/v1/groups/{group_id}/announcements/{announcement_id}", "gpa.routers.nonprofit:update_announcement", ""),
    ParityAction("edit_candidate", "PATCH", "/api/v1/groups/{group_id}/candidates/{candidate_id}", "gpa.routers.candidates:edit_candidate", ""),
    ParityAction("update_checklist_item", "PATCH", "/api/v1/groups/{group_id}/checklist/{item_id}", "gpa.routers.checklists:update_checklist_item", ""),
    ParityAction("edit_comment", "PATCH", "/api/v1/groups/{group_id}/comments/{comment_id}", "gpa.routers.discussion:edit_comment", ""),
    ParityAction("update_event", "PATCH", "/api/v1/groups/{group_id}/events/{event_id}", "gpa.routers.events:update_event", ""),
    ParityAction("update_alt_text", "PATCH", "/api/v1/groups/{group_id}/flyers/{flyer_id}", "gpa.routers.flyers:update_alt_text", ""),
    ParityAction("update_integration", "PATCH", "/api/v1/groups/{group_id}/integrations/{integration_id}", "gpa.routers.integrations:update_integration", ""),
    ParityAction("change_role", "PATCH", "/api/v1/groups/{group_id}/members/{user_id}/role", "gpa.routers.groups:change_role", ""),
    ParityAction("update_question", "PATCH", "/api/v1/groups/{group_id}/questions/{question_id}", "gpa.routers.planning:update_question", ""),
    ParityAction("update_resource", "PATCH", "/api/v1/groups/{group_id}/resources/{resource_id}", "gpa.routers.nonprofit:update_resource", ""),
    ParityAction("update_subgroup", "PATCH", "/api/v1/groups/{group_id}/subgroups/{subgroup_id}", "gpa.routers.nonprofit:update_subgroup", ""),
    ParityAction("update_task", "PATCH", "/api/v1/groups/{group_id}/tasks/{task_id}", "gpa.routers.planning:update_task", ""),
    ParityAction("update_me", "PATCH", "/api/v1/users/me", "gpa.routers.users:update_me", ""),
    ParityAction("login", "POST", "/api/v1/auth/login", "gpa.routers.auth:login", ""),
    ParityAction("login_mfa", "POST", "/api/v1/auth/login/mfa", "gpa.routers.auth:login_mfa", ""),
    ParityAction("logout", "POST", "/api/v1/auth/logout", "gpa.routers.auth:logout", ""),
    ParityAction("magic_link", "POST", "/api/v1/auth/magic-link", "gpa.routers.auth:magic_link", ""),
    ParityAction("magic_link_consume", "POST", "/api/v1/auth/magic-link/consume", "gpa.routers.auth:magic_link_consume", ""),
    ParityAction("password_reset", "POST", "/api/v1/auth/password-reset", "gpa.routers.auth:password_reset", ""),
    ParityAction("password_reset_confirm", "POST", "/api/v1/auth/password-reset/confirm", "gpa.routers.auth:password_reset_confirm", ""),
    ParityAction("refresh", "POST", "/api/v1/auth/refresh", "gpa.routers.auth:refresh", ""),
    ParityAction("register", "POST", "/api/v1/auth/register", "gpa.routers.auth:register", ""),
    ParityAction("receive_email", "POST", "/api/v1/email-in/{token}", "gpa.routers.sources:receive_email", ""),
    ParityAction("create_group", "POST", "/api/v1/groups", "gpa.routers.groups:create_group", ""),
    ParityAction("create_account_link", "POST", "/api/v1/groups/{group_id}/account-links", "gpa.routers.integrations:create_account_link", ""),
    ParityAction("create_announcement", "POST", "/api/v1/groups/{group_id}/announcements", "gpa.routers.nonprofit:create_announcement", ""),
    ParityAction("ask", "POST", "/api/v1/groups/{group_id}/ask", "gpa.routers.memory:ask", ""),
    ParityAction("propose_slot", "POST", "/api/v1/groups/{group_id}/availability/slots", "gpa.routers.polls:propose_slot", ""),
    ParityAction("mark_slot", "POST", "/api/v1/groups/{group_id}/availability/slots/{slot_id}/mark", "gpa.routers.polls:mark_slot", ""),
    ParityAction("issue_feed", "POST", "/api/v1/groups/{group_id}/calendar/feeds", "gpa.routers.calendar:issue_feed", ""),
    ParityAction("revoke_feed", "POST", "/api/v1/groups/{group_id}/calendar/feeds/{feed_id}/revoke", "gpa.routers.calendar:revoke_feed", ""),
    ParityAction("create_candidate", "POST", "/api/v1/groups/{group_id}/candidates", "gpa.routers.groups:create_candidate", "candidates.create"),
    ParityAction("approve_candidate", "POST", "/api/v1/groups/{group_id}/candidates/{candidate_id}/approve", "gpa.routers.groups:approve_candidate", ""),
    ParityAction("merge_candidate", "POST", "/api/v1/groups/{group_id}/candidates/{candidate_id}/merge", "gpa.routers.candidates:merge_candidate", ""),
    ParityAction("merge_candidate_into_event", "POST", "/api/v1/groups/{group_id}/candidates/{candidate_id}/merge-event", "gpa.routers.candidates:merge_candidate_into_event", ""),
    ParityAction("reject_candidate", "POST", "/api/v1/groups/{group_id}/candidates/{candidate_id}/reject", "gpa.routers.candidates:reject_candidate", ""),
    ParityAction("claim_checklist_item", "POST", "/api/v1/groups/{group_id}/checklist/{item_id}/claim", "gpa.routers.checklists:claim_checklist_item", ""),
    ParityAction("unclaim_checklist_item", "POST", "/api/v1/groups/{group_id}/checklist/{item_id}/unclaim", "gpa.routers.checklists:unclaim_checklist_item", ""),
    ParityAction("hide_comment", "POST", "/api/v1/groups/{group_id}/comments/{comment_id}/hide", "gpa.routers.discussion:hide_comment", ""),
    ParityAction("unhide_comment", "POST", "/api/v1/groups/{group_id}/comments/{comment_id}/unhide", "gpa.routers.discussion:unhide_comment", ""),
    ParityAction("post_parent_comment", "POST", "/api/v1/groups/{group_id}/comments/{parent_type}/{parent_id}", "gpa.routers.discussion:post_parent_comment", ""),
    ParityAction("create_decision", "POST", "/api/v1/groups/{group_id}/decisions", "gpa.routers.planning:create_decision", ""),
    ParityAction("run_digest_now", "POST", "/api/v1/groups/{group_id}/digest/run", "gpa.routers.notifications:run_digest_now", ""),
    ParityAction("erase_group_route", "POST", "/api/v1/groups/{group_id}/erase", "gpa.routers.privacy:erase_group_route", ""),
    ParityAction("create_event", "POST", "/api/v1/groups/{group_id}/events", "gpa.routers.events:create_event", ""),
    ParityAction("mark_attendance", "POST", "/api/v1/groups/{group_id}/events/{event_id}/attendance", "gpa.routers.nonprofit:mark_attendance", ""),
    ParityAction("brief_event", "POST", "/api/v1/groups/{group_id}/events/{event_id}/briefing", "gpa.routers.memory:brief_event", ""),
    ParityAction("cancel_event", "POST", "/api/v1/groups/{group_id}/events/{event_id}/cancel", "gpa.routers.events:cancel_event", ""),
    ParityAction("add_checklist_item", "POST", "/api/v1/groups/{group_id}/events/{event_id}/checklist", "gpa.routers.checklists:add_checklist_item", ""),
    ParityAction("clone_event", "POST", "/api/v1/groups/{group_id}/events/{event_id}/clone", "gpa.routers.events:clone_event", ""),
    ParityAction("post_event_comment", "POST", "/api/v1/groups/{group_id}/events/{event_id}/comments", "gpa.routers.discussion:post_event_comment", ""),
    ParityAction("resolve_contradiction", "POST", "/api/v1/groups/{group_id}/events/{event_id}/contradictions/{contradiction_id}/resolve", "gpa.routers.events:resolve_contradiction", ""),
    ParityAction("add_cost_line", "POST", "/api/v1/groups/{group_id}/events/{event_id}/costs", "gpa.routers.checklists:add_cost_line", ""),
    ParityAction("facebook_publish", "POST", "/api/v1/groups/{group_id}/events/{event_id}/facebook", "gpa.routers.events:facebook_publish", ""),
    ParityAction("generate_image", "POST", "/api/v1/groups/{group_id}/events/{event_id}/generate-image", "gpa.routers.events:generate_image", ""),
    ParityAction("create_invite", "POST", "/api/v1/groups/{group_id}/events/{event_id}/invites", "gpa.routers.invites:create_invite", ""),
    ParityAction("publish_event", "POST", "/api/v1/groups/{group_id}/events/{event_id}/publish", "gpa.routers.events:publish_event", "events.publish"),
    ParityAction("draft_reannouncement", "POST", "/api/v1/groups/{group_id}/events/{event_id}/reannounce-drafts", "gpa.routers.events:draft_reannouncement", ""),
    ParityAction("approve_reannouncement", "POST", "/api/v1/groups/{group_id}/events/{event_id}/reannounce-drafts/{draft_id}/approve", "gpa.routers.events:approve_reannouncement", ""),
    ParityAction("recap_event", "POST", "/api/v1/groups/{group_id}/events/{event_id}/recap", "gpa.routers.memory:recap_event", ""),
    ParityAction("add_exception", "POST", "/api/v1/groups/{group_id}/events/{event_id}/recurrence/exceptions", "gpa.routers.events:add_exception", ""),
    ParityAction("book_resource", "POST", "/api/v1/groups/{group_id}/events/{event_id}/resource-bookings", "gpa.routers.nonprofit:book_resource", ""),
    ParityAction("restore_event", "POST", "/api/v1/groups/{group_id}/events/{event_id}/restore", "gpa.routers.events:restore_event", ""),
    ParityAction("open_role_slots", "POST", "/api/v1/groups/{group_id}/events/{event_id}/roles", "gpa.routers.checklists:open_role_slots", ""),
    ParityAction("open_shift", "POST", "/api/v1/groups/{group_id}/events/{event_id}/shifts", "gpa.routers.nonprofit:open_shift", ""),
    ParityAction("export_group", "POST", "/api/v1/groups/{group_id}/export", "gpa.routers.privacy:export_group", ""),
    ParityAction("create_inbound_address", "POST", "/api/v1/groups/{group_id}/inbound-address", "gpa.routers.sources:create_inbound_address", ""),
    ParityAction("create_integration", "POST", "/api/v1/groups/{group_id}/integrations", "gpa.routers.integrations:create_integration", ""),
    ParityAction("reconcile_now", "POST", "/api/v1/groups/{group_id}/integrations/reconcile", "gpa.routers.integrations:reconcile_now", ""),
    ParityAction("connect_google", "POST", "/api/v1/groups/{group_id}/integrations/{integration_id}/google-connect", "gpa.routers.integrations:connect_google", ""),
    ParityAction("pull_google", "POST", "/api/v1/groups/{group_id}/integrations/{integration_id}/google-pull", "gpa.routers.integrations:pull_google", ""),
    ParityAction("sync_google", "POST", "/api/v1/groups/{group_id}/integrations/{integration_id}/google-sync", "gpa.routers.integrations:sync_google", ""),
    ParityAction("check_integration_health", "POST", "/api/v1/groups/{group_id}/integrations/{integration_id}/health", "gpa.routers.integrations:check_integration_health", ""),
    ParityAction("invite_member", "POST", "/api/v1/groups/{group_id}/invites", "gpa.routers.groups:invite_member", ""),
    ParityAction("accept_invite", "POST", "/api/v1/groups/{group_id}/invites/accept", "gpa.routers.groups:accept_invite", ""),
    ParityAction("decline_invite", "POST", "/api/v1/groups/{group_id}/invites/decline", "gpa.routers.groups:decline_invite", ""),
    ParityAction("leave_group", "POST", "/api/v1/groups/{group_id}/leave", "gpa.routers.groups:leave_group", ""),
    ParityAction("import_members", "POST", "/api/v1/groups/{group_id}/members/import", "gpa.routers.nonprofit:import_members", ""),
    ParityAction("draft_newsletter", "POST", "/api/v1/groups/{group_id}/newsletters/draft", "gpa.routers.memory:draft_newsletter", ""),
    ParityAction("resend_notification", "POST", "/api/v1/groups/{group_id}/notifications/{notification_id}/resend", "gpa.routers.notifications:resend_notification", ""),
    ParityAction("create_outbound_webhook", "POST", "/api/v1/groups/{group_id}/outbound-webhooks", "gpa.routers.integrations:create_outbound_webhook", ""),
    ParityAction("create_poll", "POST", "/api/v1/groups/{group_id}/polls", "gpa.routers.polls:create_poll", ""),
    ParityAction("apply_winner", "POST", "/api/v1/groups/{group_id}/polls/{poll_id}/apply-winner", "gpa.routers.polls:apply_winner", ""),
    ParityAction("close_poll", "POST", "/api/v1/groups/{group_id}/polls/{poll_id}/close", "gpa.routers.polls:close_poll", ""),
    ParityAction("cast_vote", "POST", "/api/v1/groups/{group_id}/polls/{poll_id}/vote", "gpa.routers.polls:cast_vote", ""),
    ParityAction("create_question", "POST", "/api/v1/groups/{group_id}/questions", "gpa.routers.planning:create_question", ""),
    ParityAction("resolve_question", "POST", "/api/v1/groups/{group_id}/questions/{question_id}/resolve", "gpa.routers.planning:resolve_question", ""),
    ParityAction("create_resource", "POST", "/api/v1/groups/{group_id}/resources", "gpa.routers.nonprofit:create_resource", ""),
    ParityAction("run_retention", "POST", "/api/v1/groups/{group_id}/retention/run", "gpa.routers.privacy:run_retention", ""),
    ParityAction("claim_role_slot", "POST", "/api/v1/groups/{group_id}/roles/{slot_id}/claim", "gpa.routers.checklists:claim_role_slot", ""),
    ParityAction("unclaim_role_slot", "POST", "/api/v1/groups/{group_id}/roles/{slot_id}/unclaim", "gpa.routers.checklists:unclaim_role_slot", ""),
    ParityAction("create_seat", "POST", "/api/v1/groups/{group_id}/seats", "gpa.routers.groups:create_seat", ""),
    ParityAction("create_source", "POST", "/api/v1/groups/{group_id}/sources", "gpa.routers.sources:create_source", ""),
    ParityAction("upload_image", "POST", "/api/v1/groups/{group_id}/sources/image", "gpa.routers.sources:upload_image", ""),
    ParityAction("ingest_link", "POST", "/api/v1/groups/{group_id}/sources/link", "gpa.routers.sources:ingest_link", ""),
    ParityAction("register_live_source", "POST", "/api/v1/groups/{group_id}/sources/live", "gpa.routers.sources:register_live_source", ""),
    ParityAction("upload_newsletter", "POST", "/api/v1/groups/{group_id}/sources/newsletter", "gpa.routers.sources:upload_newsletter", ""),
    ParityAction("paste_source", "POST", "/api/v1/groups/{group_id}/sources/paste", "gpa.routers.sources:paste_source", ""),
    ParityAction("upload_source", "POST", "/api/v1/groups/{group_id}/sources/upload", "gpa.routers.sources:upload_source", ""),
    ParityAction("ingest_voice", "POST", "/api/v1/groups/{group_id}/sources/voice", "gpa.routers.sources:ingest_voice", ""),
    ParityAction("sync_samtal_calendar", "POST", "/api/v1/groups/{group_id}/sources/{source_id}/calendar-sync", "gpa.routers.integrations:sync_samtal_calendar", ""),
    ParityAction("request_extraction", "POST", "/api/v1/groups/{group_id}/sources/{source_id}/extract", "gpa.routers.candidates:request_extraction", ""),
    ParityAction("request_vision_read", "POST", "/api/v1/groups/{group_id}/sources/{source_id}/read", "gpa.routers.candidates:request_vision_read", ""),
    ParityAction("restore_source", "POST", "/api/v1/groups/{group_id}/sources/{source_id}/restore", "gpa.routers.sources:restore_source", ""),
    ParityAction("sync_live_source", "POST", "/api/v1/groups/{group_id}/sources/{source_id}/sync", "gpa.routers.integrations:sync_live_source", ""),
    ParityAction("create_subgroup", "POST", "/api/v1/groups/{group_id}/subgroups", "gpa.routers.nonprofit:create_subgroup", ""),
    ParityAction("enroll_member", "POST", "/api/v1/groups/{group_id}/subgroups/{subgroup_id}/members", "gpa.routers.nonprofit:enroll_member", ""),
    ParityAction("summarise_discussion", "POST", "/api/v1/groups/{group_id}/summaries/discussion", "gpa.routers.memory:summarise_discussion", ""),
    ParityAction("approve_summary", "POST", "/api/v1/groups/{group_id}/summaries/{summary_id}/approve", "gpa.routers.memory:approve_summary", ""),
    ParityAction("create_task", "POST", "/api/v1/groups/{group_id}/tasks", "gpa.routers.planning:create_task", ""),
    ParityAction("create_template", "POST", "/api/v1/groups/{group_id}/templates", "gpa.routers.templates:create_template", ""),
    ParityAction("template_from_event", "POST", "/api/v1/groups/{group_id}/templates/from-event/{event_id}", "gpa.routers.templates:template_from_event", ""),
    ParityAction("instantiate_template", "POST", "/api/v1/groups/{group_id}/templates/{template_id}/instantiate", "gpa.routers.templates:instantiate_template", ""),
    ParityAction("begin_upload", "POST", "/api/v1/groups/{group_id}/uploads", "gpa.routers.uploads:begin_upload", ""),
    ParityAction("complete_upload", "POST", "/api/v1/groups/{group_id}/uploads/{upload_id}/complete", "gpa.routers.uploads:complete_upload", ""),
    ParityAction("create_webhook_endpoint", "POST", "/api/v1/groups/{group_id}/webhook-endpoints", "gpa.routers.integrations:create_webhook_endpoint", ""),
    ParityAction("respond_invite", "POST", "/api/v1/invites/{token}/rsvp", "gpa.routers.invites:respond_invite", ""),
    ParityAction("create_api_key", "POST", "/api/v1/users/me/api-keys", "gpa.routers.users:create_api_key", ""),
    ParityAction("request_email_change", "POST", "/api/v1/users/me/email-change", "gpa.routers.users:request_email_change", ""),
    ParityAction("confirm_email_change", "POST", "/api/v1/users/me/email-change/confirm", "gpa.routers.users:confirm_email_change", ""),
    ParityAction("mark_inbox_read", "POST", "/api/v1/users/me/inbox/{notification_id}/read", "gpa.routers.notifications:mark_inbox_read", ""),
    ParityAction("mfa_confirm", "POST", "/api/v1/users/me/mfa/confirm", "gpa.routers.users:mfa_confirm", ""),
    ParityAction("mfa_disable", "POST", "/api/v1/users/me/mfa/disable", "gpa.routers.users:mfa_disable", ""),
    ParityAction("mfa_setup", "POST", "/api/v1/users/me/mfa/setup", "gpa.routers.users:mfa_setup", ""),
    ParityAction("change_password", "POST", "/api/v1/users/me/password", "gpa.routers.users:change_password", ""),
    ParityAction("mail_webhook", "POST", "/api/v1/webhooks/mail/{provider}", "gpa.routers.notifications:mail_webhook", ""),
    ParityAction("receive_webhook_delivery", "POST", "/api/v1/webhooks/source/{token}", "gpa.routers.integrations:receive_webhook_delivery", ""),
    ParityAction("save_autosave", "PUT", "/api/v1/groups/{group_id}/autosaves/{target_kind}/{target_id}", "gpa.routers.uploads:save_autosave", ""),
    ParityAction("set_embed_origins", "PUT", "/api/v1/groups/{group_id}/embed-origins", "gpa.routers.integrations:set_embed_origins", ""),
    ParityAction("set_recurrence", "PUT", "/api/v1/groups/{group_id}/events/{event_id}/recurrence", "gpa.routers.events:set_recurrence", ""),
    ParityAction("set_rsvp", "PUT", "/api/v1/groups/{group_id}/events/{event_id}/rsvp", "gpa.routers.rsvps:set_rsvp", ""),
    ParityAction("update_settings", "PUT", "/api/v1/groups/{group_id}/settings", "gpa.routers.nonprofit:update_settings", ""),
    ParityAction("upload_part", "PUT", "/api/v1/groups/{group_id}/uploads/{upload_id}/parts/{index}", "gpa.routers.uploads:upload_part", ""),
    ParityAction("put_preferences", "PUT", "/api/v1/users/me/groups/{group_id}/notification-preferences", "gpa.routers.notifications:put_preferences", ""),
)

# The surfaces still to come to this same register. Listed
# here so the report can name them as pending rather than stay
# silent about them (see the header). MCP left this list at
# B-206, when its leg of the checker landed below.
PENDING_SURFACES: tuple[str, ...] = ("terminal (B-221)",)


def check_mcp_parity() -> list[str]:
    """The B-206 MCP leg of this checker, as failure sentences.

    Delegates to the generated tool registry's own checker
    (``gpa.mcp.tools.check_tool_registry``) and prefixes each
    failure ``mcp:`` so a report reader can tell which surface
    refused. Imported lazily: the registry module registers the
    internal handlers (which import services), and a top-level
    import here would close that circle at module load.
    """
    from gpa.mcp.tools import check_tool_registry

    return [f"mcp: {failure}" for failure in check_tool_registry()]


def _live_routes(app) -> dict[tuple[str, str], str]:
    """Every API route the live app serves, as a map from
    (method, path template) to its handler's dotted path.

    The walk mirrors ``agent_pack._all_api_routes``: this
    FastAPI version keeps included routers lazy, so the walk
    looks through them to the real route objects -- the same
    objects the OpenAPI builder reads, so what is checked here
    is what the contract promises.
    """
    found: dict[tuple[str, str], str] = {}
    for entry in app.routes:
        routes = [entry] if isinstance(entry, APIRoute) else []
        router = getattr(entry, "original_router", None)
        if router is not None:
            routes = [r for r in router.routes if isinstance(r, APIRoute)]
        for route in routes:
            dotted = f"{route.endpoint.__module__}:{route.endpoint.__name__}"
            for method in route.methods or set():
                if method in ("HEAD", "OPTIONS"):
                    continue
                found[(method, route.path)] = dotted
    return found


def _resolves(dotted: str) -> bool:
    """True when a dotted ``module:function`` path resolves to
    a callable -- the internal-operation leg of the check."""
    module_name, _, func_name = dotted.partition(":")
    if not module_name or not func_name:
        return False
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return False
    return callable(getattr(module, func_name, None))


def check_parity(app, actions: tuple[ParityAction, ...] = ACTIONS) -> list[str]:
    """Every parity failure, each naming its action.

    An empty list is parity. A deliberately UI-only fixture
    action (a register entry whose route and handler do not
    exist) appears here by name -- the B-201 acceptance test
    pins exactly that.
    """
    failures: list[str] = []
    live = _live_routes(app)
    registered: set[tuple[str, str]] = set()
    for action in actions:
        key = (action.method, action.path)
        registered.add(key)
        # Leg (a): the external API route must exist, and the
        # live app must serve it with the registered handler.
        if key not in live:
            failures.append(
                f"{action.name}: missing API route {action.method} {action.path}"
            )
        elif live[key] != action.handler:
            failures.append(
                f"{action.name}: API route {action.method} {action.path} is "
                f"served by {live[key]}, not the registered {action.handler}"
            )
        # Leg (b): the internal operation must resolve.
        if not _resolves(action.handler):
            failures.append(
                f"{action.name}: missing internal operation {action.handler}"
            )
        # Where an action claims a B-162 bus operation, that
        # operation must be registered on the bus.
        if action.internal_api:
            # Registration is lazy in the internal API (the
            # client's first call registers the handlers);
            # the checker registers them itself so a cold
            # process checks the real registry, not an empty one.
            from gpa.internal import handlers as _handlers

            _handlers.register_all()
            from gpa.internal import registry

            if registry.get_operation(action.internal_api) is None:
                failures.append(
                    f"{action.name}: missing internal API operation "
                    f"{action.internal_api}"
                )
    # The reverse direction: no live route outside the register.
    for (method, path), dotted in sorted(live.items()):
        if (method, path) not in registered:
            failures.append(
                f"unregistered API route {method} {path} served by {dotted}"
            )
    # The MCP leg (B-206): the generated tool registry against
    # the internal operation registry — both directions, scopes
    # included. It runs on the default register only: a caller
    # checking fixture actions is asking about those actions,
    # and the MCP leg's own tests exercise it directly.
    if actions is ACTIONS:
        failures.extend(check_mcp_parity())
    return failures


def parity_report(app, actions: tuple[ParityAction, ...] = ACTIONS) -> dict:
    """The B-201 parity report, as data (the script in
    ``scripts/parity_report.py`` renders it for TEST_REPORT).

    Counts are computed from the live app and the register at
    call time -- never stored numbers -- so a report cannot
    quote a parity the tree no longer has.
    """
    failures = check_parity(app, actions)
    bus_backed = sum(1 for a in actions if a.internal_api)
    # The MCP surface's counts come from the generated registry
    # itself at call time — tools served and operations covered
    # are computed, never stored numbers, like every count here.
    from gpa.mcp.tools import all_tool_specs

    specs = all_tool_specs()
    return {
        "actions_registered": len(actions),
        "api_routes_live": len(_live_routes(app)),
        "internal_api_backed": bus_backed,
        "mcp_tools": len(specs),
        "mcp_operations_covered": sum(1 for s in specs if s.kind == "operation"),
        "failures": failures,
        "parity_ok": not failures,
        "pending_surfaces": list(PENDING_SURFACES),
    }


def format_parity_report(report: dict) -> str:
    """The parity report as plain lines for a test report."""
    lines = [
        "API parity (B-201): "
        f"{report['actions_registered']} actions registered, "
        f"{report['api_routes_live']} API routes live, "
        f"{report['internal_api_backed']} actions backed by a "
        "registered internal-API operation (B-162).",
    ]
    if report["parity_ok"]:
        lines.append("Parity check: PASS -- every registered action has "
                     "its API route and its internal operation.")
    else:
        lines.append("Parity check: FAIL")
        lines.extend(f"  - {failure}" for failure in report["failures"])
    # The MCP surface (B-206): covered, counted, and — when the
    # overall check failed — its failures are in the list above
    # under the "mcp:" prefix, not hidden behind this line.
    lines.append(
        "MCP tool registry (B-206): "
        f"{report.get('mcp_tools', 0)} tools generated from the internal "
        f"operation registry, {report.get('mcp_operations_covered', 0)} "
        "operations covered — checked by the MCP leg of this checker."
    )
    for surface in report["pending_surfaces"]:
        lines.append(f"Pending surface, not yet built: {surface} -- "
                     "reported pending, never passed.")
    return "\n".join(lines)
