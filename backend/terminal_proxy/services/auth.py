"""JWT validation via Project Service HTTP API."""

import logging
import os
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000"
_INTERNAL_SECRET_PATH = os.environ.get("INTERNAL_SECRET_PATH", "/secrets/internal-secret")


def _load_secret() -> str | None:
    try:
        return Path(_INTERNAL_SECRET_PATH).read_text().strip()
    except FileNotFoundError:
        logger.warning("Internal secret file not found at %s", _INTERNAL_SECRET_PATH)
        return None


_INTERNAL_SECRET = _load_secret()


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

    headers = {}
    if _INTERNAL_SECRET:
        headers["X-Internal-Secret"] = _INTERNAL_SECRET

    logger.info("[AUTH] POST %s project=%s token_len=%d has_secret=%s", url, project_id, len(token), bool(_INTERNAL_SECRET))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"token": token, "project_id": project_id},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info("[AUTH] validated ok user=%s", data.get("user_id"))
                    return data.get("user_id")
                body = await resp.text()
                logger.warning("[AUTH] validate returned %d body=%s", resp.status, body)
                return None
    except Exception as e:
        logger.error("[AUTH] validation request failed: %s", e)
        return None
