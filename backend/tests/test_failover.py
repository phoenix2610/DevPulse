import sys
import types

import httpx

from app.db import SessionLocal
from app.llm import providers
from app.models import LLMCall
from conftest import URL
from fakes import FakeProvider, install


def run_job(client, h):
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    return client.get(f"/jobs/{job_id}", headers=h).json(), client.get(f"/jobs/{job_id}/issues", headers=h).json()


def no_sleep(monkeypatch):
    from app.llm import router
    monkeypatch.setattr(router.time, "sleep", lambda s: None)


def test_primary_success_records_cost(client, auth, fake_clone, monkeypatch):
    install(monkeypatch, FakeProvider("anthropic"))
    job, issues = run_job(client, auth[0])
    assert job["summary"]["llm_used"] and not job["summary"]["degraded"] and job["summary"]["providers"] == {"anthropic": 2}
    llm = [i for i in issues if i["source"] == "anthropic"]
    assert llm and llm[0]["prompt_version"] == "security_reviewer@v2"
    with SessionLocal() as db:
        calls = db.query(LLMCall).all()
    assert {c.status for c in calls} == {"ok"} and all(c.cost_usd == round(1200 / 1e6 * 4 + 300 / 1e6 * 20, 6) for c in calls)


def test_failover_to_self_hosted_after_retries(client, auth, fake_clone, monkeypatch):
    no_sleep(monkeypatch)
    primary, backup = install(monkeypatch, FakeProvider("anthropic", ["retry"]), FakeProvider("ollama", model="llama3.1:8b"))
    job, issues = run_job(client, auth[0])
    assert len(primary.calls) >= 3
    assert job["summary"]["providers"] == {"ollama": 2} and job["summary"]["failovers"] == 2
    assert any(i["source"] == "ollama" for i in issues)
    with SessionLocal() as db:
        statuses = [c.status for c in db.query(LLMCall).filter_by(provider="anthropic")]
    assert "error" in statuses


def test_circuit_breaker_skips_dead_provider(client, auth, fake_clone, monkeypatch):
    no_sleep(monkeypatch)
    primary, _ = install(monkeypatch, FakeProvider("anthropic", ["fatal"]), FakeProvider("ollama"))
    run_job(client, auth[0])
    calls_after_first = len(primary.calls)
    run_job(client, auth[0])
    run_job(client, auth[0])
    assert len(primary.calls) < calls_after_first * 3
    with SessionLocal() as db:
        assert db.query(LLMCall).filter_by(status="circuit_open").count() > 0


def test_all_providers_down_falls_back_to_static(client, auth, fake_clone, monkeypatch):
    no_sleep(monkeypatch)
    install(monkeypatch, FakeProvider("anthropic", ["fatal"]), FakeProvider("ollama", ["retry"]))
    job, issues = run_job(client, auth[0])
    assert job["status"] == "completed" and job["summary"]["degraded"] and not job["summary"]["llm_used"]
    assert any(i["title"] == "os.system call" for i in issues)


def test_refusal_is_not_retried(client, auth, fake_clone, monkeypatch):
    primary, _ = install(monkeypatch, FakeProvider("anthropic", ["refusal"]), FakeProvider("ollama"))
    job, _ = run_job(client, auth[0])
    assert len(primary.calls) == 2 and job["summary"]["providers"] == {"ollama": 2}


def test_hallucinated_paths_and_bad_items_are_dropped(client, auth, fake_clone, monkeypatch):
    install(monkeypatch, FakeProvider(issues=[
        {"file_path": "ghost.py", "line_start": 1, "line_end": 1, "severity": "high", "category": "security",
         "title": "x", "description": "d", "suggestion": "", "rule_ref": ""},
        {"file_path": "pkg/app.py", "line_start": 999, "line_end": 2000, "severity": "high", "category": "security",
         "title": "Clamped", "description": "d", "suggestion": "", "rule_ref": ""},
        {"file_path": "pkg/app.py", "line_start": 1, "line_end": 1, "severity": "scary", "category": "security",
         "title": "bad enum", "description": "d", "suggestion": "", "rule_ref": ""}]))
    _, issues = run_job(client, auth[0])
    llm = [i for i in issues if i["source"] == "anthropic"]
    assert [i["title"] for i in llm] == ["Clamped"] and llm[0]["line_start"] == llm[0]["line_end"] == 11


