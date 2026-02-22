"""
T3.11: SA naming handles UUID truncation.

SA ID rules (GCP):
- Only lowercase letters, digits, hyphens
- Between 6-30 characters
- Deterministic given the same project ID
"""

import re
import uuid

from backend.project_service.services.gcp_iam import make_sa_id


class TestSANaming:
    """Unit tests for service account ID generation."""

    def test_uuid_project_id_produces_valid_sa_id(self):
        project_id = str(uuid.uuid4())
        sa_id = make_sa_id(project_id)
        assert 6 <= len(sa_id) <= 30
        assert re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", sa_id)

    def test_short_project_id(self):
        sa_id = make_sa_id("test-project-123")
        assert 6 <= len(sa_id) <= 30
        assert re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", sa_id)

    def test_deterministic(self):
        project_id = str(uuid.uuid4())
        assert make_sa_id(project_id) == make_sa_id(project_id)

    def test_different_ids_produce_different_sa_ids(self):
        id_a = str(uuid.uuid4())
        id_b = str(uuid.uuid4())
        assert make_sa_id(id_a) != make_sa_id(id_b)

    def test_sa_id_starts_with_sa_prefix(self):
        """SA IDs should start with 'sa-' per the naming convention in plan."""
        project_id = str(uuid.uuid4())
        sa_id = make_sa_id(project_id)
        assert sa_id.startswith("sa-")

    def test_various_uuid_formats(self):
        """Test with different UUID-like project IDs."""
        ids = [
            "550e8400-e29b-41d4-a716-446655440000",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        ]
        for pid in ids:
            sa_id = make_sa_id(pid)
            assert 6 <= len(sa_id) <= 30, f"SA ID '{sa_id}' for '{pid}' out of range"
            assert re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", sa_id), (
                f"SA ID '{sa_id}' has invalid chars"
            )
