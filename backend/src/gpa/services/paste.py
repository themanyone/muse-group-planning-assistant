"""Paste ingest: chat exports and plain text (roadmap B-024).

What this file is for: a member pastes a conversation — copied out
of Messenger, WhatsApp, Discord, or Slack, or just plain text with
``Name:`` lines — and this parser turns it into ordered chunks, one
per message, each keeping its speaker and its timestamp words.

Two laws shape everything here:

- **Nothing is ever dropped.** A line the parser cannot attribute
  joins the previous message (chat exports wrap long messages
  across lines), and a line before any speaker exists becomes a
  senderless chunk of its own. Verbatim in, verbatim kept.
- **Timestamps are kept twice**: the raw words (``timestamp_text``)
  exactly as exported, and a parsed ``sent_at`` only when a known
  numeric format reads unambiguously. Relative words ("Yesterday",
  "Today at 6:30 PM") are NEVER resolved here — resolving them is
  the date resolver's named job (Phase 3, D-22), done against the
  message's true anchor, not a paste-time guess.

Slash dates: exports differ by locale. The parser tries day-first
(the team's exports) and falls back to month-first only when
day-first cannot parse (e.g. a 24 in the second position's mirror —
``10/24/26``). The raw text is kept either way, so a wrong guess is
always recoverable from the chunk itself.
"""

import re
from datetime import datetime

# --- The speaker-line shapes, most specific first -------------------
# WhatsApp, bracketed: "[24/10/2026, 18:30:12] Alice: message" and
# the US shape "[10/24/26, 6:30:12 PM] Alice: message".
_WHATSAPP_BRACKET = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+(?P<sender>[^:]{1,80}):\s?(?P<text>.*)$"
)
# WhatsApp, dash shape: "24/10/2026, 18:30 - Alice: message".
_WHATSAPP_DASH = re.compile(
    r"^(?P<ts>\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)"
    r"\s+-\s+(?P<sender>[^:]{1,80}):\s?(?P<text>.*)$",
    re.IGNORECASE,
)
# Generic "Name: message" — the shape plain-text exports and hand
# transcripts share. The name may not contain sentence punctuation,
# which keeps ordinary prose ("Note: bring chairs") attributable
# too — that IS the convention this shape promises.
_NAME_COLON = re.compile(r"^(?P<sender>[A-Za-z0-9_ .'@-]{1,60}):\s+(?P<text>.+)$")
# Discord header line: "Alice — 24/10/2026 18:30" (message follows
# on its own lines). Also "Alice — Today at 6:30 PM".
_DISCORD_HEADER = re.compile(
    r"^(?P<sender>.{1,60}?)\s+—\s+(?P<ts>.{3,40})$"
)
# Slack header line: "Alice Example  6:30 PM" (message follows).
_SLACK_HEADER = re.compile(
    r"^(?P<sender>[A-Za-z][A-Za-z0-9_ .'@-]{0,50}?)\s{2,}"
    r"(?P<ts>\d{1,2}:\d{2}\s*(?:AM|PM)|\d{1,2}:\d{2})$",
    re.IGNORECASE,
)
# A bare name on its own line — the Messenger copy-paste shape,
# where a block is: name line, message lines, optional time line.
_BARE_NAME = re.compile(r"^[A-Z][A-Za-z.'-]*(?: [A-Z][A-Za-z.'-]*){0,3}$")
# A standalone timestamp line (Messenger ends a block with one).
_STANDALONE_TS = re.compile(
    r"^(?:[A-Z][a-z]{2} \d{1,2}, \d{4},? \d{1,2}:\d{2}\s*(?:AM|PM)?"
    r"|\d{1,2}/\d{1,2}/\d{2,4},? \d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)$",
    re.IGNORECASE,
)

