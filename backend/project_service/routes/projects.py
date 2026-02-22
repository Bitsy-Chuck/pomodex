"""Project CRUD routes."""

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.project_service.middleware.auth_middleware import get_current_user_id
from backend.project_service.models.database import Project, get_db
from backend.project_service.models.schemas import (
    CreateProjectRequest, ProjectResponse, ProjectDetailResponse,
    ProjectCreateResponse, BackupStatusResponse,
)
from backend.project_service.services import project_service as svc

router = APIRouter(prefix="/projects", tags=["projects"])

HOST_IP = os.environ.get("HOST_IP", "0.0.0.0")
TERMINAL_PROXY_PORT = os.environ.get("TERMINAL_PROXY_PORT", "9000")


def _terminal_url(project_id: uuid.UUID) -> str:
    return f"ws://{HOST_IP}:{TERMINAL_PROXY_PORT}/terminal/{project_id}"


def _project_detail(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "status": p.status,
        "created_at": p.created_at,
        "last_active_at": p.last_active_at,
        "terminal_url": _terminal_url(p.id) if p.status == "running" else None,
        "ssh_host": HOST_IP if p.status == "running" else None,
        "ssh_port": p.ssh_host_port if p.status == "running" else None,
        "ssh_private_key": p.ssh_private_key,
        "last_backup_at": p.last_backup_at,
        "last_snapshot_at": p.last_snapshot_at,
    }


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.user_id == user_id).order_by(Project.created_at.desc())
    )
    return [
        ProjectResponse(
            id=p.id, name=p.name, status=p.status,
            created_at=p.created_at, last_active_at=p.last_active_at,
        )
        for p in result.scalars().all()
    ]


@router.post("", response_model=ProjectCreateResponse, status_code=201)
async def create_project(
    req: CreateProjectRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.create_project(uuid.UUID(user_id), req.name, db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_detail(project)


@router.post("/{project_id}/stop", response_model=ProjectDetailResponse)
async def stop_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.stop_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.post("/{project_id}/start", response_model=ProjectDetailResponse)
async def start_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.start_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await svc.delete_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "deleted"}


@router.post("/{project_id}/snapshot", response_model=ProjectDetailResponse)
async def snapshot_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.snapshot_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.post("/{project_id}/restore", response_model=ProjectDetailResponse)
async def restore_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.start_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.get("/{project_id}/backup-status", response_model=BackupStatusResponse)
async def backup_status(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return BackupStatusResponse(
        last_backup_at=project.last_backup_at,
        snapshot_image=project.snapshot_image,
        last_snapshot_at=project.last_snapshot_at,
    )