def test_throttle_degrades_gracefully_when_tier_exhausted(client, auth, fake_clone, monkeypatch):
    from app import config
    monkeypatch.setitem(config.TIERS, "free", config.Tier(llm_tokens_per_minute=100, max_llm_tokens_per_job=10**6,
                                                          max_concurrent_jobs=1, max_throttle_wait_s=1))
    install(monkeypatch, FakeProvider())
    job, issues = run_job(client, auth[0])
    assert job["status"] == "completed" and job["summary"]["throttled_batches"] >= 1 and job["summary"]["degraded"]
    assert issues


def test_job_budget_caps_llm_usage(client, auth, fake_clone, monkeypatch):
    from app import config
    monkeypatch.setitem(config.TIERS, "free", config.Tier(10**6, 10, 1, 10))
    (p,) = install(monkeypatch, FakeProvider())
    job, _ = run_job(client, auth[0])
    assert p.calls == [] and job["summary"]["budget_capped"]


def test_anthropic_provider_request_shape(monkeypatch):
    captured = {}

    class Msgs:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                                         usage=types.SimpleNamespace(input_tokens=10, output_tokens=5),
                                         content=[types.SimpleNamespace(type="text", text='{"issues": []}')])

    fake = types.SimpleNamespace(Anthropic=lambda **kw: types.SimpleNamespace(beta=types.SimpleNamespace(messages=Msgs())),
                                 RateLimitError=type("R", (Exception,), {}), APIStatusError=type("S", (Exception,), {}),
                                 APIConnectionError=type("C", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    c = providers.AnthropicProvider().complete("sys", "user", {"max_tokens": 16000, "effort": "medium"})
    assert c.data == {"issues": []} and "tool_choice" not in captured and "tools" not in captured
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert captured["extra_body"] == {"fallbacks": "default"} and captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["model"] == "claude-opus-5-5"

    def refusal(**kw):
        return types.SimpleNamespace(stop_reason="refusal", model="m", usage=types.SimpleNamespace(input_tokens=1, output_tokens=0), content=[])
    Msgs.create = lambda self, **kw: refusal()
    try:
        providers.AnthropicProvider().complete("s", "u", {})
        raise AssertionError("expected refusal")
    except providers.ProviderError as e:
        assert e.status == "refusal" and not e.retryable


def test_ollama_provider_over_http(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        import json
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": '{"issues": []}'}, "prompt_eval_count": 7, "eval_count": 3})

    monkeypatch.setattr(providers.OllamaProvider, "transport", httpx.MockTransport(handler))
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        c = providers.OllamaProvider().complete("s", "u", {})
    finally:
        get_settings.cache_clear()
    assert (c.input_tokens, c.output_tokens) == (7, 3) and seen["format"]["required"] == ["issues"] and seen["stream"] is False


def test_gemini_provider_over_http(monkeypatch):
    import json
    seen = {}
    reply = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"issues": []}'}]}}],
             "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "thoughtsTokenCount": 2}}

    def handler(req: httpx.Request):
        seen.update(json.loads(req.content), key=req.headers["x-goog-api-key"], path=req.url.path)
        return httpx.Response(200, json=reply)

    monkeypatch.setattr(providers.GeminiProvider, "transport", httpx.MockTransport(handler))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        c = providers.GeminiProvider().complete("s", "u", {})
        assert (c.input_tokens, c.output_tokens) == (7, 5) and seen["key"] == "test-key"
        assert seen["generationConfig"]["responseJsonSchema"]["required"] == ["issues"] and ":generateContent" in seen["path"]
        reply["candidates"][0]["finishReason"] = "SAFETY"
        try:
            providers.GeminiProvider().complete("s", "u", {})
            raise AssertionError("expected refusal")
        except providers.ProviderError as e:
            assert e.status == "refusal" and not e.retryable
    finally:
        get_settings.cache_clear()
