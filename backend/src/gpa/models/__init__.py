"""SQLAlchemy models, one module per domain.

Importing this package registers every table on ``Base.metadata`` —
Alembic and the test fixtures both rely on that. Domain files:

    identity.py  users, one-time tokens, refresh tokens, API keys
    groups.py    groups, memberships, agent seats, candidate stub
    core.py      jobs, the append-only audit log, file assets
    sources.py   sources, ordered chunks, provenance, flyers,
                 inbound email addresses
    ai.py        the AI usage ledger (Phase 3)
    events.py    events, their changes, recurrences, calendar
                 feeds, discussion threads/comments, RSVPs (Phase 4)
    planning.py  tasks, decisions, open questions, polls,
                 checklists, role slots, cost lines, availability,
                 templates (Phase 7)
    notifications.py  notifications, preferences, quiet hours,
                 event invites, digests, mail events (Phase 8)
    nonprofit.py sub-groups + their memberships, announcements,
                 resources + bookings, attendance marks,
                 candidate approvals (Phase 9)
"""

from gpa.models.ai import AiUsage
from gpa.models.core import AuditLog, FileAsset, Job, Tombstone
from gpa.models.events import (
    AnnouncementDraft,
    CalendarFeed,
    Comment,
    Event,
    EventChange,
    EventContradiction,
    EventSourceLink,
    Recurrence,
    Rsvp,
    Thread,
)
from gpa.models.groups import AgentSeat, Candidate, Group, Membership
from gpa.models.identity import ApiKey, OneTimeToken, RefreshToken, User
from gpa.models.notifications import (
    Digest,
    EventInvite,
    MailEvent,
    MemberNotificationSettings,
    Notification,
    NotificationPreference,
)
from gpa.models.nonprofit import (
    Announcement,
    AttendanceMark,
    CandidateApproval,
    Resource,
    ResourceBooking,
    SubGroup,
    SubGroupMembership,
)
from gpa.models.planning import (
    AvailabilityMark,
    AvailabilitySlot,
    ChecklistItem,
    CostLine,
    Decision,
    OpenQuestion,
    Poll,
    PollOption,
    PollVote,
    RoleSlot,
    Task,
    Template,
)
from gpa.models.sources import Flyer, InboundAddress, Provenance, Source, SourceChunk

__all__ = [
    "AiUsage",
    "AnnouncementDraft",
    "ApiKey",
    "AgentSeat",
    "AuditLog",
    "AvailabilityMark",
    "AvailabilitySlot",
    "CalendarFeed",
    "Candidate",
    "ChecklistItem",
    "Comment",
    "CostLine",
    "Decision",
    "Digest",
    "Event",
    "EventInvite",
    "EventChange",
    "EventContradiction",
    "EventSourceLink",
    "FileAsset",
    "Flyer",
    "Group",
    "InboundAddress",
    "Job",
    "MailEvent",
    "MemberNotificationSettings",
    "Membership",
    "Notification",
    "NotificationPreference",
    "OneTimeToken",
    "OpenQuestion",
    "Poll",
    "PollOption",
    "PollVote",
    "Provenance",
    "Recurrence",
    "RefreshToken",
    "RoleSlot",
    "Rsvp",
    "Source",
    "SourceChunk",
    "Task",
    "Template",
    "Thread",
    "Tombstone",
    "User",
]
