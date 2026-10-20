import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from ..compliance.sanitizer import EgressBlocked, Sanitizer, egress_guard, make_sanitizer
from ..config import Tier, get_settings
from ..db import SessionLocal
from ..models import LLMCall
from ..observability.tracing import current_trace_id, span
from ..ratelimit import CircuitBreaker, TokenBucket
from . import prompts
from .pricing import cost_usd
from .providers import Completion, ProviderError, configured

BACKOFF_S = (1.0, 2.0, 4.0)
CHARS_PER_TOKEN = 3.5
EST_OUTPUT_TOKENS = 4000


class LLMIssue(BaseModel):
    file_path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: Literal["security", "architecture", "style", "bug"]
    title: str = Field(min_length=1, max_length=300)
    description: str
    suggestion: str | None = None
    rule_ref: str | None = Field(default=None, max_length=100)


@dataclass
class Outcome:
    issues: list[dict] | None
    reason: str = "ok"
    provider: str | None = None


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def number_lines(files: dict[str, str]) -> str:
    return "\n\n".join(f"=== FILE: {p} ===\n" + "\n".join(f"{i}: {ln}" for i, ln in enumerate(src.split("\n"), 1))
                       for p, src in files.items())


def batches(files: dict[str, str], budget_chars: int = 60_000) -> list[dict[str, str]]:
    out, cur, size = [], {}, 0
    for p, src in files.items():
        if len(src) > budget_chars:
            continue
        if cur and size + len(src) > budget_chars:
            out.append(cur)
            cur, size = {}, 0
        cur[p] = src
        size += len(src)
    if cur:
        out.append(cur)
    return out


def validate(raw: list, files: dict[str, str]) -> list[dict]:
    out = []
    for item in raw:
        try:
            it = LLMIssue(**item)
        except (ValidationError, TypeError):
            continue
        src = files.get(it.file_path)
        if src is None:
            continue
        n = max(1, len(src.splitlines()))
        start = min(it.line_start, n)
        d = it.model_dump()
        d.update(line_start=start, line_end=max(start, min(it.line_end, n)),
                 suggestion=it.suggestion or None, rule_ref=it.rule_ref or None)
        out.append(d)
    return out


