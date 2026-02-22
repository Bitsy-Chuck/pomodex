"""Internal routes: validate token + ownership."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.project_service.models.database import Project, get_db
from backend.project_service.models.schemas import (
    InternalValidateRequest, InternalValidateResponse,
)
from backend.project_service.services.auth_service import decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/validate", response_model=InternalValidateResponse)
async def validate(req: InternalValidateRequest, db: AsyncSession = Depends(get_db)):
    logger.info("[VALIDATE] project=%s token_len=%d token_prefix=%s", req.project_id, len(req.token), req.token[:20])

    # Decode JWT
    payload = decode_access_token(req.token)
    if payload is None:
        logger.info("[VALIDATE] JWT decode failed for project=%s", req.project_id)
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload["sub"]
    logger.info("[VALIDATE] JWT ok user=%s project=%s", user_id, req.project_id)

    # Check project ownership
    result = await db.execute(
        select(Project).where(
            Project.id == req.project_id,
            Project.user_id == user_id,
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        logger.info("[VALIDATE] ownership check failed user=%s project=%s", user_id, req.project_id)
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Update last_connection_at
    project.last_connection_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("[VALIDATE] success user=%s project=%s", user_id, req.project_id)
    return InternalValidateResponse(user_id=user_id)
