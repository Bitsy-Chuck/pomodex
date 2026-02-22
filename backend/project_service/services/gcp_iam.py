"""
GCP IAM service account lifecycle management.

Handles creation, key generation, IAM binding, and deletion of
per-user service accounts and buckets for GCS tenant isolation.
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


def make_sa_id(user_id: str) -> str:
    """Generate a deterministic SA ID from a user ID.

    GCP SA ID constraints:
    - 6-30 characters
    - lowercase letters, digits, hyphens only
    - must start with a letter
    - must not end with a hyphen

    Convention: sa-{first 26 chars of hex digest of user_id}
    This gives us "sa-" (3 chars) + 26 hex chars = 29 chars total.
    """
    digest = hashlib.sha256(user_id.encode()).hexdigest()[:26]
    return f"sa-{digest}"


def make_bucket_name(user_id: str, gcp_project: str) -> str:
    """Generate a deterministic bucket name for a user.

    Format: {gcp_project}-u-{sha256(user_id)[:12]}
    Example: pomodex-fd2bcd-u-abc123def456  (28 chars)
    """
    digest = hashlib.sha256(user_id.encode()).hexdigest()[:12]
    return f"{gcp_project}-u-{digest}"


def create_bucket(
    bucket_name: str, gcp_project: str, credentials_path: str,
    location: str = "EUROPE-WEST1",
) -> None:
    """Create a GCS bucket. Idempotent (ignores 409 Conflict)."""
    from google.cloud import storage as gcs
    from google.api_core import exceptions as gcp_exceptions

    credentials = _get_credentials(credentials_path)
    client = gcs.Client(credentials=credentials, project=gcp_project)
    bucket_obj = client.bucket(bucket_name)
    bucket_obj.storage_class = "STANDARD"
    try:
        client.create_bucket(bucket_obj, location=location)
    except gcp_exceptions.Conflict:
        pass  # Bucket already exists — idempotent


def create_service_account(
    user_id: str, gcp_project: str, credentials_path: str,
) -> str:
    """Create a GCP service account for a user. Idempotent (fetches existing on 409).

    Returns the SA email address.
    """
    from google.api_core import exceptions as gcp_exceptions

    client = _get_iam_client(credentials_path)
    sa_id = make_sa_id(user_id)

    request = iam_admin_v1.CreateServiceAccountRequest(
        name=f"projects/{gcp_project}",
        account_id=sa_id,
        service_account=iam_admin_v1.ServiceAccount(
            display_name=f"Sandbox SA for user {user_id}",
        ),
    )
    try:
        sa = client.create_service_account(request=request)
        return sa.email
    except gcp_exceptions.AlreadyExists:
        return f"{sa_id}@{gcp_project}.iam.gserviceaccount.com"


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


def grant_bucket_iam(
    sa_email: str,
    bucket_name: str,
    gcp_project: str,
    credentials_path: str,
) -> None:
    """Grant unconditional roles/storage.objectAdmin on the user's bucket."""
    from google.cloud import storage as gcs

    credentials = _get_credentials(credentials_path)
    client = gcs.Client(credentials=credentials, project=gcp_project)
    bucket_obj = client.bucket(bucket_name)

    policy = bucket_obj.get_iam_policy(requested_policy_version=3)
    policy.version = 3

    member = f"serviceAccount:{sa_email}"
    policy.bindings.append(
        {
            "role": "roles/storage.objectAdmin",
            "members": {member},
        }
    )
    bucket_obj.set_iam_policy(policy)


def delete_gcs_prefix(
    bucket_name: str,
    prefix: str,
    gcp_project: str,
    credentials_path: str,
) -> None:
    """Delete all objects under a prefix in a bucket (for project deletion)."""
    from google.cloud import storage as gcs

    credentials = _get_credentials(credentials_path)
    client = gcs.Client(credentials=credentials, project=gcp_project)
    bucket_obj = client.bucket(bucket_name)

    blobs = list(bucket_obj.list_blobs(prefix=prefix))
    if blobs:
        bucket_obj.delete_blobs(blobs)


def delete_bucket(
    bucket_name: str, gcp_project: str, credentials_path: str,
) -> None:
    """Force-delete a bucket (deletes all objects first). For future user deletion."""
    from google.cloud import storage as gcs
    from google.api_core import exceptions as gcp_exceptions

    credentials = _get_credentials(credentials_path)
    client = gcs.Client(credentials=credentials, project=gcp_project)
    bucket_obj = client.bucket(bucket_name)
    try:
        bucket_obj.delete(force=True)
    except gcp_exceptions.NotFound:
        pass  # Already deleted — idempotent


def delete_service_account(
    sa_email: str, gcp_project: str, credentials_path: str,
) -> None:
    """Delete a service account. Idempotent."""
    from google.api_core import exceptions as gcp_exceptions

    client = _get_iam_client(credentials_path)
    sa_name = f"projects/{gcp_project}/serviceAccounts/{sa_email}"
    try:
        client.delete_service_account(
            request=iam_admin_v1.DeleteServiceAccountRequest(name=sa_name)
        )
    except gcp_exceptions.NotFound:
        pass  # Already deleted — idempotent
