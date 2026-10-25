import threading

import pytest
from starlette.websockets import WebSocketDisconnect

from app.agents.repo import RepoError, validate_repo_url
from conftest import URL, register, ws_events


def test_auth_flow(client):
    assert client.post("/auth/register", json={"email": "a@b.co", "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={"email": "a@b.co", "password": "longenough1"}).status_code == 201
    assert client.post("/auth/register", json={"email": "A@b.co", "password": "longenough1"}).status_code == 409
    assert client.post("/auth/login", json={"email": "a@b.co", "password": "wrongpass12"}).status_code == 401
    tok = client.post("/auth/login", json={"email": "a@b.co", "password": "longenough1"}).json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["email"] == "a@b.co" and me["tier"] == "free" and me["limits"]["max_concurrent_jobs"] == 1
    assert client.get("/jobs").status_code == 401


def test_login_brute_force_is_rate_limited(client):
    codes = [client.post("/auth/login", json={"email": "x@y.zz", "password": "wrongpass12"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[-1] == 429


@pytest.mark.parametrize("bad", ["http://github.com/a/b", "https://evil.com/a/b", "file:///etc/passwd", "ext::sh -c id",
                                 "https://user:pw@github.com/a/b", "https://github.com/a/b?x=1", "https://github.com/a",
                                 "https://github.com/--upload-pack=x/y", "https://github.com/../x", "https://github.com/a/.."])
def test_url_validation_rejects(bad):
    with pytest.raises(RepoError):
        validate_repo_url(bad)


def test_url_normalized():
    assert validate_repo_url("https://GitHub.com/acme/demo.git/") == (URL, "acme/demo")


def test_job_lifecycle(client, auth, fake_clone):
    h, _ = auth
    r = client.post("/jobs", json={"repo_url": URL}, headers=h)
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    job = client.get(f"/jobs/{job_id}", headers=h).json()
    assert job["status"] == "completed" and job["progress"] == 100
    s = job["summary"]
    assert s["files_scanned"] == 2 and s["degraded"] is True and s["llm_used"] is False
    assert s["lifecycle"]["new"] == s["total_issues"]

    issues = client.get(f"/jobs/{job_id}/issues", headers=h).json()
    titles = {i["title"] for i in issues}
    assert "Exposed secret: AWS access key ID" in titles
    assert "os.system call" in titles and "Weak hash algorithm" in titles and "Unsafe deserialization" in titles
    assert "Python syntax error" in titles
    aws = next(i for i in issues if i["title"].startswith("Exposed secret"))
    assert (aws["file_path"], aws["line_start"], aws["severity"], aws["source"]) == ("pkg/app.py", 2, "critical", "static")
    assert all(i["finding_status"] == "open" and i["finding_id"] for i in issues)

    logs = client.get(f"/jobs/{job_id}/logs", headers=h).json()
    assert [l["seq"] for l in logs] == sorted(l["seq"] for l in logs) and logs[-1]["progress"] == 100

    tree = client.get(f"/jobs/{job_id}/tree", headers=h).json()
    assert [(e["name"], e["type"]) for e in tree] == [("pkg", "dir")] and tree[0]["issue_count"] > 0
    sub = client.get(f"/jobs/{job_id}/tree", params={"path": "pkg"}, headers=h).json()
    assert {e["name"] for e in sub} == {"app.py", "broken.py"}
    assert sum(e["issue_count"] for e in sub) == tree[0]["issue_count"]

    f = client.get(f"/jobs/{job_id}/file", params={"path": "pkg//./app.py"}, headers=h).json()
    assert f["path"] == "pkg/app.py" and f["language"] == "python" and len(f["issues"]) >= 3


def test_no_duplicate_static_findings(client, auth, fake_clone):
    h, _ = auth
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    issues = client.get(f"/jobs/{job_id}/issues", headers=h).json()
    line5 = [i["title"] for i in issues if i["file_path"] == "pkg/app.py" and i["line_start"] == 5 and i["category"] == "security"]
    assert line5 == ["os.system call"]


def test_progress_persisted_while_running(client, auth, fake_clone, monkeypatch):
    from app.agents import heuristics
    from app.db import SessionLocal
    from app.models import AnalysisJob
    seen = []

    def spy(snap):
        with SessionLocal() as db:
            seen.append(db.query(AnalysisJob).one().progress)
        return []
    monkeypatch.setattr(heuristics, "architecture_scan", spy)
    client.post("/jobs", json={"repo_url": URL}, headers=auth[0])
    assert seen and seen[0] >= 40


def test_path_traversal_blocked(client, auth, fake_clone):
    h, _ = auth
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    for p in ["../../etc/passwd", "/etc/passwd", "pkg/../../../x"]:
        assert client.get(f"/jobs/{job_id}/file", params={"path": p}, headers=h).status_code in (400, 404)
        assert client.get(f"/jobs/{job_id}/tree", params={"path": p}, headers=h).status_code in (400, 404)


def test_jobs_are_private(client, auth, fake_clone):
    h, _ = auth
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    other, _ = register(client, "x@y.zz")
    assert client.get(f"/jobs/{job_id}", headers=other).status_code == 404
    issue_id = client.get(f"/jobs/{job_id}/issues", headers=h).json()[0]["id"]
    assert client.put(f"/issues/{issue_id}/feedback", json={"vote": 1}, headers=other).status_code == 404


def test_bad_url_is_422(client, auth):
    assert client.post("/jobs", json={"repo_url": "https://evil.com/a/b"}, headers=auth[0]).status_code == 422


def test_enqueue_failure_does_not_strand_job(client, auth, monkeypatch):
    from app.api import jobs as jobs_api
    monkeypatch.setattr(jobs_api.analyze_repository, "delay", lambda *_: (_ for _ in ()).throw(ConnectionError("broker")))
    assert client.post("/jobs", json={"repo_url": URL}, headers=auth[0]).status_code == 503
    assert client.get("/jobs", headers=auth[0]).json()[0]["status"] == "failed"


def test_clone_failure_marks_job_failed(client, auth, monkeypatch):
    from app.agents import repo as r
    monkeypatch.setattr(r, "clone", lambda u, j, extra_env=None: (_ for _ in ()).throw(r.RepoError("git clone failed: not found")))
    h, _ = auth
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    job = client.get(f"/jobs/{job_id}", headers=h).json()
    assert job["status"] == "failed" and "not found" in job["error"]


def test_websocket_replay_and_first_message_auth(client, auth, fake_clone):
    h, tok = auth
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    msgs = ws_events(client, job_id, tok)
    assert msgs[-1]["status"] == "completed" and any(m.get("agent") == "security-reviewer" for m in msgs)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
            ws.send_json({"token": "bad"})
            ws.receive_json()


def test_websocket_live_stream(client, auth, fake_clone):
    from app.db import SessionLocal
    from app.models import AnalysisJob, Repository, User
    from app.worker.tasks import analyze_repository

    _, tok = auth
    with SessionLocal() as db:
        uid = db.query(User).one().id
        repo = Repository(user_id=uid, url=URL, name="acme/demo")
        db.add(repo)
        db.flush()
        job = AnalysisJob(repository_id=repo.id, user_id=uid)
        db.add(job)
        db.commit()
        job_id = job.id
    threading.Timer(0.3, lambda: analyze_repository.run(job_id)).start()
    msgs = ws_events(client, job_id, tok)
    seqs = [m["seq"] for m in msgs if m["type"] == "log"]
    assert seqs == sorted(set(seqs)) and len(seqs) > 5
    assert msgs[-1]["status"] == "completed"


def test_rag_retrieval_relevance():
    from app.agents.rag import retrieve, seed_standards
    from app.db import SessionLocal
    with SessionLocal() as db:
        assert seed_standards(db) > 0 and seed_standards(db) == 0
        top = retrieve(db, "sql injection string concatenation query", k=2)
        assert any("Injection" in r.title for r in top)


def test_tree_is_empty_not_404_while_cloning(client, auth):
    from app.db import SessionLocal
    from app.models import AnalysisJob, Repository, User
    with SessionLocal() as db:
        uid = db.query(User).one().id
        repo = Repository(user_id=uid, url=URL, name="acme/demo")
        db.add(repo)
        db.flush()
        job = AnalysisJob(repository_id=repo.id, user_id=uid, status="running")
        db.add(job)
        db.commit()
        jid = job.id
    assert client.get(f"/jobs/{jid}/tree", headers=auth[0]).json() == []
    assert client.get(f"/jobs/{jid}/tree", params={"path": "src"}, headers=auth[0]).status_code == 404
