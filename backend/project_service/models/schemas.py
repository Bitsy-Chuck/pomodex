"""Pydantic request/response models."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


# --- Auth ---

class RegisterRequest(BaseModel):
    email: str
    password: str

class RegisterResponse(BaseModel):
    user_id: UUID

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str

class RefreshRequest(BaseModel):
    refresh_token: str

# --- Projects ---

class CreateProjectRequest(BaseModel):
    name: str

class ProjectResponse(BaseModel):
    id: UUID
    name: str
    status: str
    created_at: datetime
    last_active_at: datetime | None = None

class ProjectDetailResponse(ProjectResponse):
    terminal_url: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_user: str = "agent"
    ssh_private_key: str | None = None
    last_backup_at: datetime | None = None
    last_snapshot_at: datetime | None = None

class ProjectCreateResponse(ProjectDetailResponse):
    pass

class BackupStatusResponse(BaseModel):
    last_backup_at: datetime | None = None
    snapshot_image: str | None = None
    last_snapshot_at: datetime | None = None

# --- Snapshots ---

class SnapshotItem(BaseModel):
    tag: str
    created_at: datetime

class RestoreRequest(BaseModel):
    snapshot_tag: str | None = None

# --- Internal ---

class InternalValidateRequest(BaseModel):
    token: str
    project_id: str

class InternalValidateResponse(BaseModel):
    user_id: str
