import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import get_settings
from app.db import SessionLocal
from app.integrations import github
from app.models import GitHubConnection
from conftest import register

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


@pytest.fixture()
def gh_app(monkeypatch):
    for k, v in {"GITHUB_APP_ID": "4242", "GITHUB_APP_SLUG": "ai-reviewer", "GITHUB_CLIENT_ID": "Iv1.client",
                 "GITHUB_CLIENT_SECRET": "shh", "GITHUB_APP_PRIVATE_KEY": PEM.replace("\n", "\\n")}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        path = req.url.path
        if path == "/login/oauth/access_token":
            form = parse_qs(req.content.decode())
            if form.get("code") == ["bad"]:
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": "ghu_usertoken", "expires_in": 28800,
                                             "refresh_token": "ghr_refresh", "token_type": "bearer"})
        if path == "/user":
            return httpx.Response(200, json={"login": "octocat"})
        if path == "/user/installations":
            return httpx.Response(200, json={"installations": [{"id": 77, "app_id": 4242}, {"id": 99, "app_id": 1}]})
        if path == "/user/installations/77/repositories":
            return httpx.Response(200, json={"repositories": [
                {"full_name": "acme/private-svc", "private": True, "html_url": "https://github.com/acme/private-svc"}]})
        if path == "/user/installations/99/repositories":
            raise AssertionError("must not enumerate other apps' installations")
        if path == "/app/installations/77/access_tokens":
            return httpx.Response(201, json={"token": "ghs_scoped", "expires_at": "2099-01-01T00:00:00Z"})
        if path == "/applications/Iv1.client/grant":
            return httpx.Response(204)
        return httpx.Response(404)

    github.transport = httpx.MockTransport(handler)
    yield calls
    get_settings.cache_clear()


def connect(client, h):
    url = client.post("/integrations/github/authorize", headers=h).json()["url"]
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["Iv1.client"] and "scope" not in q
    r = client.get("/integrations/github/callback", params={"code": "c0de", "state": q["state"][0]}, follow_redirects=False)
    return q["state"][0], r


def test_oauth_connect_stores_encrypted_token(client, gh_app):
    h, _ = register(client)
    state, r = connect(client, h)
    assert r.status_code == 302 and r.headers["location"].endswith("/settings?github=connected")
    with SessionLocal() as db:
        conn = db.query(GitHubConnection).one()
    assert conn.github_login == "octocat" and "ghu_usertoken" not in conn.access_token_enc
    r2 = client.get("/integrations/github/callback", params={"code": "c0de", "state": state}, follow_redirects=False)
    assert r2.headers["location"].endswith("github=invalid_state")
    assert client.get("/integrations/github/status", headers=h).json()["login"] == "octocat"


def test_forged_state_rejected(client, gh_app):
    r = client.get("/integrations/github/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
    assert r.headers["location"].endswith("github=invalid_state")


def test_only_granted_repos_listed(client, gh_app):
    h, _ = register(client)
    connect(client, h)
    repos = client.get("/integrations/github/repos", headers=h).json()
    assert [r["full_name"] for r in repos] == ["acme/private-svc"]


def test_private_repo_job_uses_single_repo_readonly_token(client, gh_app, fake_clone):
    _, envs = fake_clone
    h, _ = register(client)
    connect(client, h)
    job_id = client.post("/jobs", json={"repo_url": "https://github.com/acme/private-svc"}, headers=h).json()["job_id"]
    job = client.get(f"/jobs/{job_id}", headers=h).json()
    assert job["status"] == "completed" and job["private"] is True
    mint = next(r for r in gh_app if r.url.path.endswith("/access_tokens"))
    assert json.loads(mint.content) == {"repositories": ["private-svc"], "permissions": {"contents": "read", "metadata": "read"}}
    claims = jwt.decode(mint.headers["authorization"].split()[1], KEY.public_key(), algorithms=["RS256"])
    assert claims["iss"] == "4242" and claims["exp"] - claims["iat"] <= 600
    env = envs[-1]
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert base64.b64decode(env["GIT_CONFIG_VALUE_0"].split()[-1]).decode() == "x-access-token:ghs_scoped"


def test_public_repo_gets_no_token(client, gh_app, fake_clone):
    _, envs = fake_clone
    h, _ = register(client)
    connect(client, h)
    client.post("/jobs", json={"repo_url": "https://github.com/acme/demo"}, headers=h)
    assert envs[-1] == {} and not any(r.url.path.endswith("/access_tokens") for r in gh_app)


def test_disconnect_revokes_grant(client, gh_app):
    h, _ = register(client)
    connect(client, h)
    assert client.delete("/integrations/github", headers=h).status_code == 204
    assert any(r.method == "DELETE" and r.url.path == "/applications/Iv1.client/grant" for r in gh_app)
    assert client.get("/integrations/github/status", headers=h).json()["connected"] is False


def test_clone_error_never_leaks_token():
    from app.agents.repo import _scrub
    secret = "eC1hY2Nlc3MtdG9rZW46Z2hzX3Njb3BlZA=="
    out = _scrub(f"fatal: AUTHORIZATION: basic {secret} failed; https://x-access-token:ghs_abc@github.com/a/b")
    assert secret not in out and "ghs_abc" not in out and "***" in out
