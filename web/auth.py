from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel

from .config import Settings, get_settings


class LoginPayload(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def issue_token(username: str, settings: Settings) -> TokenResponse:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expires_hours)
    token = jwt.encode(
        {"sub": username, "exp": expires},
        settings.jwt_secret,
        algorithm="HS256",
    )
    return TokenResponse(access_token=token, expires_in=settings.jwt_expires_hours * 3600)


def _decode_token(token: str, settings: Settings) -> str:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Malformed token")
    return sub


def require_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> str:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return _decode_token(header[7:], settings)


def authorize_ws_token(token: str | None, settings: Settings) -> str:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    return _decode_token(token, settings)
