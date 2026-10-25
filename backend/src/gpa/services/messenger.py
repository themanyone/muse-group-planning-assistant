"""The Messenger source (roadmap B-125) — the honest fallback.

The roadmap's words for this slice, kept verbatim in spirit:
where the platform's terms and the group's own connected
account allow it, a Messenger group an organiser administers
can be a live source through the official integration
surface. **That access was never granted to this project.**
No platform application, no page token, no granted
permission exists — and so this slice ships what the roadmap
says it ships in that case:

- registration of a Messenger live source still works
  (B-032's permission record — a group may record its grant
  and its intent);
- **syncing one refuses, in plain words**, and names the
  road that DOES work: paste/export ingest (B-024), whose
  parser already reads Messenger copy-paste blocks into
  speaker chunks. The fallback is not a lesser door — it
  produces the identical chunk shape every other source
  produces;
- nothing here simulates, stubs, or fakes a live Messenger
  connection. If access is granted one day, a real client
  lands behind services/livechat.py's dispatch and this
  module's refusal is deleted — the demo must never depend
  on an ungranted permission, so nothing in the demo does.
"""

from gpa.errors import AppError
from gpa.models.sources import Source

# The access state, in one place, in plain words — the
# handoff quotes this sentence, and the sync door raises it.
ACCESS_STATE = "not_granted"
REFUSAL = (
    "Messenger live access has not been granted to this "
    "project, so this source cannot be synced. The group's "
    "road is the one that works today: paste or export the "
    "conversation and add it through paste ingest — it lands "
    "as the same speaker chunks a live source would produce."
)


def access_state() -> dict:
    """The slice's honest status, for any surface that asks."""
    return {
        "connector_kind": "messenger",
        "access": ACCESS_STATE,
        "live_sync": False,
        "fallback": "paste/export ingest (B-024)",
        "detail": REFUSAL,
    }


def fetch_messages(source: Source, cursor: str) -> tuple[list[dict], str]:
    """The sync door for a Messenger source: a plain refusal.

    Named like the Samtal client's method because
    services/livechat.py dispatches both through the same
    shape — and this one always raises, because there is no
    granted access to fetch with. The refusal is recorded on
    the source's sync state by the caller, so the ungranted
    state is visible on the source itself, not just here.
    """
    raise AppError("validation_error", detail=REFUSAL)
