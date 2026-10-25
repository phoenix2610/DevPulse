import asyncio
import logging
from collections import Counter
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events
from ..agents import repo as repo_agent
from ..agents.repo import LANGS, SKIP_DIRS, RepoError
from ..db import SessionLocal, get_db
from ..integrations import github
from ..models import AnalysisJob, CodeIssue, Finding, GitHubConnection, IssueFeedback, JobLog, Repository, User, utcnow
from ..ratelimit import request_limiter
from ..schemas import FileOut, IssueOut, JobAccepted, JobCreate, JobOut, LogOut, TreeEntry
from ..security import current_user, decrypt, user_from_token
from ..services import reap_stale_jobs
from ..worker.tasks import analyze_repository

log = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])
SUBMISSIONS_PER_MINUTE = 20


def job_out(job: AnalysisJob) -> JobOut:
    r = job.repository
    return JobOut(id=job.id, status=job.status, progress=job.progress, queue_reason=job.queue_reason, repo_url=r.url,
                  repo_name=r.name, private=r.private, trace_id=job.trace_id, commit_sha=job.commit_sha,
                  summary=job.summary, error=job.error, created_at=job.created_at, finished_at=job.finished_at)


def own_job(db: Session, job_id: str, user: User) -> AnalysisJob:
    job = db.get(AnalysisJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "Job not found")
    return job


def _granted_installation(db: Session, user: User, full_name: str) -> tuple[int, bool] | None:
    conn = db.scalar(select(GitHubConnection).where(GitHubConnection.user_id == user.id))
    token = decrypt(conn.access_token_enc) if conn else None
    if not token:
        return None
    try:
        for r in github.list_granted_repos(token):
            if r.full_name.lower() == full_name.lower():
                return r.installation_id, r.private
    except github.GitHubError as e:
        log.warning("github lookup failed: %s", e)
    return None


