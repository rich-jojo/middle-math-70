from __future__ import annotations

import hashlib
import ipaddress
import secrets
import unicodedata
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, utcnow
from app.models import AuthSession, User

password_hasher = PasswordHasher(type=Type.ID)
_rate_events: dict[str, deque[float]] = defaultdict(deque)


def normalize_username(username: str) -> str:
    value = unicodedata.normalize("NFKC", username).strip()
    if not value:
        raise HTTPException(status_code=422, detail="사용자 이름을 입력해 주세요.")
    if len(value) > 64:
        raise HTTPException(status_code=422, detail="사용자 이름은 64자 이하로 입력해 주세요.")
    if any(unicodedata.category(ch)[0] == "C" for ch in value):
        raise HTTPException(status_code=422, detail="사용자 이름에는 제어 문자를 사용할 수 없습니다.")
    return value


def username_key(username: str) -> str:
    return normalize_username(username).casefold()


def validate_password(password: str) -> str:
    if password is None or password == "":
        raise HTTPException(status_code=422, detail="비밀번호를 입력해 주세요.")
    if len(password) > 256:
        raise HTTPException(status_code=422, detail="비밀번호는 256자 이하로 입력해 주세요.")
    return password


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_ip(request: Request, settings: Settings) -> str:
    direct = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not forwarded or not settings.trusted_proxy_cidrs:
        return direct
    try:
        addr = ipaddress.ip_address(direct)
        trusted = [
            ipaddress.ip_network(c.strip()) for c in settings.trusted_proxy_cidrs.split(",") if c.strip()
        ]
        if any(addr in net for net in trusted):
            return forwarded
    except ValueError:
        if settings.trusted_proxy_cidrs:
            return forwarded
    return direct


def check_rate_limit(key: str, max_failures: int = 8, window_seconds: int = 60) -> None:
    now = utcnow().timestamp()
    q = _rate_events[key]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= max_failures:
        raise HTTPException(status_code=429, detail="로그인 시도가 많습니다. 잠시 후 다시 시도해 주세요.")


def record_login_failure(key: str) -> None:
    _rate_events[key].append(utcnow().timestamp())


def clear_login_failures(key: str) -> None:
    _rate_events.pop(key, None)


def check_signup_rate_limit(key: str, max_attempts: int = 8, window_seconds: int = 60) -> None:
    now = utcnow().timestamp()
    rate_key = f"signup:{key}"
    q = _rate_events[rate_key]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= max_attempts:
        raise HTTPException(status_code=429, detail="가입 시도가 많습니다. 잠시 후 다시 시도해 주세요.")
    q.append(now)


def create_session(
    db: Session, response: Response, request: Request, user: User, settings: Settings
) -> tuple[AuthSession, str]:
    raw_token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(48)
    session = AuthSession(
        user_id=user.id,
        token_hash=token_hash(raw_token),
        csrf_token=csrf,
        ip_address=client_ip(request, settings),
        user_agent=request.headers.get("user-agent", "")[:1000],
        expires_at=utcnow() + timedelta(days=settings.session_days),
    )
    db.add(session)
    db.flush()
    response.set_cookie(
        settings.session_cookie,
        raw_token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return session, csrf


def get_current_session(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthSession:
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash(raw),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    return session


def get_current_user(session: AuthSession = Depends(get_current_session)) -> User:
    return session.user


def require_csrf(request: Request, session: AuthSession = Depends(get_current_session)) -> None:
    if request.headers.get("x-csrf-token") != session.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF 토큰이 없거나 올바르지 않습니다.")


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user
