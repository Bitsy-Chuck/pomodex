"""Internal routes: validate token + ownership."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.project_service.models.database import Project, get_db
from backend.project_service.models.schemas import (
    InternalValidateRequest, InternalValidateResponse,
)
from backend.project_service.services.auth_service import decode_access_token

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/validate", response_model=InternalValidateResponse)
async def validate(req: InternalValidateRequest, db: AsyncSession = Depends(get_db)):
    # Decode JWT
    payload = decode_access_token(req.token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload["sub"]

    # Check project ownership
    result = await db.execute(
        select(Project).where(
            Project.id == req.project_id,
            Project.user_id == user_id,
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Update last_connection_at
    project.last_connection_at = datetime.now(timezone.utc)
    await db.commit()

    return InternalValidateResponse(user_id=user_id)
