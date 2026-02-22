"""
M3 Integration Tests: GCP IAM — Per-Project Service Accounts

Tests T3.1 through T3.10 against real GCP APIs.

Test ordering: Tests are ordered by dependency — T3.1 creates the SA,
subsequent tests use it. pytest runs tests in file order by default.
"""

import json
import time

import pytest
from google.cloud import storage
from google.api_core import exceptions as gcp_exceptions

from backend.project_service.services.gcp_iam import (
    create_service_account,
    create_sa_key,
    grant_gcs_iam,
    delete_service_account,
    make_sa_id,
)


# ---------------------------------------------------------------------------
# Module-scoped fixtures: create one SA for all tests in this file
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sa_email(test_project_id, gcp_project, sa_key_path, created_sa_tracker):
    """Create a SA once for the entire test session. Cleaned up at end."""
    email = create_service_account(
        test_project_id, gcp_project, credentials_path=sa_key_path
    )
    created_sa_tracker.append(email)
    return email


@pytest.fixture(scope="session")
def sa_key_json(sa_email, gcp_project, sa_key_path):
    """Generate a key for the test SA. Available for all tests."""
    return create_sa_key(sa_email, gcp_project, credentials_path=sa_key_path)


@pytest.fixture(scope="session")
def sa_with_iam(sa_email, sa_key_json, test_project_id, gcs_bucket_name, gcp_project, sa_key_path):
    """SA with IAM bindings already set up. Used by T3.3-T3.7."""
    grant_gcs_iam(
        sa_email,
        gcs_bucket_name,
        f"projects/{test_project_id}/",
        gcp_project,
        credentials_path=sa_key_path,
    )
    return sa_email, sa_key_json


@pytest.fixture(scope="session")
def gcs_admin_client(sa_key_path, gcp_project):
    """GCS client with admin credentials for setting up test data."""
    return storage.Client.from_service_account_json(sa_key_path, project=gcp_project)


# ---------------------------------------------------------------------------
# T3.1: Create service account with correct naming
# ---------------------------------------------------------------------------


class TestCreateServiceAccount:
    """T3.1: Create SA with correct naming."""

    def test_creates_sa_with_correct_email_pattern(
        self, sa_email, test_project_id, gcp_project
    ):
        expected_sa_id = make_sa_id(test_project_id)
        assert sa_email == f"{expected_sa_id}@{gcp_project}.iam.gserviceaccount.com"

    def test_sa_display_name_includes_project_id(
        self, sa_email, test_project_id, gcp_project, iam_client
    ):
        """Verify the SA's display name contains the project ID."""
        from google.cloud import iam_admin_v1

        sa_name = f"projects/{gcp_project}/serviceAccounts/{sa_email}"
        sa = iam_client.get_service_account(
            request=iam_admin_v1.GetServiceAccountRequest(name=sa_name)
        )
        assert test_project_id in sa.display_name

    def test_sa_is_listable(self, sa_email, gcp_project, iam_client):
        """Verify SA appears in the list of service accounts."""
        from google.cloud import iam_admin_v1

        request = iam_admin_v1.ListServiceAccountsRequest(
            name=f"projects/{gcp_project}"
        )
        found = any(
            sa.email == sa_email
            for sa in iam_client.list_service_accounts(request=request)
        )
        assert found, f"SA {sa_email} not found in list"


# ---------------------------------------------------------------------------
# T3.2: Generate SA key
# ---------------------------------------------------------------------------


class TestCreateSAKey:
    """T3.2: Generate SA key."""

    def test_returns_valid_json_key(self, sa_key_json):
        key_data = json.loads(sa_key_json)
        assert key_data["type"] == "service_account"
        assert "project_id" in key_data
        assert "private_key_id" in key_data
        assert "private_key" in key_data
        assert "client_email" in key_data

    def test_key_client_email_matches_sa(self, sa_key_json, sa_email):
        key_data = json.loads(sa_key_json)
        assert key_data["client_email"] == sa_email


# ---------------------------------------------------------------------------
# Helper: GCS client from SA key JSON
# ---------------------------------------------------------------------------


def _gcs_client_from_key(key_json: str):
    """Create a GCS client authenticated with a SA key JSON string."""
    import tempfile

    key_data = json.loads(key_json)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(key_json)
        f.flush()
        return storage.Client.from_service_account_json(
            f.name, project=key_data["project_id"]
        )


