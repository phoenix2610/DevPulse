import time
from dataclasses import dataclass
from typing import Callable

from .config import Tier
from .kv import get_redis

_BUCKET = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local force = tonumber(ARGV[4]) or 0
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1]) or capacity
local ts = tonumber(b[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed, wait = 0, 0
if cost <= tokens or force == 1 then
  tokens = math.min(capacity, tokens - cost)
  allowed = 1
else
  wait = (cost - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 60)
return {allowed, tostring(wait), tostring(tokens)}
"""


@dataclass
class Decision:
    allowed: bool
    wait_s: float
    remaining: float


class TokenBucket:
    def __init__(self, name: str, capacity: float, refill_per_s: float):
        self.name, self.capacity, self.rate = name, capacity, refill_per_s
        self._script = get_redis().register_script(_BUCKET)

    @classmethod
    def llm_tokens(cls, tier: Tier) -> "TokenBucket":
        return cls("llm", tier.llm_tokens_per_minute, tier.llm_tokens_per_minute / 60)

    def try_acquire(self, key: str, cost: float, force: bool = False) -> Decision:
        allowed, wait, remaining = self._script(keys=[f"tb:{self.name}:{key}"],
                                                args=[self.capacity, self.rate, cost, int(force)])
        return Decision(bool(int(allowed)), float(wait), float(remaining))

    def acquire(self, key: str, cost: float, max_wait_s: float, on_wait: Callable[[float], None] | None = None,
                sleep: Callable[[float], None] = time.sleep) -> bool:
        if cost > self.capacity:
            return False
        waited = 0.0
        while True:
            d = self.try_acquire(key, cost)
            if d.allowed:
                return True
            if waited + d.wait_s > max_wait_s:
                return False
            if on_wait:
                on_wait(d.wait_s)
            sleep(d.wait_s)
            waited += d.wait_s

    def reconcile(self, key: str, estimated: float, actual: float) -> None:
        if actual != estimated:
            self.try_acquire(key, actual - estimated, force=True)


class CircuitBreaker:

    def __init__(self, name: str, threshold: int, cooldown_s: int):
        self.key, self.threshold, self.cooldown = f"cb:{name}", threshold, cooldown_s

    def is_open(self) -> bool:
        return bool(get_redis().exists(f"{self.key}:open"))

    def record_success(self) -> None:
        get_redis().delete(f"{self.key}:fails", f"{self.key}:open")

    def record_failure(self) -> bool:
        r = get_redis()
        fails = r.incr(f"{self.key}:fails")
        r.expire(f"{self.key}:fails", self.cooldown * 4)
        if fails >= self.threshold:
            r.set(f"{self.key}:open", 1, ex=self.cooldown)
            r.delete(f"{self.key}:fails")
            return True
        return False


def request_limiter(per_minute: int) -> TokenBucket:
    return TokenBucket("req", per_minute, per_minute / 60)
