"""Outgoing mail — the dev file driver (roadmap B-012).

In development there is no mail server; the "email" is a JSON file
written into the outbox directory, exactly where an operator (or a
test) can read it. The interface is one function, ``send_mail``, so
a real SMTP/HTTP driver later replaces this file without any caller
changing. The one-time token is a field of the file because in dev
the file IS the mailbox — production drivers render links into the
body instead and never persist raw tokens.
"""

import json
import uuid
from datetime import datetime, timezone

from gpa.config import Settings


def send_mail(
    settings: Settings,
    *,
    to: str,
    subject: str,
    body: str,
    token: str | None = None,
    purpose: str = "",
) -> str:
    """Write one outgoing message to the outbox; return the file id."""
    settings.outbox_dir.mkdir(parents=True, exist_ok=True)
    message_id = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    payload = {
        "id": message_id,
        "to": to,
        "subject": subject,
        "body": body,
        "token": token,
        "purpose": purpose,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    path = settings.outbox_dir / f"{stamp}-{message_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return message_id
