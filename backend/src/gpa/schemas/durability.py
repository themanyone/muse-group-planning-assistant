"""Durability request/response shapes (roadmap B-171)."""

from pydantic import BaseModel, Field


class BeginUploadRequest(BaseModel):
    filename: str = "upload"
    total_bytes: int = Field(gt=0)
    chunk_bytes: int = Field(gt=0)
    sha256: str


class UploadStatusResponse(BaseModel):
    upload_id: str
    filename: str
    total_bytes: int
    chunk_bytes: int
    parts_total: int
    parts_received: list[int]
    parts_missing: list[int]
    status: str
    source_id: str | None = None


class AutosaveRequest(BaseModel):
    payload: dict
    base_version: int = 0


class AutosaveConflict(BaseModel):
    payload: dict
    version: int
    updated_at: str


class AutosaveResponse(BaseModel):
    payload: dict
    version: int
    updated_at: str
    conflicts: list[AutosaveConflict] = []
