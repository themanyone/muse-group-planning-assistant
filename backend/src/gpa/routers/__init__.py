"""HTTP routers, one module per domain.

Routers are thin (roadmap §3): parse the request, authorise via
gpa.deps, call a service, answer with a schema. All rules live in
gpa.services — never in a router.
"""