@dataclass
class ReviewRouter:
    job_id: str | None
    user_id: int
    tier: Tier
    emit: Callable[..., None] = lambda *a, **k: None
    sleep: Callable[[float], None] = lambda s: time.sleep(s)
    sanitizer: Sanitizer = field(default_factory=lambda: make_sanitizer(get_settings().pii_engine))
    stats: Counter = field(default_factory=Counter)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        s = get_settings()
        self.providers = configured()
        self.bucket = TokenBucket.llm_tokens(self.tier)
        self.budget_left = self.tier.max_llm_tokens_per_job
        self.breakers = {p.name: CircuitBreaker(p.name, s.circuit_failure_threshold, s.circuit_cooldown_s)
                         for p in self.providers}

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def _record(self, *, agent, provider, model, prompt_ref, status, error=None, c: Completion | None = None,
                latency_ms=0, redactions=None):
        with SessionLocal() as db:
            db.add(LLMCall(job_id=self.job_id, user_id=self.user_id, agent=agent, provider=provider, model=model,
                           prompt_version=prompt_ref, status=status, error=(error or "")[:2000] or None,
                           input_tokens=c.input_tokens if c else 0, output_tokens=c.output_tokens if c else 0,
                           cost_usd=cost_usd(provider, c.model, c.input_tokens, c.output_tokens) if c else 0.0,
                           latency_ms=c.latency_ms if c else latency_ms, redactions=redactions,
                           trace_id=current_trace_id()))
            db.commit()

    def review(self, agent: str, prompt_name: str, standards: str, files: dict[str, str]) -> Outcome:
        if not self.providers:
            return Outcome(None, "no_providers")
        with span("llm.review", **{"agent.name": agent, "review.files": len(files)}) as sp:
            with self._lock:
                before = Counter(self.sanitizer.counts)
                clean = {p: self.sanitizer.sanitize(src) for p, src in files.items()}
                redactions = dict(self.sanitizer.counts - before)
            listing = number_lines(clean)
            sp.set_attribute("compliance.redactions", sum(redactions.values()))
            try:
                egress_guard(listing)
            except EgressBlocked as e:
                self.stats["egress_blocked"] += 1
                self.emit(f"Compliance guard blocked an LLM request: {e}", agent=agent, level="warn")
                return Outcome(None, "egress_blocked")

            prompt = prompts.get(prompt_name)
            system, user = prompt.render(standards=standards, files=listing)
            est = estimate_tokens(system + user) + min(int(prompt.params.get("max_tokens", 16000)), EST_OUTPUT_TOKENS)
            if est > self.budget_left:
                self.stats["job_budget"] += 1
                return Outcome(None, "job_budget")
            ok = self.bucket.acquire(str(self.user_id), est, self.tier.max_throttle_wait_s, sleep=self.sleep,
                                     on_wait=lambda w: self.emit(f"Token budget exhausted for this minute; throttling "
                                                                 f"{w:.0f}s instead of failing", agent=agent, level="warn"))
            if not ok:
                self.stats["throttled"] += 1
                return Outcome(None, "throttled")

            for idx, provider in enumerate(self.providers):
                cb = self.breakers[provider.name]
                if cb.is_open():
                    self._record(agent=agent, provider=provider.name, model=provider.model, prompt_ref=prompt.ref,
                                 status="circuit_open")
                    continue
                for attempt in range(get_settings().llm_retries + 1):
                    with span("gen_ai.chat", **{"gen_ai.system": provider.name, "gen_ai.request.model": provider.model,
                                                "gen_ai.operation.name": "chat", "prompt.version": prompt.ref,
                                                "retry.attempt": attempt}) as call_span:
                        t0 = time.monotonic()
                        try:
                            c = provider.complete(system, user, prompt.params)
                        except ProviderError as e:
                            call_span.set_attribute("error.type", e.status)
                            self._record(agent=agent, provider=provider.name, model=provider.model, prompt_ref=prompt.ref,
                                         status=e.status, error=str(e), latency_ms=int((time.monotonic() - t0) * 1000),
                                         redactions=redactions)
                            if e.retryable and attempt < get_settings().llm_retries:
                                self.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                                continue
                            if cb.record_failure():
                                self.emit(f"Circuit opened for {provider.name} after repeated failures", agent=agent, level="warn")
                            break
                        call_span.set_attribute("gen_ai.response.model", c.model)
                        call_span.set_attribute("gen_ai.usage.input_tokens", c.input_tokens)
                        call_span.set_attribute("gen_ai.usage.output_tokens", c.output_tokens)
                        call_span.set_attribute("llm.cost_usd", cost_usd(provider.name, c.model, c.input_tokens, c.output_tokens))
                    cb.record_success()
                    self._record(agent=agent, provider=provider.name, model=c.model, prompt_ref=prompt.ref,
                                 status="ok", c=c, redactions=redactions)
                    actual = c.input_tokens + c.output_tokens
                    self.bucket.reconcile(str(self.user_id), est, actual)
                    self.budget_left -= actual
                    self.stats[f"provider:{provider.name}"] += 1
                    if idx > 0:
                        self.stats["failovers"] += 1
                        self.emit(f"Primary LLM unavailable; served by fallback provider '{provider.name}'", agent=agent, level="warn")
                    issues = validate(c.data["issues"], clean)
                    for i in issues:
                        i.update(source=provider.name, prompt_version=prompt.ref)
                    return Outcome(issues, "ok", provider.name)
            self.bucket.reconcile(str(self.user_id), est, 0)
            self.stats["providers_unavailable"] += 1
            return Outcome(None, "providers_unavailable")
