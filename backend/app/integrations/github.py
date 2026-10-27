import base64
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt

from ..config import get_settings

transport: httpx.BaseTransport | None = None


class GitHubError(Exception):
    pass


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None


@dataclass
class GrantedRepo:
    full_name: str
    private: bool
    installation_id: int
    html_url: str


def _client(base: str, **kw) -> httpx.Client:
    return httpx.Client(base_url=base, timeout=15, transport=transport, headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", **kw.pop("headers", {})}, **kw)


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        raise GitHubError(f"GitHub API {r.status_code}: {r.text[:200]}")
    data = r.json() if r.content else {}
    if isinstance(data, dict) and data.get("error"):
        raise GitHubError(f"GitHub OAuth error: {data.get('error_description') or data['error']}")
    return data


def app_jwt() -> str:
    s = get_settings()
    now = int(time.time())
    key = s.github_app_private_key.replace("\\n", "\n")
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": s.github_app_id}, key, algorithm="RS256")


def callback_url() -> str:
    return f"{get_settings().public_api_url}/integrations/github/callback"


def authorize_url(state: str) -> str:
    s = get_settings()
    q = urlencode({"client_id": s.github_client_id, "redirect_uri": callback_url(), "state": state})
    return f"{s.github_web_url}/login/oauth/authorize?{q}"


def install_url() -> str:
    s = get_settings()
    return f"{s.github_web_url}/apps/{s.github_app_slug}/installations/new"


def _token_set(d: dict) -> TokenSet:
    exp = datetime.now(timezone.utc) + timedelta(seconds=int(d["expires_in"])) if d.get("expires_in") else None
    return TokenSet(d["access_token"], d.get("refresh_token"), exp)


def exchange_code(code: str) -> TokenSet:
    s = get_settings()
    with _client(s.github_web_url, headers={"Accept": "application/json"}) as c:
        return _token_set(_check(c.post("/login/oauth/access_token", data={
            "client_id": s.github_client_id, "client_secret": s.github_client_secret, "code": code,
            "redirect_uri": callback_url()})))


def refresh(refresh_token: str) -> TokenSet:
    s = get_settings()
    with _client(s.github_web_url, headers={"Accept": "application/json"}) as c:
        return _token_set(_check(c.post("/login/oauth/access_token", data={
            "client_id": s.github_client_id, "client_secret": s.github_client_secret,
            "grant_type": "refresh_token", "refresh_token": refresh_token})))


def get_login(user_token: str) -> str:
    with _client(get_settings().github_api_url, headers={"Authorization": f"Bearer {user_token}"}) as c:
        return _check(c.get("/user"))["login"]


def list_granted_repos(user_token: str) -> list[GrantedRepo]:
    s = get_settings()
    out: list[GrantedRepo] = []
    with _client(s.github_api_url, headers={"Authorization": f"Bearer {user_token}"}) as c:
        installs = _check(c.get("/user/installations", params={"per_page": 100}))["installations"]
        for inst in installs:
            if str(inst.get("app_id")) != str(s.github_app_id):
                continue
            page = 1
            while True:
                data = _check(c.get(f"/user/installations/{inst['id']}/repositories", params={"per_page": 100, "page": page}))
                repos = data.get("repositories", [])
                out += [GrantedRepo(r["full_name"], bool(r["private"]), int(inst["id"]), r["html_url"]) for r in repos]
                if len(repos) < 100:
                    break
                page += 1
    return out


def mint_repo_token(installation_id: int, repo_name: str) -> str:
    with _client(get_settings().github_api_url, headers={"Authorization": f"Bearer {app_jwt()}"}) as c:
        data = _check(c.post(f"/app/installations/{installation_id}/access_tokens", json={
            "repositories": [repo_name], "permissions": {"contents": "read", "metadata": "read"}}))
    return data["token"]


def revoke_grant(user_token: str) -> None:
    s = get_settings()
    basic = base64.b64encode(f"{s.github_client_id}:{s.github_client_secret}".encode()).decode()
    with _client(s.github_api_url, headers={"Authorization": f"Basic {basic}"}) as c:
        r = c.request("DELETE", f"/applications/{s.github_client_id}/grant", json={"access_token": user_token})
        if r.status_code not in (204, 404, 422):
            raise GitHubError(f"revoke failed: {r.status_code}")


def git_auth_env(token: str) -> dict[str, str]:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"}
