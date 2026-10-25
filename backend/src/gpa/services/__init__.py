"""Services: where the product's rules live.

Routers parse and authorise; services decide. A service owns its
transaction — it commits before it returns, so whatever a router
answers is already durable (roadmap B-003, commit-before-answer).
"""
