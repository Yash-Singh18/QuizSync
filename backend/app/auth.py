"""
FastAPI dependency for host authentication via Google OAuth (Supabase).

Usage:
    @router.post("/rooms")
    def create_room(host_id: str = Depends(current_host)):
        ...

The host's Google OAuth access/JWT token must be in:
    Authorization: Bearer <token>
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.supabase_client import get_supabase

_bearer = HTTPBearer()


def current_host(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    """
    Validates the bearer token with Supabase auth.get_user().
    Returns the authenticated user's UUID string, or raises 401.
    """
    try:
        response = get_supabase().auth.get_user(creds.credentials)
        if response is None or response.user is None:
            raise ValueError("no user")
        return str(response.user.id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired auth token",
        )
