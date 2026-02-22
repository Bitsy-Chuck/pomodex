"""SQLAlchemy async engine, session, and table definitions."""

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Integer, String, Text, ForeignKey, BigInteger,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/sandboxes",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(Text, unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # GCP (per-user bucket + SA)
    gcs_bucket = Column(Text)
    gcp_sa_email = Column(Text)
    gcp_sa_key = Column(Text)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(Text, unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="creating")

    # Docker
    container_id = Column(Text)
    container_name = Column(Text)
    volume_name = Column(Text)
    ssh_host_port = Column(Integer)

    # SSH
    ssh_public_key = Column(Text, nullable=False)
    ssh_private_key = Column(Text, nullable=False)

    # GCP
    gcs_prefix = Column(Text, nullable=False)

    # Snapshot
    snapshot_image = Column(Text)
    last_snapshot_at = Column(DateTime(timezone=True))
    snapshot_size_bytes = Column(BigInteger)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_backup_at = Column(DateTime(timezone=True))
    last_connection_at = Column(DateTime(timezone=True))


async def create_tables():
    """Create all tables. Used for dev/test — production uses migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency: yield an async DB session."""
    async with async_session() as session:
        yield session
