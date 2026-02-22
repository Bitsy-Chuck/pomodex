"""
GCP IAM service account lifecycle management.

Handles creation, key generation, IAM binding, and deletion of
per-project service accounts for GCS tenant isolation.
"""

import hashlib

from google.cloud import iam_admin_v1
from google.oauth2 import service_account


def _get_credentials(credentials_path: str):
    """Load SA credentials from a JSON key file."""
    return service_account.Credentials.from_service_account_file(credentials_path)


def _get_iam_client(credentials_path: str) -> iam_admin_v1.IAMClient:
    """Get an IAM admin client."""
    return iam_admin_v1.IAMClient(credentials=_get_credentials(credentials_path))


def make_sa_id(project_id: str) -> str:
    """Generate a deterministic SA ID from a project ID.

    GCP SA ID constraints:
    - 6-30 characters
    - lowercase letters, digits, hyphens only
    - must start with a letter
    - must not end with a hyphen

    Convention: sa-{first 26 chars of hex digest of project_id}
    This gives us "sa-" (3 chars) + 26 hex chars = 29 chars total.
    """
    digest = hashlib.sha256(project_id.encode()).hexdigest()[:26]
    return f"sa-{digest}"


def create_service_account(
    project_id: str, gcp_project: str, credentials_path: str
) -> str:
    """Create a GCP service account for a project.

    Returns the SA email address.
    """
    client = _get_iam_client(credentials_path)
    sa_id = make_sa_id(project_id)

    request = iam_admin_v1.CreateServiceAccountRequest(
        name=f"projects/{gcp_project}",
        account_id=sa_id,
        service_account=iam_admin_v1.ServiceAccount(
            display_name=f"Sandbox SA for {project_id}",
        ),
    )
    sa = client.create_service_account(request=request)
    return sa.email


def create_sa_key(sa_email: str, gcp_project: str, credentials_path: str) -> str:
    """Generate a JSON key for a service account.

    Returns the key as a JSON string.
    """
    client = _get_iam_client(credentials_path)
    sa_name = f"projects/{gcp_project}/serviceAccounts/{sa_email}"

    request = iam_admin_v1.CreateServiceAccountKeyRequest(
        name=sa_name,
        private_key_type=iam_admin_v1.ServiceAccountPrivateKeyType.TYPE_GOOGLE_CREDENTIALS_FILE,
    )
    key = client.create_service_account_key(request=request)
    # private_key_data is returned as bytes (already decoded by the client library)
    return key.private_key_data.decode("utf-8")


def grant_gcs_iam(
    sa_email: str,
    bucket: str,
    prefix: str,
    gcp_project: str,
    credentials_path: str,
) -> None:
    """Set conditional IAM bindings for a SA on a GCS bucket.

    Creates two bindings:
    1. roles/storage.objectAdmin on gs://bucket/{prefix}* (read/write own data)
    2. roles/storage.objectViewer on gs://bucket/shared/* (read-only shared data)
    """
    from google.cloud import storage as gcs

    credentials = _get_credentials(credentials_path)
    client = gcs.Client(credentials=credentials, project=gcp_project)
    bucket_obj = client.bucket(bucket)

    # Get current IAM policy (must use version 3 for conditions)
    policy = bucket_obj.get_iam_policy(requested_policy_version=3)
    policy.version = 3

    member = f"serviceAccount:{sa_email}"

    # Binding 1: objectAdmin on project prefix
    # Condition: resource name starts with the project's prefix
    # GCS object resource name format: projects/_/buckets/{bucket}/objects/{object_path}
    policy.bindings.append(
        {
            "role": "roles/storage.objectAdmin",
            "members": {member},
            "condition": {
                "title": f"project-{sa_email[:20]}",
                "description": f"Scoped to prefix {prefix}",
                "expression": (
                    f'resource.name.startsWith("projects/_/buckets/{bucket}/objects/{prefix}")'
                ),
            },
        }
    )

    # Binding 2: objectViewer on shared prefix
    policy.bindings.append(
        {
            "role": "roles/storage.objectViewer",
            "members": {member},
            "condition": {
                "title": f"shared-{sa_email[:20]}",
                "description": "Read-only access to shared prefix",
                "expression": (
                    f'resource.name.startsWith("projects/_/buckets/{bucket}/objects/shared/")'
                ),
            },
        }
    )

    bucket_obj.set_iam_policy(policy)


def remove_gcs_iam(
    sa_email: str,
    bucket: str,
    gcp_project: str,
    credentials_path: str,
) -> None:
    """Remove all IAM bindings for a SA from a GCS bucket."""
    from google.cloud import storage as gcs

    credentials = _get_credentials(credentials_path)
    client = gcs.Client(credentials=credentials, project=gcp_project)
    bucket_obj = client.bucket(bucket)

    policy = bucket_obj.get_iam_policy(requested_policy_version=3)
    policy.version = 3

    member = f"serviceAccount:{sa_email}"
    # Also match "deleted:serviceAccount:..." entries from already-deleted SAs
    policy.bindings = [
        b
        for b in policy.bindings
        if not any(m == member or m.startswith(f"deleted:serviceAccount:{sa_email}") for m in b.get("members", set()))
    ]

    bucket_obj.set_iam_policy(policy)


def delete_service_account(
    sa_email: str, gcp_project: str, credentials_path: str,
    bucket: str | None = None,
) -> None:
    """Delete a service account and clean up IAM bindings. Idempotent."""
    from google.api_core import exceptions as gcp_exceptions

    # Clean up bucket IAM bindings if bucket is specified
    if bucket:
        try:
            remove_gcs_iam(sa_email, bucket, gcp_project, credentials_path)
        except Exception:
            pass  # Best-effort IAM cleanup

    # Delete the SA itself
    client = _get_iam_client(credentials_path)
    sa_name = f"projects/{gcp_project}/serviceAccounts/{sa_email}"
    try:
        client.delete_service_account(
            request=iam_admin_v1.DeleteServiceAccountRequest(name=sa_name)
        )
    except gcp_exceptions.NotFound:
        pass  # Already deleted — idempotent