# Numeric timestamp formats, tried in order (see the module header
# for the day-first rule). Anything else stays raw-text only.
_TS_FORMATS = (
    "%d/%m/%Y, %H:%M:%S",
    "%d/%m/%Y, %H:%M",
    "%d/%m/%y, %H:%M:%S",
    "%d/%m/%y, %H:%M",
    "%m/%d/%Y, %I:%M:%S %p",
    "%m/%d/%Y, %I:%M %p",
    "%m/%d/%y, %I:%M:%S %p",
    "%m/%d/%y, %I:%M %p",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%b %d, %Y, %I:%M %p",
    "%b %d, %Y, %H:%M",
)


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse a numeric timestamp, or return None (raw text is kept
    by the caller either way — None means "words, not a number")."""
    if not raw:
        return None
    text = raw.strip().strip(",")
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _chunk(sender, text, timestamp_text=None):
    """One chunk dict in the sources core's shape."""
    return {
        "sender": sender,
        "text": text,
        "timestamp_text": timestamp_text,
        "sent_at": parse_timestamp(timestamp_text),
    }


def parse_paste(text: str) -> list[dict]:
    """Parse pasted chat/plain text into ordered chunk dicts.

    Returns at least one chunk for any non-empty input: unparsed
    text is preserved verbatim, never dropped (the module law).
    """
    chunks: list[dict] = []
    pending_sender: str | None = None  # A header line awaiting its message.
    pending_ts: str | None = None

    def flush_pending(line: str) -> bool:
        """Start a chunk from a pending header + this first line."""
        nonlocal pending_sender, pending_ts
        if pending_sender is None:
            return False
        chunks.append(_chunk(pending_sender, line, pending_ts))
        pending_sender, pending_ts = None, None
        return True

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            # Blank lines separate messages in header formats; in
            # running text they are paragraph breaks inside the
            # current message. Either way the words survive: a
            # blank inside a chunk's text is kept as a newline.
            if chunks and pending_sender is None:
                chunks[-1]["text"] += "\n"
            continue

        match = _WHATSAPP_BRACKET.match(line) or _WHATSAPP_DASH.match(line)
        if match:
            pending_sender, pending_ts = None, None
            chunks.append(
                _chunk(match["sender"].strip(), match["text"], match["ts"].strip())
            )
            continue

        match = _DISCORD_HEADER.match(line)
        if match and ("/" in match["ts"] or "at" in match["ts"].lower()):
            pending_sender, pending_ts = match["sender"].strip(), match["ts"].strip()
            continue

        match = _SLACK_HEADER.match(line)
        if match:
            pending_sender, pending_ts = match["sender"].strip(), match["ts"].strip()
            continue

        if _STANDALONE_TS.match(line.strip()) and chunks and pending_sender is None:
            # Messenger shape: the time belongs to the block above.
            chunks[-1]["timestamp_text"] = line.strip()
            chunks[-1]["sent_at"] = parse_timestamp(line.strip())
            continue

        match = _NAME_COLON.match(line)
        if match:
            pending_sender, pending_ts = None, None
            chunks.append(_chunk(match["sender"].strip(), match["text"], None))
            continue

        if _BARE_NAME.match(line.strip()) and len(line.strip()) <= 40:
            # A name alone on a line starts a Messenger-style block:
            # name line, message lines, optional timestamp line. In
            # this headerless shape a line of one-to-four capitalised
            # words IS the speaker marker — that is the export's own
            # convention, and ordinary wrapped prose (sentences with
            # lowercase words or punctuation) never matches it. A
            # pending header that never got a message is kept as a
            # chunk with empty text, so nothing vanishes.
            if pending_sender is not None:
                chunks.append(_chunk(pending_sender, "", pending_ts))
            pending_sender, pending_ts = line.strip(), None
            continue

        if flush_pending(line):
            continue
        if chunks:
            # A wrapped continuation of the previous message.
            last = chunks[-1]
            last["text"] = f"{last['text']}\n{line}" if last["text"] else line
        else:
            # Text before any speaker: preserved as its own chunk.
            chunks.append(_chunk(None, line, None))

    if pending_sender is not None:
        # A trailing header with no message: keep it, never drop it.
        chunks.append(_chunk(pending_sender, "", pending_ts))
    if not chunks and text.strip():
        chunks.append(_chunk(None, text.strip(), None))
    # Trim the paragraph-break newlines blank lines added at edges,
    # but never alter a chunk's inner words.
    for chunk in chunks:
        chunk["text"] = chunk["text"].strip("\n")
    return chunks
