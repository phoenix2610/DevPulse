import os
import shutil
import subprocess

import pytest

os.environ.update(DATABASE_URL=os.environ.get("TEST_DB", "sqlite:///./test.db"), REDIS_URL="", WORKDIR="./test_data",
                  ANTHROPIC_API_KEY="", OLLAMA_URL="", GEMINI_API_KEY="", SANDBOX_ENABLED="false")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.agents import repo as repo_agent  # noqa: E402
from app.db import Base, engine, init_db  # noqa: E402
from app.integrations import github  # noqa: E402
from app.kv import get_redis  # noqa: E402
from app.llm import prompts  # noqa: E402
from app.main import app  # noqa: E402

VULN_APP = '''import os, pickle, hashlib
AWS_KEY = "AKIAIOSFODNN7ABCDEFG"

def run(cmd):
    os.system(cmd)  # TODO: sanitize

def load(blob):
    return pickle.loads(blob)

def h(x):
    return hashlib.md5(x).hexdigest()
'''
URL = "https://github.com/acme/demo"


@pytest.fixture(autouse=True)
def fresh_state():
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    init_db()
    get_redis().flushall()
    prompts.invalidate()
    github.transport = None
    yield
    shutil.rmtree("./test_data", ignore_errors=True)


@pytest.fixture()
def fake_clone(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "app.py").write_text(VULN_APP)
    (src / "pkg" / "broken.py").write_text("def x(:\n")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "skip.js").write_text("eval(x)")
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    envs: list[dict] = []

    def _clone(url, job_id, extra_env=None):
        envs.append(extra_env or {})
        dest = repo_agent.job_root(job_id)
        shutil.copytree(src, dest)
        return dest

    monkeypatch.setattr(repo_agent, "clone", _clone)
    return src, envs


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def register(client, email="dev@example.com"):
    r = client.post("/auth/register", json={"email": email, "password": "supersecret1"})
    assert r.status_code == 201, r.text
    tok = r.json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, tok


@pytest.fixture()
def auth(client):
    return register(client)


def ws_events(client, job_id, token):
    with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
        ws.send_json({"token": token})
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "done":
                return msgs
