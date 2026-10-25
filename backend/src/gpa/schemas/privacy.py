"""Privacy wire shapes (Phase 13): group export + erasure
(B-143) and the per-person / per-group privacy surfaces
(B-144). Kept apart from the domain schemas because these
shapes answer to privacy law, not to a domain.
"""

from pydantic import BaseModel, Field


class EraseRequest(BaseModel):
    """The double confirmation (B-143): the group's EXACT
    name, typed back, and the word ERASE. Anything less is a
    422 and the group stands untouched — a destructive act
    must be impossible to perform by accident or by a stray
    client retry with a stale body."""

    confirm_name: str = Field(min_length=1, max_length=160)
    confirm_word: str = Field(min_length=1, max_length=16)


class EraseResponse(BaseModel):
    """What an erasure reports: the fact, the per-table row
    counts that were destroyed, and how many stored files went
    with them. Never any content — the content is gone, and
    the receipt must not become a copy of it."""

    group_id: str
    erased: bool
    deleted: dict[str, int]
    storage_objects_removed: int


class MeDataResponse(BaseModel):
    """Everything the system holds about the caller (B-144),
    in sections named the way a person would name them. The
    leaves are plain records; the service guarantees no hash
    or secret appears anywhere in them."""

    account: dict
    my_groups: list[dict]
    my_api_keys: list[dict]
    seats_i_created: list[dict]
    my_notifications: dict
    comments_i_wrote: list[dict]
    my_rsvps: list[dict]
    my_poll_votes: list[dict]
    my_attendance: list[dict]
    tasks_assigned_to_me: list[dict]
    candidates_i_authored: list[dict]
    my_linked_accounts: list[dict]


class SourcePermissionEntry(BaseModel):
    """One source's permission record, in plain words (B-144):
    what it is, who added it, and the permission they recorded
    when they did — the words the group can audit."""

    source_id: str
    title: str
    kind: str
    added_by: str
    permission: str
    added_on: str


class RetentionRunResponse(BaseModel):
    """A retention sweep's receipt: counts only (B-144)."""

    sources_expired: int
    storage_objects_removed: int