@router.post("/jobs", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_job(body: JobCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = request_limiter(SUBMISSIONS_PER_MINUTE).try_acquire(f"jobs:{user.id}", 1)
    if not d.allowed:
        raise HTTPException(429, "Too many submissions", headers={"Retry-After": str(int(d.wait_s) + 1)})
    try:
        url, name = repo_agent.validate_repo_url(body.repo_url)
    except RepoError as e:
        raise HTTPException(422, str(e)) from e
    repo = db.scalar(select(Repository).where(Repository.user_id == user.id, Repository.url == url))
    if not repo:
        repo = Repository(user_id=user.id, url=url, name=name)
        db.add(repo)
        db.flush()
    if url.startswith("https://github.com/"):
        grant = _granted_installation(db, user, name)
        repo.installation_id, repo.private = (grant[0], grant[1]) if grant else (None, False)
    job = AnalysisJob(repository_id=repo.id, user_id=user.id)
    db.add(job)
    db.commit()
    try:
        analyze_repository.delay(job.id)
    except Exception as e:
        log.exception("enqueue failed")
        job.status, job.error, job.finished_at = "failed", "Queue unavailable, please retry", utcnow()
        db.commit()
        raise HTTPException(503, "Job queue unavailable") from e
    return JobAccepted(job_id=job.id, status="queued", status_url=f"/jobs/{job.id}")


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    reap_stale_jobs(db)
    jobs = db.scalars(select(AnalysisJob).where(AnalysisJob.user_id == user.id).order_by(AnalysisJob.created_at.desc()).limit(50))
    return [job_out(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    reap_stale_jobs(db)
    return job_out(own_job(db, job_id, user))


@router.get("/jobs/{job_id}/logs", response_model=list[LogOut])
def get_logs(job_id: str, after: int = 0, db: Session = Depends(get_db), user: User = Depends(current_user)):
    own_job(db, job_id, user)
    return list(db.scalars(select(JobLog).where(JobLog.job_id == job_id, JobLog.id > after).order_by(JobLog.id)))


def issues_out(db: Session, job: AnalysisJob, user: User, rows: list[CodeIssue]) -> list[IssueOut]:
    ids = [r.id for r in rows]
    votes = dict(db.execute(select(IssueFeedback.issue_id, IssueFeedback.vote)
                            .where(IssueFeedback.user_id == user.id, IssueFeedback.issue_id.in_(ids))).all()) if ids else {}
    fps = {r.fingerprint for r in rows if r.fingerprint}
    findings = {f.fingerprint: f for f in db.scalars(select(Finding).where(
        Finding.repository_id == job.repository_id, Finding.fingerprint.in_(fps)))} if fps else {}
    out = []
    for r in rows:
        f = findings.get(r.fingerprint)
        out.append(IssueOut.model_validate(r).model_copy(update={
            "my_vote": votes.get(r.id), "finding_id": f.id if f else None, "finding_status": f.status if f else None}))
    return out


@router.get("/jobs/{job_id}/issues", response_model=list[IssueOut])
def get_issues(job_id: str, severity: str | None = None, category: str | None = None, file: str | None = None,
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = own_job(db, job_id, user)
    q = select(CodeIssue).where(CodeIssue.job_id == job_id)
    if severity:
        q = q.where(CodeIssue.severity == severity)
    if category:
        q = q.where(CodeIssue.category == category)
    if file:
        q = q.where(CodeIssue.file_path == repo_agent.normalize_rel(file))
    return issues_out(db, job, user, list(db.scalars(q.order_by(CodeIssue.file_path, CodeIssue.line_start))))


def _require_source(job: AnalysisJob) -> None:
    if job.source_purged_at:
        raise HTTPException(410, "Source files expired under the retention policy; findings are still available. Re-run the scan to browse code.")


@router.get("/jobs/{job_id}/tree", response_model=list[TreeEntry])
def get_tree(job_id: str, path: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = own_job(db, job_id, user)
    _require_source(job)
    root = repo_agent.job_root(job_id)
    try:
        d = repo_agent.safe_join(root, path)
    except RepoError as e:
        raise HTTPException(400, str(e)) from e
    if not d.is_dir():
        if not path.strip("/") and job.status in ("queued", "running"):
            return []
        raise HTTPException(404, "Directory not found")
    counts = Counter(db.scalars(select(CodeIssue.file_path).where(CodeIssue.job_id == job_id)))
    dir_counts: Counter = Counter()
    for fp, c in counts.items():
        parts = fp.split("/")
        for i in range(1, len(parts)):
            dir_counts["/".join(parts[:i])] += c
    out: list[TreeEntry] = []
    for child in d.iterdir():
        if child.is_symlink() or child.name in SKIP_DIRS:
            continue
        rel = child.relative_to(root.resolve()).as_posix()
        if child.is_dir():
            out.append(TreeEntry(name=child.name, path=rel, type="dir", issue_count=dir_counts.get(rel, 0)))
        elif child.suffix.lower() in LANGS or child.name.startswith(".env"):
            out.append(TreeEntry(name=child.name, path=rel, type="file", size=child.stat().st_size, issue_count=counts.get(rel, 0)))
    return sorted(out, key=lambda e: (e.type != "dir", e.name.lower()))


@router.get("/jobs/{job_id}/file", response_model=FileOut)
def get_file(job_id: str, path: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = own_job(db, job_id, user)
    _require_source(job)
    rel = repo_agent.normalize_rel(path)
    try:
        f = repo_agent.safe_join(repo_agent.job_root(job_id), rel)
    except RepoError as e:
        raise HTTPException(400, str(e)) from e
    if not f.is_file() or f.is_symlink():
        raise HTTPException(404, "File not found")
    if f.stat().st_size > 1_000_000:
        raise HTTPException(413, "File too large to display")
    text = repo_agent.read_text(f)
    if text is None:
        raise HTTPException(415, "Binary file")
    rows = list(db.scalars(select(CodeIssue).where(CodeIssue.job_id == job_id, CodeIssue.file_path == rel)
                           .order_by(CodeIssue.line_start)))
    return FileOut(path=rel, language=LANGS.get(PurePosixPath(rel).suffix.lower(), "plaintext"), content=text,
                   issues=issues_out(db, job, user, rows))


@router.websocket("/ws/jobs/{job_id}")
async def job_stream(ws: WebSocket, job_id: str):
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        await ws.close(code=4401)
        return
    with SessionLocal() as db:
        user = user_from_token(str(hello.get("token", "")) if isinstance(hello, dict) else "", db)
        job = db.get(AnalysisJob, job_id)
        if not user or not job or job.user_id != user.id:
            await ws.close(code=4401)
            return
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        async for ev in events.subscribe(job_id):
            await queue.put(ev)

    pump_task = asyncio.create_task(pump())
    try:
        await asyncio.sleep(0)
        last = 0
        with SessionLocal() as db:
            for row in db.scalars(select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.id)):
                await ws.send_json({**LogOut.model_validate(row).model_dump(mode="json"), "type": "log"})
                last = row.id
            status_now = db.get(AnalysisJob, job_id).status
        if status_now in ("completed", "failed"):
            await ws.send_json({"type": "done", "status": status_now})
            return
        while True:
            ev = await queue.get()
            if ev.get("type") == "done":
                await ws.send_json(ev)
                return
            if ev.get("type") == "ping":
                await ws.send_json(ev)
            elif ev["seq"] > last:
                last = ev["seq"]
                await ws.send_json({**ev, "type": "log"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        pump_task.cancel()
        try:
            await ws.close()
        except RuntimeError:
            pass
