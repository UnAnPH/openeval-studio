"""Authentication router, password hashing, and API key management for OpenEval Studio."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from typing import Any

import bcrypt
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from server.db import SessionLocal, current_user_id, current_user_slug

logger = logging.getLogger("openeval.auth")

SESSION_COOKIE_NAME = "oe_session"
SESSION_SECRET = os.getenv("SESSION_SECRET", "openeval-dev-secret-key-32-chars-long").encode(
    "utf-8"
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def hash_password(password: str) -> str:
    """Hash plaintext password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    """Verify password against bcrypt hash."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def generate_api_key() -> tuple[str, str]:
    """Generate high-entropy raw API key and its SHA-256 hash."""
    raw_key = f"oe_live_{secrets.token_urlsafe(32)}"
    token_hash = hash_token(raw_key)
    return raw_key, token_hash


def hash_token(raw_token: str) -> str:
    """Compute SHA-256 hash of API token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def is_production_env() -> bool:
    """Check if server is executing in production environment."""
    return os.getenv("OPENEVAL_ENV", "").strip().lower() == "production"


def get_session_secret() -> bytes:
    """Retrieve HMAC session secret, refusing insecure defaults in production."""
    raw = os.getenv("SESSION_SECRET", "").strip()
    if is_production_env() and (not raw or raw == "openeval-dev-secret-key-32-chars-long"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Insecure or missing SESSION_SECRET in production environment.",
        )
    if not raw:
        raw = "openeval-dev-secret-key-32-chars-long"
    return raw.encode("utf-8")


def is_https_request(request: Request) -> bool:
    """Determine whether request was made over HTTPS directly or via reverse proxy."""
    proto = (
        (request.headers.get("x-forwarded-proto") or request.headers.get("X-Forwarded-Proto") or "")
        .strip()
        .lower()
    )
    return proto == "https" or request.url.scheme == "https"


def create_session_token(user_id: int, slug: str) -> str:
    """Create signed HMAC-SHA256 session token."""
    secret = get_session_secret()
    ts = int(time.time())
    payload = f"{user_id}:{slug}:{ts}"
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str) -> tuple[int, str] | None:
    """Verify HMAC-SHA256 session token and check 14-day expiry."""
    try:
        parts = token.split(":")
        if len(parts) != 4:
            return None
        uid_str, slug, ts_str, sig = parts
        payload = f"{uid_str}:{slug}:{ts_str}"
        secret = get_session_secret()
        expected_sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        # 14 days expiration
        if time.time() - int(ts_str) > 14 * 86400:
            return None
        return int(uid_str), slug
    except Exception:
        return None


def resolve_api_key_user(raw_key: str) -> tuple[int, str] | None:
    """Resolve (user_id, slug) from raw API key via OPENEVAL_API_KEY or api_keys table."""
    raw_key = raw_key.strip()
    if not raw_key:
        return None

    # 1. Environment override OPENEVAL_API_KEY writes as owner (only compare if lengths match)
    env_key = os.getenv("OPENEVAL_API_KEY", "").strip()
    if env_key and len(raw_key) == len(env_key) and hmac.compare_digest(raw_key, env_key):
        with SessionLocal() as db:
            from server.db import get_user_id_by_slug

            owner_id = get_user_id_by_slug(db, "owner")
            return owner_id, "owner"

    # 2. Database lookup by token hash
    token_hash = hash_token(raw_key)
    with SessionLocal() as db:
        row = db.execute(
            text(
                "SELECT u.id, u.slug "
                "FROM api_keys k "
                "JOIN users u ON k.user_id = u.id "
                "WHERE k.token_hash = :hash "
                "LIMIT 1"
            ),
            {"hash": token_hash},
        ).fetchone()
        if row:
            return int(row[0]), str(row[1])

    return None


# ---------------------------------------------------------------------------
# Request & Response Models (password_hash strictly excluded)
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, description="Unique username")
    password: str = Field(..., min_length=6, max_length=128, description="Password")


class RegisterResponse(BaseModel):
    user_id: int
    username: str
    api_key: str = Field(..., description="Raw API key shown once on registration")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserResponse(BaseModel):
    user_id: int
    username: str


class KeyRotationResponse(BaseModel):
    api_key: str = Field(..., description="New raw API key shown once")


# ---------------------------------------------------------------------------
# Authentication Routes
# ---------------------------------------------------------------------------


@router.post("/register", response_model=RegisterResponse)
def register(req: RegisterRequest, response: Response, request: Request) -> Any:
    """Register a new tenant user, create initial API key, and set session cookie."""
    username = req.username.strip().lower()

    if username == "demo":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration with username 'demo' is not allowed.",
        )

    if not re.match(r"^[a-zA-Z0-9_\-]+$", username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username can only contain alphanumeric characters, underscores, and hyphens.",
        )

    pwd_hash = hash_password(req.password)
    raw_key, key_hash = generate_api_key()

    with SessionLocal() as db:
        # Check uniqueness
        existing = db.execute(
            text("SELECT id FROM users WHERE slug = :slug"),
            {"slug": username},
        ).fetchone()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Username '{username}' is already registered.",
            )

        res = db.execute(
            text("INSERT INTO users (slug, password_hash) VALUES (:slug, :hash) RETURNING id"),
            {"slug": username, "hash": pwd_hash},
        )
        user_row = res.fetchone()
        if not user_row:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed creating user record.",
            )
        user_id = int(user_row[0])

        # Store hashed API key
        db.execute(
            text("INSERT INTO api_keys (user_id, token_hash) VALUES (:uid, :hash)"),
            {"uid": user_id, "hash": key_hash},
        )
        db.commit()

    # Issue signed session cookie
    token = create_session_token(user_id, username)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_https_request(request),
        samesite="lax",
        max_age=14 * 86400,
    )

    return RegisterResponse(user_id=user_id, username=username, api_key=raw_key)


@router.post("/login", response_model=UserResponse)
def login(req: LoginRequest, response: Response, request: Request) -> Any:
    """Authenticate with username and password, setting signed session cookie."""
    username = req.username.strip().lower()

    with SessionLocal() as db:
        row = db.execute(
            text("SELECT id, slug, password_hash FROM users WHERE slug = :slug"),
            {"slug": username},
        ).fetchone()

        if not row or not verify_password(req.password, row[2]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
            )

        user_id = int(row[0])
        slug = str(row[1])

    token = create_session_token(user_id, slug)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_https_request(request),
        samesite="lax",
        max_age=14 * 86400,
    )

    return UserResponse(user_id=user_id, username=slug)


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    """Clear session cookie to log out."""
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return {"status": "ok"}


@router.get("/me", response_model=UserResponse)
def get_current_user(request: Request) -> Any:
    """Return currently authenticated user information."""
    uid = current_user_id.get()
    slug = current_user_slug.get()

    if not uid or slug in ("demo", "unknown"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )

    return UserResponse(user_id=uid, username=slug)


@router.post("/rotate-key", response_model=KeyRotationResponse)
def rotate_api_key(request: Request) -> Any:
    """Invalidate existing API keys for authenticated user and issue a new raw key."""
    uid = current_user_id.get()
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to rotate API keys.",
        )

    raw_key, key_hash = generate_api_key()

    with SessionLocal() as db:
        db.execute(text("DELETE FROM api_keys WHERE user_id = :uid"), {"uid": uid})
        db.execute(
            text("INSERT INTO api_keys (user_id, token_hash) VALUES (:uid, :hash)"),
            {"uid": uid, "hash": key_hash},
        )
        db.commit()

    return KeyRotationResponse(api_key=raw_key)
