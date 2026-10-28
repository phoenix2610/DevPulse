from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import analytics
from ..db import get_db
from ..models import User
from ..security import current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def overview(days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db), user: User = Depends(current_user)):
    return analytics.overview(db, user.id, days)
