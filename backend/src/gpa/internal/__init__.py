"""The internal API package (roadmap Phase 17, B-161 → B-166).

Everything the parts of this system use to talk to EACH OTHER
lives here: the module map and architecture checker (B-161), the
typed envelope + operation registry + local client SDK (B-162),
the encrypted transports (B-163), service identities and replay
protection (B-164), the internal-call audit hook (B-165), and the
in-process profile that binds them together (B-166).

Boundary law for this package: ``gpa.internal`` may import the
domain services (it is the composition root for cross-module
operations) and the shared layers (models, schemas, errors).
Domain services import ``gpa.internal.client`` — the SDK — and
never another domain's service module for a registered operation.
Nothing here imports a router: HTTP is an outer layer's concern.
"""
