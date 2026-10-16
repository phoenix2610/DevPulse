import os
import threading

import pytest

from app.ratelimit import CircuitBreaker, TokenBucket


def test_bucket_burst_wait_and_reconcile():
    b = TokenBucket("t", 100, 10)
    assert b.try_acquire("u", 60).allowed and not b.try_acquire("u", 60).allowed
    assert 1.9 < b.try_acquire("u", 60).wait_s <= 2.1
    b.reconcile("u", estimated=60, actual=10)
    assert b.try_acquire("u", 60).allowed
    b.reconcile("u", estimated=0, actual=500)
    assert b.try_acquire("u", 1).wait_s > 40
    assert b.try_acquire("other-user", 100).allowed


def test_acquire_throttles_instead_of_failing():
    b = TokenBucket("t2", 100, 10)
    b.try_acquire("u", 100)
    slept = []
    assert b.acquire("u", 30, max_wait_s=1, sleep=slept.append) is False and slept == []
    b2 = TokenBucket("t3", 100, 1000)
    b2.try_acquire("u", 100)
    assert b2.acquire("u", 50, max_wait_s=5, on_wait=slept.append)
    assert b.acquire("u", 101, max_wait_s=999) is False


def test_bucket_is_atomic_under_concurrency():
    b = TokenBucket("t4", 50, 0.001)
    wins = []
    threads = [threading.Thread(target=lambda: wins.append(b.try_acquire("u", 1).allowed)) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(wins) == 50


def test_circuit_breaker():
    cb = CircuitBreaker("p", threshold=2, cooldown_s=30)
    cb.record_failure()
    assert not cb.is_open()
    assert cb.record_failure() and cb.is_open()
    cb.record_success()
    assert not cb.is_open()


@pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="set TEST_REDIS_URL to run against a real Redis")
def test_bucket_on_real_redis(monkeypatch):
    import redis

    from app import ratelimit
    r = redis.Redis.from_url(os.environ["TEST_REDIS_URL"])
    r.flushdb()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: r)
    b = TokenBucket("real", 50, 0.001)
    wins = []
    threads = [threading.Thread(target=lambda: wins.append(b.try_acquire("u", 1).allowed)) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(wins) == 50
