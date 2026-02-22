"""JWT validation via Project Service HTTP API."""

import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000"


async def validate_token(
    token: str,
    project_id: str,
    project_service_url: str | None = None,
) -> str | None:
    """Validate JWT token via Project Service.

    Calls POST /internal/validate with {token, project_id}.
    Returns user_id if valid, None otherwise.
    """
    base_url = project_service_url or os.environ.get(
        "PROJECT_SERVICE_URL", _DEFAULT_URL
    )
    url = f"{base_url}/internal/validate"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"token": token, "project_id": project_id},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("user_id")
                return None
    except Exception as e:
        logger.error("Auth validation failed: %s", e)
        return None
