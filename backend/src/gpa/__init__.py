"""Group Planning Assistant — backend package.

This package is the whole backend: a modular FastAPI application
(roadmap D-30) whose parts talk through services, never by reaching
into each other's tables. Layout, one domain at a time:

    models/    SQLAlchemy tables, one file per domain
    schemas/   Pydantic request/response shapes, one file per domain
    services/  the rules of the product (routers stay thin)
    routers/   HTTP surface — parse, authorise, call a service, answer
    storage/   file storage behind one interface (B-007)

Everything in this package is the team's own original work, written
for this project (roadmap D-46).
"""

__version__ = "0.1.0"
