from sqlalchemy import cast, select
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import DocChunk
from .embeddings import cosine, embed
from .standards_data import STANDARDS


def seed_standards(db: Session) -> int:
    existing = set(db.scalars(select(DocChunk.title)))
    added = 0
    for source, title, content in STANDARDS:
        if title in existing:
            continue
        db.add(DocChunk(source=source, title=title, content=content, embedding=embed(f"{title}. {content}")))
        added += 1
    db.commit()
    return added


def retrieve(db: Session, query: str, k: int = 4, source: str | None = None) -> list[DocChunk]:
    qv = embed(query)
    stmt = select(DocChunk)
    if source:
        stmt = stmt.where(DocChunk.source == source)
    if db.bind.dialect.name == "postgresql":
        return list(db.scalars(stmt.order_by(cast(DocChunk.embedding, Vector(get_settings().embedding_dim)).cosine_distance(qv)).limit(k)))
    rows = list(db.scalars(stmt))
    rows.sort(key=lambda r: cosine(qv, r.embedding), reverse=True)
    return rows[:k]
