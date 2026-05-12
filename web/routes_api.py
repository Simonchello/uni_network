from fastapi import APIRouter, Depends, HTTPException, Request, status

from .auth import LoginPayload, TokenResponse, issue_token, require_auth, verify_password
from .config import Settings, get_settings

router = APIRouter()


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginPayload, settings: Settings = Depends(get_settings)) -> TokenResponse:
    if payload.username != settings.admin_username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(payload.password, settings.admin_password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return issue_token(payload.username, settings)


@router.get("/api/stats")
def get_stats(request: Request, _: str = Depends(require_auth)) -> dict:
    poller = request.app.state.poller
    return poller.latest()


@router.get("/api/users")
def get_users(request: Request, _: str = Depends(require_auth)) -> dict:
    poller = request.app.state.poller
    snapshot = poller.latest()["snapshot"]
    return {
        "users": [
            {"email": email, "uplink": row["uplink"], "downlink": row["downlink"]}
            for email, row in sorted(snapshot.items())
        ]
    }
