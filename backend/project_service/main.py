"""Project Service API — FastAPI application."""

import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.project_service.middleware.internal_middleware import InternalOnlyMiddleware
from backend.project_service.routes.auth import router as auth_router
from backend.project_service.routes.internal import router as internal_router
from backend.project_service.routes.projects import router as projects_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Project Service", version="0.1.0")

# Middleware (order matters — internal check before CORS)
app.add_middleware(InternalOnlyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(auth_router)
app.include_router(internal_router)
app.include_router(projects_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    from backend.project_service.models.database import create_tables, async_session
    await create_tables()

    from backend.project_service.tasks.inactivity_checker import run_inactivity_checker_loop
    asyncio.create_task(run_inactivity_checker_loop(async_session))
