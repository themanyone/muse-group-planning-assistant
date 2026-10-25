"""Pydantic request/response schemas, one module per domain.

Schemas are the API's typed promises (roadmap D-04): the same
shapes validate incoming requests and describe outgoing responses,
and the OpenAPI contract (B-006) is generated from them. A response
schema is also a privacy boundary — a field that is not in the
schema (a password hash, a token hash) cannot leak in a response.
"""
