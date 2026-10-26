"""The MCP surface package (roadmap Phase 19, B-204 onward).

Model Context Protocol is a flagship surface of this product
(Volmarr's order, decision D-36): any MCP-capable agent must be
able to move in and work through the same law a human meets.
This package is that surface as its own module (D-30): a server
core (B-204), authentication and authorisation (B-205), a tool
registry generated from the internal operation registry
(B-206), and then the domain tool families, resources,
subscriptions, and prompts of B-207 → B-219.

One boundary rules the whole package, and the architecture
tests pin it: code here talks to the backend ONLY through the
internal encrypted API (``gpa.internal``), speaking as this
package's own service identity (B-164). It imports no service,
no router, and no model — if a capability is not yet an
internal operation, it is not yet an MCP capability, and the
honest answer is to register the operation, never to reach
around the door.

The package's ``__init__`` deliberately imports nothing heavy:
the MCP SDK and the database are the server module's business,
paid only by processes that actually serve.
"""

from __future__ import annotations

__all__: list[str] = []
