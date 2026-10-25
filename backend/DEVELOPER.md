# The Developer Pack

*Build on the Group Planning Assistant. Everything below is
generated from, or tested against, the same frozen contract
the server runs — `OPENAPI.json` at this repository's root.*

## The surface

- **Base URL:** your deployment's origin, e.g.
  `https://plans.example.org`.
- **Versioning:** everything lives under `/api/v1`. The
  version is in the path, never in a header; a breaking
  change is a new version prefix, and `/api/v1` keeps its
  promises. `OPENAPI.json` is the frozen contract for this
  version — 188 paths, 218 schemas at the time of writing.
- **Errors** are RFC 9457 problem JSON: `type`, `title`,
  `status`, `detail`, plus a stable machine `code` on
  application errors. Branch on `code`, never on `detail`
  wording.
- **Concurrency:** mutable resources carry ETags; send
  `If-Match` with the ETag you read. A stale write is a
  `412`, never a silent overwrite.
- **Writes that create things** accept an
  `Idempotency-Key` header: retrying with the same key
  returns the first result instead of a duplicate.

## Authentication — keys are self-service (B-014)

A signed-in user mints their own keys; no admin is
involved and nothing out-of-band is needed:

```http
POST /api/v1/users/me/api-keys        {"label": "my script", "scopes": ["read"]}
GET  /api/v1/users/me/api-keys        list your keys (prefixes only)
DELETE /api/v1/users/me/api-keys/{id} revoke, effective immediately
```

- The **full key is shown exactly once**, in the create
  response. Only its hash is stored; if you lose it, mint
  another — nobody can recover it, including us.
- Keys look like `gpa_…` and travel in the **`X-API-Key`**
  header: `X-API-Key: gpa_…`. (Session tokens use
  `Authorization: Bearer …` — a different door, kept apart
  on purpose.)
- **Scopes:** `read` (default) reads what the key's owner
  may read; `publish` additionally reaches the organiser
  write doors the owner may use. A key can never do more
  than its owner — it is a narrower door into the same
  account, not a second account.
- **Scopes a key cannot hold:** there is no approve scope.
  Approving candidates (the review queue) is a human act
  and stays one — for keys, for MCP tools, for everyone.

## The Python SDK (`sdk/python/gpa_sdk.py`)

One file, **standard library only** — copy it, vendor it,
or add `sdk/python` to `sys.path`. It is generated from
`OPENAPI.json` (one method per operation, plus public-page
sugar), and the test suite requires it to regenerate
byte-identically, so the SDK you read is the contract the
server runs.

```python
from gpa_sdk import GpaClient

client = GpaClient("https://plans.example.org", api_key="gpa_...")
page = client.public_page("riverbend-community-trust")
events = client.public_events("riverbend-community-trust")
```

The whole acceptance of this pack is a script an outsider
can run — `examples/public_events.py`, twelve lines, lists
a group's public events against a live server:

```sh
python examples/public_events.py http://127.0.0.1:8000 riverbend-community-trust
```

Errors raise `gpa_sdk.GpaError` carrying the HTTP status
and the parsed problem body.

## The TypeScript types (`sdk/typescript/`)

`gpa-types` — the contract's schemas as TypeScript
interfaces. `index.d.ts` is a byte-identical promotion of
the project's generated `frontend_types.ts`, enforced by
the test suite; the package is publishable as-is
(`npm pack` proves the tarball). Publishing to the npm
registry is the project owner's step.

## The public surface (no key needed)

Groups that choose `public-listed` visibility publish a
page and an embed:

- `GET /api/v1/public/groups/{slug}` — the public page.
- `GET /api/v1/public/groups/{slug}/events` — public
  projections only: fields that are not public (host, cost,
  access notes, discussion) **do not exist** in this
  response; they are not nulled, they are absent.
- `GET /api/v1/embed/groups/{slug}` — the embeddable
  widget page, served only to origins the group has
  allow-listed.

A group that is not public-listed is a 404 on these doors,
exactly as if it did not exist.

## Regenerating everything

```sh
uv run python scripts/export_openapi.py      # routes -> OPENAPI.json
uv run python scripts/generate_ts_types.py   # OPENAPI.json -> frontend_types.ts
uv run python scripts/generate_sdk.py        # contract -> Python SDK + TS package
```

Run all three whenever the API changes; commit the
outputs with the change. The suite fails on drift.
