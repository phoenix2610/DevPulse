"""Operator commands.  Usage:
    python -m app.cli promote-admin <email>
    python -m app.cli set-tier <email> <free|pro|enterprise>
    python -m app.cli maintenance          # reap stuck jobs + delete expired clones now
"""
import sys

from sqlalchemy import select

from .config import TIERS
from .db import SessionLocal, init_db
from .models import User


def main(argv: list[str]) -> int:
    if argv[:1] == ["maintenance"]:
        init_db()
        from .worker.tasks import maintenance
        print(maintenance.run())
        return 0
    if len(argv) < 2 or argv[0] not in ("promote-admin", "set-tier"):
        print(__doc__)
        return 2
    init_db()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == argv[1].lower()))
        if not user:
            print(f"no user {argv[1]}")
            return 1
        if argv[0] == "promote-admin":
            user.is_admin = True
        else:
            if len(argv) < 3 or argv[2] not in TIERS:
                print(f"tier must be one of {sorted(TIERS)}")
                return 2
            user.tier = argv[2]
        db.commit()
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
