from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AnalysisJob, CodeIssue, Finding, IssueFeedback, Repository, User, utcnow
from ..schemas import FeedbackIn, FindingStatusIn
from ..security import current_user

router = APIRouter(tags=["feedback"])


def _own_issue(db: Session, issue_id: int, user: User) -> CodeIssue:
    issue = db.get(CodeIssue, issue_id)
    if not issue or db.get(AnalysisJob, issue.job_id).user_id != user.id:
        raise HTTPException(404, "Issue not found")
    return issue


@router.put("/issues/{issue_id}/feedback")
def put_feedback(issue_id: int, body: FeedbackIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.vote not in (1, -1):
        raise HTTPException(422, "vote must be 1 or -1")
    issue = _own_issue(db, issue_id, user)
    fb = db.scalar(select(IssueFeedback).where(IssueFeedback.issue_id == issue_id, IssueFeedback.user_id == user.id))
    if fb is None:
        fb = IssueFeedback(issue_id=issue_id, user_id=user.id, vote=body.vote)
        db.add(fb)
    fb.vote, fb.comment, fb.source, fb.prompt_version, fb.updated_at = (body.vote, body.comment, issue.source,
                                                                         issue.prompt_version, utcnow())
    db.commit()
    return {"issue_id": issue_id, "vote": fb.vote}


@router.delete("/issues/{issue_id}/feedback", status_code=204)
def delete_feedback(issue_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _own_issue(db, issue_id, user)
    db.query(IssueFeedback).filter(IssueFeedback.issue_id == issue_id, IssueFeedback.user_id == user.id).delete()
    db.commit()
    return Response(status_code=204)


@router.patch("/findings/{finding_id}")
def set_finding(finding_id: int, body: FindingStatusIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    f = db.get(Finding, finding_id)
    if not f or db.get(Repository, f.repository_id).user_id != user.id:
        raise HTTPException(404, "Finding not found")
    f.status = body.status
    f.resolved_at = None
    db.commit()
    return {"id": f.id, "status": f.status}
