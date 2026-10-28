from datetime import timedelta

import pytest
from celery.exceptions import Retry

from app import analytics
from app.db import SessionLocal
from app.models import AnalysisJob, Finding, Repository, User, utcnow
from conftest import URL, VULN_APP
from fakes import FakeProvider, install


def test_score_and_forecast_math():
    assert analytics.health_score({}, 5) == 100.0
    assert analytics.health_score({"critical": 1}, 1) > analytics.health_score({"critical": 3}, 1)
    assert analytics.health_score({"high": 4}, 10) > analytics.health_score({"high": 4}, 1)
    assert analytics.grade(95) == "A" and analytics.grade(10) == "F"
    t0 = utcnow()
    f = analytics.linear_forecast([(t0 + timedelta(days=d), 10 + 2 * d) for d in (0, 7, 14)], horizon_days=7)
    assert f["slope_per_week"] == 14.0 and f["projected"] == 52.0 and f["trend"] == "rising" and f["r2"] == 1.0
    assert analytics.linear_forecast([(t0, 1), (t0, 2)]) is None
    assert analytics.percentile([10, 20, 30, 40], 0.5) == 25.0


def test_lifecycle_mttr_feedback_and_overview(client, auth, fake_clone, monkeypatch):
    src, _ = fake_clone
    h, _ = auth
    install(monkeypatch, FakeProvider())
    j1 = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    issues1 = client.get(f"/jobs/{j1}/issues", headers=h).json()

    llm_issue = next(i for i in issues1 if i["source"] == "anthropic")
    static_issue = next(i for i in issues1 if i["source"] == "static")
    assert client.put(f"/issues/{llm_issue['id']}/feedback", json={"vote": 1}, headers=h).status_code == 200
    assert client.put(f"/issues/{static_issue['id']}/feedback", json={"vote": -1, "comment": "fp"}, headers=h).status_code == 200
    assert client.put(f"/issues/{static_issue['id']}/feedback", json={"vote": 5}, headers=h).status_code == 422
    assert client.get(f"/jobs/{j1}/issues", headers=h).json()[issues1.index(llm_issue)]["my_vote"] == 1

    (src / "pkg" / "app.py").write_text(VULN_APP.replace("    os.system(cmd)  # TODO: sanitize", "    print(cmd)"))
    with SessionLocal() as db:
        for f in db.query(Finding):
            f.first_seen_at = utcnow() - timedelta(hours=48)
        db.commit()
    j2 = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    s2 = client.get(f"/jobs/{j2}", headers=h).json()["summary"]["lifecycle"]
    assert s2["resolved"] >= 1

    md5 = next(i for i in client.get(f"/jobs/{j2}/issues", headers=h).json() if i["title"] == "Weak hash algorithm")
    assert client.patch(f"/findings/{md5['finding_id']}", json={"status": "dismissed"}, headers=h).json()["status"] == "dismissed"
    j3 = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    md5_3 = next(i for i in client.get(f"/jobs/{j3}/issues", headers=h).json() if i["title"] == "Weak hash algorithm")
    assert md5_3["finding_status"] == "dismissed"

    ov = client.get("/analytics/overview", headers=h).json()
    assert ov["scans"] == 3 and len(ov["timeseries"]) == 3
    assert ov["timeseries"][0]["critical"] >= 1
    assert ov["mttr"]["resolved"] >= 1 and 47 <= ov["mttr"]["avg_hours"] <= 49
    repo = ov["repos"][0]
    assert repo["name"] == "acme/demo" and 0 < repo["health_score"] < 100 and repo["forecast"]["trend"] in ("falling", "stable", "rising")
    assert len(repo["debt_series"]) == 3 and repo["debt_series"][0]["value"] > repo["debt_series"][-1]["value"]
    assert ov["llm"]["calls"] == 6 and ov["llm"]["total_cost_usd"] > 0 and ov["llm"]["latency_p50_ms"] == 42
    prompts = {p["prompt_version"]: p for p in ov["prompts"]}
    assert prompts["security_reviewer@v2"]["up"] == 1 and prompts["static"]["down"] == 1
    assert prompts["security_reviewer@v2"]["approval"] == 1.0


def test_concurrency_limit_queues_instead_of_rejecting(client, auth, fake_clone, monkeypatch):
    from app.worker import tasks
    with SessionLocal() as db:
        uid = db.query(User).one().id
        repo = Repository(user_id=uid, url=URL, name="acme/demo")
        db.add(repo)
        db.flush()
        busy = AnalysisJob(repository_id=repo.id, user_id=uid, status="running", started_at=utcnow())
        waiting = AnalysisJob(repository_id=repo.id, user_id=uid)
        db.add_all([busy, waiting])
        db.commit()
        waiting_id, busy_id = waiting.id, busy.id
    monkeypatch.setattr(tasks, "get_settings", lambda: type("S", (), {"eager": False})())
    with pytest.raises(Retry):
        tasks.analyze_repository.run(waiting_id)
    job = client.get(f"/jobs/{waiting_id}", headers=auth[0]).json()
    assert job["status"] == "queued" and "free slot" in job["queue_reason"]
    with SessionLocal() as db:
        db.get(AnalysisJob, busy_id).status = "completed"
        db.commit()
    assert tasks.analyze_repository.run(waiting_id) == "completed"


def test_stale_running_job_is_reaped(client, auth):
    with SessionLocal() as db:
        uid = db.query(User).one().id
        repo = Repository(user_id=uid, url=URL, name="acme/demo")
        db.add(repo)
        db.flush()
        db.add(AnalysisJob(repository_id=repo.id, user_id=uid, status="running", created_at=utcnow() - timedelta(hours=2),
                           started_at=utcnow() - timedelta(hours=2)))
        db.add(AnalysisJob(repository_id=repo.id, user_id=uid, status="running", started_at=utcnow()))
        db.commit()
    jobs = sorted(client.get("/jobs", headers=auth[0]).json(), key=lambda j: j["created_at"])
    assert jobs[0]["status"] == "failed" and "timed out" in jobs[0]["error"] and jobs[1]["status"] == "running"


def test_slot_lock_timeout_retries_instead_of_stranding(client, auth, monkeypatch):
    from app.worker import tasks
    with SessionLocal() as db:
        uid = db.query(User).one().id
        repo = Repository(user_id=uid, url=URL, name="acme/demo")
        db.add(repo)
        db.flush()
        job = AnalysisJob(repository_id=repo.id, user_id=uid)
        db.add(job)
        db.commit()
        jid = job.id
    monkeypatch.setattr(tasks, "_claim_slot", lambda _: (_ for _ in ()).throw(TimeoutError("lock busy")))
    with pytest.raises(Retry):
        tasks.analyze_repository.run(jid)


def test_cli_set_tier_changes_limits(client, auth):
    from app.cli import main as cli
    assert cli(["set-tier", "dev@example.com", "pro"]) == 0
    assert client.get("/auth/me", headers=auth[0]).json()["limits"]["max_concurrent_jobs"] == 3
    assert cli(["set-tier", "dev@example.com", "platinum"]) == 2
