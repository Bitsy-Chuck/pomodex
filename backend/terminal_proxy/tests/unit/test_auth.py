"""Auth client unit tests — mock HTTP responses."""

import pytest
from aiohttp import web

from backend.terminal_proxy.services.auth import validate_token


@pytest.fixture
def mock_auth_app():
    """Create an aiohttp app mimicking /internal/validate."""
    async def handler(request):
        data = await request.json()
        if data.get("token") == "good-token" and data.get("project_id") == "proj-1":
            return web.json_response({"user_id": "user-abc"})
        return web.Response(status=401)

    app = web.Application()
    app.router.add_post("/internal/validate", handler)
    return app


@pytest.mark.asyncio
async def test_valid_token_returns_user_id(aiohttp_server, mock_auth_app):
    """Valid token + correct project returns user_id."""
    server = await aiohttp_server(mock_auth_app)
    url = f"http://127.0.0.1:{server.port}"
    user_id = await validate_token("good-token", "proj-1", project_service_url=url)
    assert user_id == "user-abc"


@pytest.mark.asyncio
async def test_invalid_token_returns_none(aiohttp_server, mock_auth_app):
    """Invalid token returns None."""
    server = await aiohttp_server(mock_auth_app)
    url = f"http://127.0.0.1:{server.port}"
    user_id = await validate_token("bad-token", "proj-1", project_service_url=url)
    assert user_id is None


@pytest.mark.asyncio
async def test_wrong_project_returns_none(aiohttp_server, mock_auth_app):
    """Valid token but wrong project returns None."""
    server = await aiohttp_server(mock_auth_app)
    url = f"http://127.0.0.1:{server.port}"
    user_id = await validate_token("good-token", "wrong-proj", project_service_url=url)
    assert user_id is None


@pytest.mark.asyncio
async def test_unreachable_service_returns_none():
    """Service down returns None (no crash)."""
    user_id = await validate_token("any", "any", project_service_url="http://127.0.0.1:1")
    assert user_id is None
