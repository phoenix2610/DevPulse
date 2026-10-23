import shutil
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AnalysisJob, CodeIssue, Finding, aware, utcnow


def reap_stale_jobs(db: Session) -> int:
    s = get_settings()
    now = utcnow()
    n = 0
    stale_running = now - timedelta(seconds=s.task_time_limit_s + 120)
    stale_queued = now - timedelta(hours=6)
    candidates = select(AnalysisJob).where(AnalysisJob.status.in_(("running", "queued")),
                                           AnalysisJob.created_at < stale_running)
    for job in db.scalars(candidates):
        started, created = aware(job.started_at), aware(job.created_at)
        if (job.status == "running" and started and started < stale_running) or \
                (job.status == "queued" and created < stale_queued):
            job.status, job.error, job.finished_at = "failed", "Job timed out or the worker was lost", now
            n += 1
    if n:
        db.commit()
    return n


def running_count(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(AnalysisJob)
                     .where(AnalysisJob.user_id == user_id, AnalysisJob.status == "running")) or 0


def update_findings(db: Session, job: AnalysisJob, finished_at: datetime) -> dict:
    issues = db.scalars(select(CodeIssue).where(CodeIssue.job_id == job.id, CodeIssue.fingerprint.is_not(None))).all()
    existing = {f.fingerprint: f for f in db.scalars(select(Finding).where(Finding.repository_id == job.repository_id))}
    seen, stats = set(), {"new": 0, "reopened": 0, "resolved": 0}
    for i in issues:
        if i.fingerprint in seen:
            continue
        seen.add(i.fingerprint)
        f = existing.get(i.fingerprint)
        if f is None:
            db.add(Finding(repository_id=job.repository_id, fingerprint=i.fingerprint, title=i.title, severity=i.severity,
                           category=i.category, file_path=i.file_path, status="open", first_seen_at=finished_at,
                           last_seen_at=finished_at, last_seen_job_id=job.id))
            stats["new"] += 1
            continue
        f.last_seen_at, f.last_seen_job_id, f.severity = finished_at, job.id, i.severity
        if f.status == "resolved":
            f.status, f.resolved_at = "open", None
            stats["reopened"] += 1
    for fp, f in existing.items():
        if fp not in seen and f.status == "open":
            f.status, f.resolved_at = "resolved", finished_at
            stats["resolved"] += 1
    return stats


def purge_clone(job: AnalysisJob) -> None:
    from .agents.repo import job_root

    shutil.rmtree(job_root(job.id), ignore_errors=True)
    job.source_purged_at = utcnow()


def cleanup_clones(db: Session) -> dict:
    from .agents.repo import job_root

    s = get_settings()
    cutoff = utcnow() - timedelta(hours=s.repo_retention_hours)
    purged = 0
    for job in db.scalars(select(AnalysisJob).where(AnalysisJob.status.in_(("completed", "failed")),
                                                    AnalysisJob.source_purged_at.is_(None),
                                                    AnalysisJob.finished_at < cutoff)):
        purge_clone(job)
        purged += 1
    db.commit()
    orphans = 0
    root = job_root("x").parent
    if root.is_dir():
        known = set(db.scalars(select(AnalysisJob.id)))
        for d in root.iterdir():
            if d.is_dir() and d.name not in known and d.stat().st_mtime < cutoff.timestamp():
                shutil.rmtree(d, ignore_errors=True)
                orphans += 1
    return {"purged": purged, "orphans": orphans}
