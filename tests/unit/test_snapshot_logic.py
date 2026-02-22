"""Unit tests for snapshot logic (M5).
T5.9: Restore determines correct image based on snapshot_image parameter.
"""

import pytest

from backend.project_service.services.snapshot_manager import restore_image_for_project

BASE_IMAGE = "agent-sandbox:latest"
AR_REGISTRY = "europe-west1-docker.pkg.dev/pomodex-fd2bcd/sandboxes"


class TestRestoreImageSelection:
    """T5.9: Pure function selects correct image for restore."""

    def test_returns_snapshot_image_when_set(self):
        snapshot = f"{AR_REGISTRY}/proj-abc123:latest"
        result = restore_image_for_project(snapshot_image=snapshot, base_image=BASE_IMAGE)
        assert result == snapshot

    def test_returns_base_image_when_snapshot_is_none(self):
        result = restore_image_for_project(snapshot_image=None, base_image=BASE_IMAGE)
        assert result == BASE_IMAGE

    def test_returns_base_image_when_snapshot_is_empty_string(self):
        result = restore_image_for_project(snapshot_image="", base_image=BASE_IMAGE)
        assert result == BASE_IMAGE
