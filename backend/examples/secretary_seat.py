#!/usr/bin/env python3
"""Worked example (roadmap B-129): a secretary seat at work.

The scene: a group plans in its Samtal hall. A *secretary
seat* — an AI participant with its own API key, its own
role, and scopes ``read`` + ``draft`` + ``publish`` — is
asked to keep the review queue fed. This script is its
whole job, in the open:

1. It reads the hall source's chunks with its key
   (``read`` scope; the seat sees what its seat role sees).
2. It drafts ONE candidate into the queue, titled from the
   hall's own words — the first chunk that announces a plan
   (the rule below is transparent on purpose: a worked
   example teaches the API's shape, not a drafting model;
   real drafting is the extraction engine's, Phase 3).
3. It tries to approve its own draft — and is refused, 403
   ``seat_cannot_approve``. That refusal is part of the
   example: a seat drafts, a person decides (B-018), and
   the pack's scope documentation says so in writing.

Run it against a live server:

    python examples/secretary_seat.py \
        --base-url http://127.0.0.1:8000 \
        --api-key gpa_... --group-id <id> --source-id <id>

Or import :func:`secretary_pass` with any httpx-shaped
client — the test suite runs it in mock mode exactly that
way (tests/test_agent_pack.py), with no server and no model.
"""

import argparse
import json


def _plan_line(chunks: list[dict]) -> dict | None:
    """The chunk this secretary treats as the plan: the
    first one whose words announce something ("is on",
    "moved to", "cancelled" — the phrases a hall uses when
    a plan exists). Deliberately simple and stated here in
    the open; nothing about this rule is a model."""
    markers = (" is on", "moved to", "cancelled", "postponed")
    for chunk in chunks:
        text = chunk.get("text", "")
        if any(marker in text for marker in markers):
            return chunk
    return chunks[0] if chunks else None


def secretary_pass(http, *, base_url: str, api_key: str, group_id: str, source_id: str) -> dict:
    """One pass of the secretary's job. ``http`` is anything
    with httpx's get/post shape (a real client in production,
    the app's TestClient in the mock-mode test).

    Returns a plain report: what it read, what it drafted,
    and the approval refusal it met — the three facts that
    define the seat experience."""
    headers = {"X-Api-Key": api_key}
    base = f"{base_url}/api/v1/groups/{group_id}"

    # 1. Read the hall (read scope).
    detail = http.get(f"{base}/sources/{source_id}", headers=headers)
    detail.raise_for_status()
    source = detail.json()
    chunks = source.get("chunks", [])

    # 2. Draft from the hall's own words (draft scope).
    line = _plan_line(chunks)
    report = {
        "source_title": source.get("title", ""),
        "chunks_read": len(chunks),
        "candidate_id": None,
        "candidate_title": None,
        "approve_status": None,
        "approve_code": None,
    }
    if line is None:
        return report
    title = line["text"].strip()
    if len(title) > 240:
        title = title[:237] + "..."
    drafted = http.post(
        f"{base}/candidates",
        json={"kind": "event", "title": title},
        headers=headers,
    )
    drafted.raise_for_status()
    candidate = drafted.json()
    report["candidate_id"] = candidate["id"]
    report["candidate_title"] = candidate["title"]

    # 3. The boundary, demonstrated: a seat can never approve.
    approval = http.post(
        f"{base}/candidates/{candidate['id']}/approve", headers=headers
    )
    report["approve_status"] = approval.status_code
    if approval.status_code != 200:
        try:
            report["approve_code"] = approval.json().get("code")
        except ValueError:
            report["approve_code"] = None
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--source-id", required=True)
    args = parser.parse_args()

    import httpx

    with httpx.Client() as http:
        report = secretary_pass(
            http,
            base_url=args.base_url.rstrip("/"),
            api_key=args.api_key,
            group_id=args.group_id,
            source_id=args.source_id,
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
