import os
import time
from datetime import timedelta
from types import SimpleNamespace

from app.agents import repo as repo_agent
from app.db import SessionLocal
from app.models import AnalysisJob, utcnow
from app.netutil import client_ip
from conftest import URL


def test_expired_clones_purged_but_findings_kept(client, auth, fake_clone):
    from app.worker.tasks import maintenance
    h, _ = auth
    old = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    new = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    with SessionLocal() as db:
        db.get(AnalysisJob, old).finished_at = utcnow() - timedelta(hours=100)
        db.commit()
    orphan = repo_agent.job_root("deadbeef" * 4)
    orphan.mkdir(parents=True)
    past = time.time() - 200 * 3600
    os.utime(orphan, (past, past))

    assert maintenance.run() == {"reaped": 0, "purged": 1, "orphans": 1}
    assert not repo_agent.job_root(old).exists() and repo_agent.job_root(new).exists() and not orphan.exists()
    r = client.get(f"/jobs/{old}/tree", headers=h)
    assert r.status_code == 410 and "retention" in r.json()["detail"]
    assert client.get(f"/jobs/{old}/issues", headers=h).json()
    assert client.get(f"/jobs/{new}/tree", headers=h).status_code == 200
    assert maintenance.run()["purged"] == 0


def test_failed_job_clone_removed_immediately(client, auth, fake_clone, monkeypatch):
    from app.agents import heuristics
    monkeypatch.setattr(heuristics, "security_scan", lambda snap: (_ for _ in ()).throw(RuntimeError("boom")))
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=auth[0]).json()["job_id"]
    assert client.get(f"/jobs/{job_id}", headers=auth[0]).json()["status"] == "failed"
    assert not repo_agent.job_root(job_id).exists()


def _req(peer, xff=None):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff} if xff else {})


def test_client_ip_trusted_proxies(monkeypatch):
    from app.config import get_settings
    assert client_ip(_req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    get_settings.cache_clear()
    try:
        assert client_ip(_req("10.0.0.5", "198.51.100.7, 10.0.0.9")) == "198.51.100.7"
        assert client_ip(_req("10.0.0.5", "6.6.6.6, 198.51.100.7")) == "198.51.100.7"
        assert client_ip(_req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
        assert client_ip(_req("10.0.0.5")) == "10.0.0.5"
    finally:
        get_settings.cache_clear()
