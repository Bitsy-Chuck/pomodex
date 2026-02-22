"""Shared test configuration for terminal_proxy tests."""

import pytest


def pytest_collection_modifyitems(config, items):
    """Add markers based on test location."""
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
