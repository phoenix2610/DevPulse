from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings, tier_for
from ..db import get_db
from ..models import GitHubConnection, User
from ..netutil import client_ip
from ..ratelimit import request_limiter
from ..schemas import Credentials, TokenOut, UserOut
from ..security import create_token, current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _throttle(request: Request) -> None:
    ip = client_ip(request)
    d = request_limiter(get_settings().auth_attempts_per_minute).try_acquire(f"auth:{ip}", 1)
    if not d.allowed:
        raise HTTPException(429, "Too many attempts", headers={"Retry-After": str(int(d.wait_s) + 1)})


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: Credentials, request: Request, db: Session = Depends(get_db)):
    _throttle(request)
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    s = get_settings()
    user = User(email=email, password_hash=hash_password(body.password), tier=s.default_tier)
    db.add(user)
    db.commit()
    return TokenOut(access_token=create_token(user.id))


@router.post("/login", response_model=TokenOut)
def login(body: Credentials, request: Request, db: Session = Depends(get_db)):
    _throttle(request)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    conn = db.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    return UserOut(id=user.id, email=user.email, tier=user.tier,
                   is_admin=user.is_admin,
                   limits=tier_for(user.tier).__dict__, github_login=conn.github_login if conn else None)
