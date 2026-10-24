import logging
import time

from ..agents.graph import JobContext, build_graph
from ..config import get_settings, tier_for
from ..db import SessionLocal
from ..events import emit, finish
from ..integrations import github
from ..kv import lock
from ..models import AnalysisJob, CodeIssue, utcnow
from ..observability.tracing import current_trace_id, span
from ..services import cleanup_clones, purge_clone, reap_stale_jobs, running_count, update_findings
from .celery_app import celery_app

log = logging.getLogger(__name__)
SLOT_RETRY_S = 15


def _git_env(installation_id: int | None, repo_name: str):
    def make() -> dict[str, str]:
        if not installation_id:
            return {}
        return github.git_auth_env(github.mint_repo_token(installation_id, repo_name.split("/", 1)[1]))
    return make


def _claim_slot(job_id: str) -> tuple[str, int] | None:
    with SessionLocal() as db:
        job = db.get(AnalysisJob, job_id)
        if not job or job.status not in ("queued", "running"):
            return ("gone", 0)
        tier = tier_for(job.repository.user.tier)
        with lock(f"user-slots:{job.user_id}"):
            db.refresh(job)
            if job.status == "running":
                db.query(CodeIssue).filter(CodeIssue.job_id == job_id).delete()
            elif running_count(db, job.user_id) >= tier.max_concurrent_jobs and not get_settings().eager:
                reason = f"Waiting for a free slot ({tier.max_concurrent_jobs} concurrent job(s) on your plan)"
                if job.queue_reason != reason:
                    job.queue_reason = reason
                    db.commit()
                    emit(job_id, reason + "; your job is queued, not rejected", agent="scheduler", status="queued")
                return ("queued", running_count(db, job.user_id))
            job.status, job.started_at, job.queue_reason, job.progress = "running", utcnow(), None, 0
            db.commit()
    return None


@celery_app.task(name="analyze_repository", bind=True, max_retries=240)
def analyze_repository(self, job_id: str) -> str:
    with SessionLocal() as db:
        reap_stale_jobs(db)
    try:
        wait = _claim_slot(job_id)
    except TimeoutError:
        raise self.retry(countdown=SLOT_RETRY_S)
    if wait:
        if wait[0] == "gone":
            return "skipped"
        raise self.retry(countdown=SLOT_RETRY_S)

    with SessionLocal() as db:
        job = db.get(AnalysisJob, job_id)
        repo, user = job.repository, job.repository.user
        ctx = JobContext(job_id=job_id, user_id=user.id, repo_url=repo.url, tier=tier_for(user.tier),
                         git_env=_git_env(repo.installation_id, repo.name))

    with span("analysis_job", **{"job.id": job_id, "repo.name": repo.name, "user.tier": user.tier}):
        trace_id = current_trace_id()
        with SessionLocal() as db:
            db.get(AnalysisJob, job_id).trace_id = trace_id
            db.commit()
        emit(job_id, "Job picked up by worker", agent="system", progress=1, status="running")
        t0 = time.monotonic()
        try:
            result = build_graph(ctx, lambda msg, **kw: emit(job_id, msg, status="running", **kw)).invoke({"issues": []})
            with SessionLocal() as db:
                job = db.get(AnalysisJob, job_id)
                db.add_all(CodeIssue(job_id=job_id, **{k: v for k, v in i.items() if k in CodeIssue.__table__.c})
                           for i in result["final_issues"])
                now = utcnow()
                db.flush()
                lifecycle = update_findings(db, job, now)
                summary = {**result["summary"], "lifecycle": lifecycle, "duration_s": round(time.monotonic() - t0, 1)}
                job.status, job.progress, job.summary = "completed", 100, summary
                job.commit_sha, job.finished_at = summary.get("commit_sha"), now
                db.commit()
            red = sum(summary["redactions"].values())
            emit(job_id, f"Analysis complete: {summary['total_issues']} issues"
                 + (f"; {red} secret/PII values masked before any LLM call" if red else "")
                 + ("; degraded mode (static analysis covered LLM gaps)" if summary["degraded"] else ""),
                 progress=100, status="completed")
            finish(job_id, "completed")
            return "completed"
        except Exception as e:  # noqa: BLE001
            log.exception("job %s failed", job_id)
            msg = str(e) if type(e).__name__ in ("RepoError", "GitHubError") else f"Internal error: {type(e).__name__}"
            with SessionLocal() as db:
                job = db.get(AnalysisJob, job_id)
                job.status, job.error, job.finished_at = "failed", msg, utcnow()
                purge_clone(job)
                db.commit()
            emit(job_id, f"Failed: {msg}", level="error", status="failed")
            finish(job_id, "failed")
            return "failed"


@celery_app.task(name="maintenance")
def maintenance() -> dict:
    with SessionLocal() as db:
        return {"reaped": reap_stale_jobs(db), **cleanup_clones(db)}
