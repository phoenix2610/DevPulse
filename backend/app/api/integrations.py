import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import kv
from ..config import get_settings
from ..db import get_db
from ..integrations import github
from ..models import GitHubConnection, User, aware, utcnow
from ..security import current_user, decrypt, encrypt, read_state, sign_state

router = APIRouter(prefix="/integrations/github", tags=["github"])


def _require_enabled():
    if not get_settings().github_enabled:
        raise HTTPException(503, "GitHub App is not configured on this server")


def user_token(db: Session, user: User) -> str | None:
    conn = db.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    if not conn:
        return None
    exp = aware(conn.expires_at)
    if exp and (exp - utcnow()).total_seconds() < 300 and conn.refresh_token_enc:
        ts = github.refresh(decrypt(conn.refresh_token_enc) or "")
        conn.access_token_enc, conn.expires_at = encrypt(ts.access_token), ts.expires_at
        if ts.refresh_token:
            conn.refresh_token_enc = encrypt(ts.refresh_token)
        db.commit()
        return ts.access_token
    return decrypt(conn.access_token_enc)


@router.get("/status")
def status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    conn = db.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    s = get_settings()
    return {"enabled": s.github_enabled, "connected": bool(conn), "login": conn.github_login if conn else None,
            "install_url": github.install_url() if s.github_enabled else None}


@router.post("/authorize")
def authorize(user: User = Depends(current_user)):
    _require_enabled()
    nonce = secrets.token_urlsafe(16)
    kv.put_once(f"gh_oauth:{nonce}", str(user.id), ttl_s=600)
    return {"url": github.authorize_url(sign_state(user.id, nonce))}


@router.get("/callback")
def callback(code: str = "", state: str = "", db: Session = Depends(get_db)):
    _require_enabled()
    app_url = get_settings().public_app_url
    parsed = read_state(state)
    if not parsed or kv.pop_once(f"gh_oauth:{parsed[1]}") != str(parsed[0]) or not code:
        return RedirectResponse(f"{app_url}/settings?github=invalid_state", status_code=302)
    user_id = parsed[0]
    try:
        ts = github.exchange_code(code)
        login = github.get_login(ts.access_token)
    except github.GitHubError:
        return RedirectResponse(f"{app_url}/settings?github=error", status_code=302)
    conn = db.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user_id)) or GitHubConnection(user_id=user_id)
    conn.github_login, conn.access_token_enc, conn.expires_at = login, encrypt(ts.access_token), ts.expires_at
    conn.refresh_token_enc = encrypt(ts.refresh_token) if ts.refresh_token else None
    db.add(conn)
    db.commit()
    return RedirectResponse(f"{app_url}/settings?github=connected", status_code=302)


@router.get("/repos")
def repos(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _require_enabled()
    token = user_token(db, user)
    if not token:
        raise HTTPException(409, "GitHub not connected")
    try:
        return [r.__dict__ for r in github.list_granted_repos(token)]
    except github.GitHubError as e:
        raise HTTPException(502, str(e)) from e


@router.delete("", status_code=204)
def disconnect(db: Session = Depends(get_db), user: User = Depends(current_user)):
    conn = db.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    if conn:
        token = decrypt(conn.access_token_enc)
        if token and get_settings().github_enabled:
            try:
                github.revoke_grant(token)
            except github.GitHubError:
                pass
        db.delete(conn)
        db.commit()
