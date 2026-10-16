import time
import uuid
from contextlib import contextmanager
from functools import lru_cache

import redis

from .config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    url = get_settings().redis_url
    if url:
        return redis.Redis.from_url(url, socket_timeout=5, health_check_interval=30)
    import fakeredis

    return fakeredis.FakeRedis()


_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"


@contextmanager
def lock(name: str, ttl_s: int = 10, wait_s: float = 5.0):
    r, token, key = get_redis(), uuid.uuid4().hex, f"lock:{name}"
    deadline = time.monotonic() + wait_s
    while not r.set(key, token, nx=True, px=ttl_s * 1000):
        if time.monotonic() > deadline:
            raise TimeoutError(f"could not acquire {key}")
        time.sleep(0.05)
    try:
        yield
    finally:
        r.eval(_RELEASE, 1, key, token)


def put_once(key: str, value: str, ttl_s: int) -> None:
    get_redis().set(key, value, ex=ttl_s)


def pop_once(key: str) -> str | None:
    v = get_redis().getdel(key)
    return v.decode() if isinstance(v, bytes) else v
