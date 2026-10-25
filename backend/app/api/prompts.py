from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..llm import prompts
from ..models import PromptVersion, User
from ..schemas import PromptVersionIn
from ..security import require_admin

router = APIRouter(prefix="/prompts", tags=["prompt-ops"])


@router.get("")
def list_prompts(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    out = []
    for name in prompts.list_names():
        data = prompts.load_file(name)
        rows = db.scalars(select(PromptVersion).where(PromptVersion.name == name).order_by(PromptVersion.created_at)).all()
        active = prompts.get(name)
        out.append({"name": name, "active": active.ref,
                    "versions": [{"version": v, "origin": "file"} for v in data["versions"]] +
                                [{"version": r.version, "origin": "runtime", "is_active": r.is_active} for r in rows]})
    return out


@router.post("/{name}/versions", status_code=201)
def publish(name: str, body: PromptVersionIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        file_versions = prompts.load_file(name)["versions"]
    except KeyError as e:
        raise HTTPException(404, "Unknown prompt") from e
    if body.version in file_versions or db.scalar(select(PromptVersion).where(PromptVersion.name == name,
                                                                              PromptVersion.version == body.version)):
        raise HTTPException(409, "Version already exists")
    if "$files" not in body.user_template:
        raise HTTPException(422, "user_template must include $files")
    if body.activate:
        db.execute(update(PromptVersion).where(PromptVersion.name == name).values(is_active=False))
    db.add(PromptVersion(name=name, version=body.version, system=body.system, user_template=body.user_template,
                         params=body.params, is_active=body.activate, created_by=admin.id))
    db.commit()
    prompts.invalidate(name)
    return {"name": name, "version": body.version, "active": body.activate}


@router.post("/{name}/activate/{version}")
def activate(name: str, version: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    db.execute(update(PromptVersion).where(PromptVersion.name == name).values(is_active=False))
    row = db.scalar(select(PromptVersion).where(PromptVersion.name == name, PromptVersion.version == version))
    if row:
        row.is_active = True
    elif version != prompts.load_file(name).get("active"):
        db.rollback()
        raise HTTPException(404, "Runtime version not found (file versions are selected via the YAML `active` key)")
    db.commit()
    prompts.invalidate(name)
    return {"name": name, "active": prompts.get(name).ref}
