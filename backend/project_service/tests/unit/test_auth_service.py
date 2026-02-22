"""Unit tests for auth_service: password hashing, JWT creation/validation."""

import time
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.project_service.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    create_refresh_token,
    hash_refresh_token,
)


class TestPasswordHashing:

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("SecurePass123!")
        assert hashed != "SecurePass123!"

    def test_hash_is_bcrypt_format(self):
        hashed = hash_password("SecurePass123!")
        assert hashed.startswith("$2b$")

    def test_verify_correct_password(self):
        hashed = hash_password("SecurePass123!")
        assert verify_password("SecurePass123!", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("SecurePass123!")
        assert verify_password("WrongPassword", hashed) is False


class TestJWT:

    def test_create_and_decode_access_token(self):
        """T8.8: JWT contains user_id claim and can be decoded."""
        user_id = str(uuid4())
        token = create_access_token(user_id)
        payload = decode_access_token(token)
        assert payload["sub"] == user_id

    def test_expired_token_rejected(self):
        """T8.9: Token valid within 15min window, rejected after."""
        user_id = str(uuid4())
        # Create a token that's already expired by using a negative expiry
        import jwt as pyjwt
        from datetime import datetime, timedelta, timezone
        expired_payload = {
            "sub": user_id,
            "iat": datetime.now(timezone.utc) - timedelta(minutes=20),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
        }
        from backend.project_service.services.auth_service import JWT_SECRET, JWT_ALGORITHM
        expired_token = pyjwt.encode(expired_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        assert decode_access_token(expired_token) is None

    def test_invalid_token_rejected(self):
        """T8.8: Invalid JWTs return None."""
        assert decode_access_token("garbage.token.here") is None

    def test_token_has_expiry(self):
        user_id = str(uuid4())
        token = create_access_token(user_id)
        payload = decode_access_token(token)
        assert "exp" in payload

    def test_token_has_15_min_expiry(self):
        """T8.9: Token expires in 15 minutes."""
        user_id = str(uuid4())
        token = create_access_token(user_id)
        payload = decode_access_token(token)
        from datetime import datetime, timezone
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        diff = (exp - iat).total_seconds()
        assert diff == 15 * 60


class TestRefreshToken:

    def test_create_refresh_token_is_opaque(self):
        token = create_refresh_token()
        assert len(token) >= 32
        assert "." not in token  # Not a JWT

    def test_refresh_tokens_are_unique(self):
        t1 = create_refresh_token()
        t2 = create_refresh_token()
        assert t1 != t2

    def test_hash_refresh_token_deterministic(self):
        token = "some_refresh_token_value"
        h1 = hash_refresh_token(token)
        h2 = hash_refresh_token(token)
        assert h1 == h2

    def test_hash_refresh_token_not_plaintext(self):
        token = "some_refresh_token_value"
        assert hash_refresh_token(token) != token
