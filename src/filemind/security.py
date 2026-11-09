"""Password, session token and CSRF primitives."""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(session_token: str, secret: str) -> str:
    return hmac.new(secret.encode(), session_token.encode(), hashlib.sha256).hexdigest()


def csrf_matches(provided: str, session_token: str, secret: str) -> bool:
    return hmac.compare_digest(provided, csrf_token(session_token, secret))