def _wait_for_iam_propagation(fn, timeout=90, interval=5):
    """Retry fn() until it succeeds or timeout. For IAM propagation."""
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            return fn()
        except Exception as e:
            last_error = e
            time.sleep(interval)
    raise TimeoutError(
        f"IAM propagation timed out after {timeout}s. Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# T3.3: IAM binding — SA can write to its own prefix
# ---------------------------------------------------------------------------


class TestIAMBindingOwnPrefix:
    """T3.3: SA can write to its own prefix."""

    def test_sa_can_write_to_own_prefix(
        self, sa_with_iam, test_project_id, gcs_bucket_name, gcs_admin_client
    ):
        _sa_email, key_json = sa_with_iam
        sa_client = _gcs_client_from_key(key_json)
        bucket = sa_client.bucket(gcs_bucket_name)
        blob_path = f"projects/{test_project_id}/test-write.txt"
        blob = bucket.blob(blob_path)

        def upload():
            blob.upload_from_string("test content from SA")
            return True

        result = _wait_for_iam_propagation(upload)
        assert result is True

        # Verify readable
        content = blob.download_as_text()
        assert content == "test content from SA"

        # Clean up with admin client (SA may lack delete permission
        # depending on IAM condition resource matching)
        admin_bucket = gcs_admin_client.bucket(gcs_bucket_name)
        admin_bucket.blob(blob_path).delete()


# ---------------------------------------------------------------------------
# T3.4: IAM binding — SA cannot write to another project's prefix
# ---------------------------------------------------------------------------


class TestIAMBindingOtherPrefix:
    """T3.4: SA cannot write to another project's prefix."""

    def test_sa_cannot_write_to_other_prefix(
        self, sa_with_iam, gcs_bucket_name
    ):
        _sa_email, key_json = sa_with_iam
        sa_client = _gcs_client_from_key(key_json)
        bucket = sa_client.bucket(gcs_bucket_name)
        blob = bucket.blob("projects/OTHER-PROJECT-ID/test-forbidden.txt")

        with pytest.raises(Exception) as exc_info:
            blob.upload_from_string("should fail")

        assert "403" in str(exc_info.value) or "Forbidden" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T3.5: IAM binding — SA can read shared prefix
# ---------------------------------------------------------------------------


class TestIAMBindingSharedRead:
    """T3.5: SA can read shared prefix."""

    def test_sa_can_read_shared_prefix(
        self, sa_with_iam, gcs_bucket_name, gcs_admin_client
    ):
        _sa_email, key_json = sa_with_iam

        # Upload a file to shared/ using admin credentials
        admin_bucket = gcs_admin_client.bucket(gcs_bucket_name)
        shared_blob = admin_bucket.blob("shared/test-shared-read.txt")
        shared_blob.upload_from_string("shared content")

        try:
            sa_client = _gcs_client_from_key(key_json)
            sa_bucket = sa_client.bucket(gcs_bucket_name)
            sa_blob = sa_bucket.blob("shared/test-shared-read.txt")

            def read():
                return sa_blob.download_as_text()

            content = _wait_for_iam_propagation(read)
            assert content == "shared content"
        finally:
            shared_blob.delete()


# ---------------------------------------------------------------------------
# T3.6: IAM binding — SA cannot write to shared prefix
# ---------------------------------------------------------------------------


class TestIAMBindingSharedWrite:
    """T3.6: SA cannot write to shared prefix."""

    def test_sa_cannot_write_to_shared_prefix(
        self, sa_with_iam, gcs_bucket_name
    ):
        _sa_email, key_json = sa_with_iam
        sa_client = _gcs_client_from_key(key_json)
        bucket = sa_client.bucket(gcs_bucket_name)
        blob = bucket.blob("shared/test-forbidden-write.txt")

        with pytest.raises(Exception) as exc_info:
            blob.upload_from_string("should fail")

        assert "403" in str(exc_info.value) or "Forbidden" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T3.7: IAM binding — SA cannot read other project's prefix
# ---------------------------------------------------------------------------


class TestIAMBindingOtherRead:
    """T3.7: SA cannot read other project's prefix."""

    def test_sa_cannot_read_other_project_prefix(
        self, sa_with_iam, gcs_bucket_name, gcs_admin_client
    ):
        _sa_email, key_json = sa_with_iam

        # Upload a file to another project's prefix using admin creds
        admin_bucket = gcs_admin_client.bucket(gcs_bucket_name)
        other_blob = admin_bucket.blob("projects/OTHER-PROJECT-ID/secret.txt")
        other_blob.upload_from_string("secret data")

        try:
            sa_client = _gcs_client_from_key(key_json)
            sa_bucket = sa_client.bucket(gcs_bucket_name)
            sa_blob = sa_bucket.blob("projects/OTHER-PROJECT-ID/secret.txt")

            with pytest.raises(Exception) as exc_info:
                sa_blob.download_as_text()

            assert "403" in str(exc_info.value) or "Forbidden" in str(exc_info.value)
        finally:
            other_blob.delete()


# ---------------------------------------------------------------------------
# T3.8: Delete service account
# ---------------------------------------------------------------------------


class TestDeleteServiceAccount:
    """T3.8: Delete SA cleans up everything."""

    def test_delete_sa_removes_from_gcp(
        self, gcp_project, sa_key_path, created_sa_tracker, iam_client
    ):
        """Create a fresh SA, delete it, verify it's gone."""
        from google.cloud import iam_admin_v1

        # Create a disposable SA
        temp_project_id = f"test-del-{__import__('uuid').uuid4().hex[:6]}"
        temp_email = create_service_account(
            temp_project_id, gcp_project, credentials_path=sa_key_path
        )
        # Don't add to tracker — we're deleting it ourselves

        # Delete it
        delete_service_account(temp_email, gcp_project, credentials_path=sa_key_path)

        # Verify not listable
        request = iam_admin_v1.ListServiceAccountsRequest(
            name=f"projects/{gcp_project}"
        )
        found = any(
            sa.email == temp_email
            for sa in iam_client.list_service_accounts(request=request)
        )
        assert not found, f"SA {temp_email} still exists after deletion"

    def test_delete_sa_get_returns_not_found(
        self, gcp_project, sa_key_path, iam_client
    ):
        """After deletion, getting the SA returns 404."""
        from google.cloud import iam_admin_v1

        # Create and delete
        temp_project_id = f"test-del2-{__import__('uuid').uuid4().hex[:6]}"
        temp_email = create_service_account(
            temp_project_id, gcp_project, credentials_path=sa_key_path
        )
        delete_service_account(temp_email, gcp_project, credentials_path=sa_key_path)

        # Verify get raises NotFound
        sa_name = f"projects/{gcp_project}/serviceAccounts/{temp_email}"
        with pytest.raises(gcp_exceptions.NotFound):
            iam_client.get_service_account(
                request=iam_admin_v1.GetServiceAccountRequest(name=sa_name)
            )


# ---------------------------------------------------------------------------
# T3.9: Delete non-existent SA is idempotent
# ---------------------------------------------------------------------------


class TestDeleteIdempotent:
    """T3.9: Deleting non-existent SA doesn't raise."""

    def test_delete_nonexistent_sa_succeeds(self, gcp_project, sa_key_path):
        # Should not raise
        delete_service_account(
            "nonexistent@pomodex-fd2bcd.iam.gserviceaccount.com",
            gcp_project,
            credentials_path=sa_key_path,
        )


# ---------------------------------------------------------------------------
# T3.10: IAM propagation timing
# ---------------------------------------------------------------------------


class TestIAMPropagationTiming:
    """T3.10: Measure IAM propagation delay."""

    def test_iam_propagation_within_60_seconds(
        self, gcp_project, sa_key_path, gcs_bucket_name, created_sa_tracker
    ):
        """Create SA + set IAM binding, measure time until GCS access works."""
        import uuid

        # Create a fresh SA for timing measurement
        timing_project_id = f"test-timing-{uuid.uuid4().hex[:6]}"
        sa_email = create_service_account(
            timing_project_id, gcp_project, credentials_path=sa_key_path
        )
        created_sa_tracker.append(sa_email)

        key_json = create_sa_key(sa_email, gcp_project, credentials_path=sa_key_path)

        # Set IAM binding
        grant_gcs_iam(
            sa_email,
            gcs_bucket_name,
            f"projects/{timing_project_id}/",
            gcp_project,
            credentials_path=sa_key_path,
        )

        # Record the moment IAM binding was set
        start_time = time.time()

        # Try to use the SA to access GCS with backoff
        sa_client = _gcs_client_from_key(key_json)
        bucket = sa_client.bucket(gcs_bucket_name)
        blob = bucket.blob(f"projects/{timing_project_id}/timing-test.txt")

        timeout = 60
        interval = 3
        deadline = start_time + timeout
        propagation_delay = None

        while time.time() < deadline:
            try:
                blob.upload_from_string("timing test")
                propagation_delay = time.time() - start_time
                break
            except Exception:
                time.sleep(interval)

        assert propagation_delay is not None, (
            f"IAM propagation did not complete within {timeout}s"
        )
        assert propagation_delay <= 60, (
            f"IAM propagation took {propagation_delay:.1f}s (> 60s limit)"
        )

        # Document the observed delay
        print(f"\n  IAM propagation delay: {propagation_delay:.1f}s")

        # Clean up
        blob.delete()
