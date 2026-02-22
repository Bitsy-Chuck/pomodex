"""Audit logging for terminal input."""

import json
import logging
import time

logger = logging.getLogger("terminal_proxy.audit")


class AuditLogger:
    """Logs terminal input messages for audit trail.

    Only logs client input (not ttyd output — too verbose, contains ANSI).
    """

    def __init__(self, project_id: str, user_id: str):
        self.project_id = project_id
        self.user_id = user_id
        self.entries: list[dict] = []

    def log_input(self, message) -> None:
        """Log an input message from the client."""
        if isinstance(message, bytes):
            content = message.decode("utf-8", errors="replace")
        else:
            content = message

        entry = {
            "event": "terminal_input",
            "project_id": self.project_id,
            "user_id": self.user_id,
            "timestamp": time.time(),
            "content": content,
        }
        self.entries.append(entry)
        logger.info(json.dumps(entry))
