"""Basic-Auth: DB-User mit Rollen, .env als Notfall-Fallback."""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings
from .db import SessionLocal, User
from .security import verify_password

security = HTTPBasic()


def _verify_db(credentials: HTTPBasicCredentials) -> User | None:
    """Prueft Credentials gegen die users-Tabelle. Returns User oder None."""
    try:
        with SessionLocal() as s:
            u = s.query(User).filter(User.username == credentials.username).first()
            if u is None:
                return None
            return u if verify_password(credentials.password, u.password_hash) else None
    except Exception:
        return None


def _verify_env(credentials: HTTPBasicCredentials) -> bool:
    """Fallback: vergleicht gegen .env-Werte (konstant-Zeit-Vergleich)."""
    user_ok = secrets.compare_digest(credentials.username, settings.auth_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.auth_password)
    return user_ok and pass_ok


def _unauthorized():
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Nicht autorisiert",
        headers={"WWW-Authenticate": "Basic"},
    )


def current_user(credentials: HTTPBasicCredentials = Depends(security)) -> User:
    """DB zuerst, .env als Fallback (Pseudo-User mit Rolle 'admin')."""
    u = _verify_db(credentials)
    if u is not None:
        return u
    if _verify_env(credentials):
        return User(
            id=0,
            username=settings.auth_user,
            password_hash="",
            role="admin",
        )
    raise _unauthorized()


def require_auth(user: User = Depends(current_user)) -> User:
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur Admins koennen diese Aktion ausfuehren.",
        )
    return user
