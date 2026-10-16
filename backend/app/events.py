import asyncio
import json
import threading
from collections import defaultdict
from typing import AsyncIterator

from .config import get_settings
from .db import SessionLocal
from sqlalchemy import update

from .kv import get_redis
from .models import AnalysisJob, JobLog

_mem_subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
_mem_lock = threading.Lock()
_mem_loops: dict[int, asyncio.AbstractEventLoop] = {}


def channel(job_id: str) -> str:
    return f"job:{job_id}"


def emit(job_id: str, message: str, *, agent: str = "system", level: str = "info", progress: int | None = None,
         status: str | None = None) -> dict:
    with SessionLocal() as db:
        row = JobLog(job_id=job_id, agent=agent, level=level, message=message, progress=progress)
        db.add(row)
        if progress is not None:
            db.execute(update(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.progress < progress)
                       .values(progress=progress))
        db.commit()
        event = {"seq": row.id, "agent": agent, "level": level, "message": message, "progress": progress,
                 "status": status, "ts": row.created_at.isoformat()}
    _publish(job_id, event)
    return event


def finish(job_id: str, status: str) -> None:
    _publish(job_id, {"seq": 10**12, "type": "done", "status": status})


def _publish(job_id: str, event: dict) -> None:
    s = get_settings()
    if s.redis_url:
        import redis

        try:
            get_redis().publish(channel(job_id), json.dumps(event))
        except redis.RedisError:
            pass
        return
    with _mem_lock:
        targets = list(_mem_subs.get(job_id, []))
    for q in targets:
        loop = _mem_loops.get(id(q))
        if loop:
            loop.call_soon_threadsafe(q.put_nowait, event)


async def subscribe(job_id: str) -> AsyncIterator[dict]:
    s = get_settings()
    if s.redis_url:
        import redis.asyncio as aioredis

        client = aioredis.Redis.from_url(s.redis_url)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel(job_id))
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if msg is None:
                    yield {"type": "ping", "seq": 0}
                    continue
                yield json.loads(msg["data"])
        finally:
            await pubsub.unsubscribe(channel(job_id))
            await client.aclose()
    else:
        q: asyncio.Queue = asyncio.Queue()
        _mem_loops[id(q)] = asyncio.get_running_loop()
        with _mem_lock:
            _mem_subs[job_id].append(q)
        try:
            while True:
                yield await q.get()
        finally:
            with _mem_lock:
                _mem_subs[job_id].remove(q)
            _mem_loops.pop(id(q), None)
