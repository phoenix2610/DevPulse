from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.llm import prompts
from app.observability import tracing
from conftest import URL, register
from fakes import FakeProvider, install

NEW_SYSTEM = "You are a meticulous security reviewer (v3). Standards: $standards"


def test_file_prompts_render_safely():
    p = prompts.get("security_reviewer")
    assert p.ref == "security_reviewer@v2"
    system, user = p.render(standards="- A03", files="def f(): return {'$x': 1}")
    assert "- A03" in system and "{'$x': 1}" in user


def test_runtime_publish_activate_and_rollback(client, fake_clone, monkeypatch):
    from app.cli import main as cli
    admin, _ = register(client, "admin@example.com")
    user, _ = register(client, "dev@example.com")
    body0 = {"version": "v9", "system": NEW_SYSTEM, "user_template": "$files"}
    assert client.post("/prompts/security_reviewer/versions", json=body0, headers=admin).status_code == 403
    assert cli(["promote-admin", "admin@example.com"]) == 0
    body = {"version": "v3", "system": NEW_SYSTEM, "user_template": "Files:\n$files", "activate": True}
    assert client.post("/prompts/security_reviewer/versions", json=body, headers=user).status_code == 403
    assert client.post("/prompts/security_reviewer/versions", json={**body, "user_template": "no files"}, headers=admin).status_code == 422
    assert client.post("/prompts/security_reviewer/versions", json=body, headers=admin).status_code == 201
    assert client.post("/prompts/security_reviewer/versions", json=body, headers=admin).status_code == 409

    (p,) = install(monkeypatch, FakeProvider())
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=user).json()["job_id"]
    assert any("meticulous security reviewer (v3)" in c["system"] for c in p.calls)
    issues = client.get(f"/jobs/{job_id}/issues", headers=user).json()
    assert {i["prompt_version"] for i in issues if i["source"] == "anthropic"} >= {"security_reviewer@v3"}

    r = client.post("/prompts/security_reviewer/activate/v2", headers=admin)
    assert r.json()["active"] == "security_reviewer@v2"
    listing = {x["name"]: x for x in client.get("/prompts", headers=admin).json()}
    assert listing["security_reviewer"]["active"] == "security_reviewer@v2"


def test_traces_capture_llm_cost_and_latency(client, fake_clone, monkeypatch):
    exporter = InMemorySpanExporter()
    tracing.setup(SimpleSpanProcessor(exporter))
    install(monkeypatch, FakeProvider())
    h, _ = register(client)
    r = client.post("/jobs", json={"repo_url": URL}, headers=h)
    assert len(r.headers["x-trace-id"]) == 32
    job = client.get(f"/jobs/{r.json()['job_id']}", headers=h).json()
    spans = exporter.get_finished_spans()
    chat = [s for s in spans if s.name == "gen_ai.chat"]
    assert chat and chat[0].attributes["gen_ai.usage.input_tokens"] == 1200
    assert chat[0].attributes["llm.cost_usd"] > 0
    assert {s.attributes["prompt.version"] for s in chat} == {"security_reviewer@v2", "architecture_reviewer@v1"}
    names = {s.name for s in spans}
    assert {"analysis_job", "agent.parse_repo", "agent.security_review", "llm.review"} <= names
    job_span = next(s for s in spans if s.name == "analysis_job")
    assert job["trace_id"] == format(job_span.context.trace_id, "032x")
    assert all(s.context.trace_id == job_span.context.trace_id for s in chat)
