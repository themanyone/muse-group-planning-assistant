# gpa-types

TypeScript types for the **Group Planning Assistant** API.

`index.d.ts` is generated from the API's frozen OpenAPI
contract (`OPENAPI.json` in the backend repository) by the
same generator that produces the project's
`frontend_types.ts` — the file in this package is a
byte-identical promotion of it, never a hand-maintained
fork. When the contract moves, this package moves in the
same commit.

## Use

```ts
import type { EventResponse, GroupResponse } from "gpa-types";
```

Every request/response schema in the contract is exported
as an interface named exactly as the contract names it.

## Regenerate / republish (maintainers)

From the backend repository root:

```sh
uv run python scripts/export_openapi.py        # if the API changed
uv run python scripts/generate_ts_types.py     # refresh frontend_types.ts
uv run python scripts/generate_sdk.py          # promote into this package
npm pack sdk/typescript                        # build the tarball
npm publish sdk/typescript                     # owner's step, owner's word
```

The test suite regenerates the package's `index.d.ts` and
requires it to match byte for byte, so a stale publish is
impossible to miss.
