import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.trace import SpanKind

from .api import analytics, auth, feedback, integrations, jobs, prompts
from .config import get_settings
from .db import SessionLocal, init_db
from .observability import tracing


@asynccontextmanager
async def lifespan(_: FastAPI):
    tracing.setup()
    init_db()
    from .agents.rag import seed_standards

    with SessionLocal() as db:
        seed_standards(db)
    yield


app = FastAPI(title="Autonomous AI Code Reviewer", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["Retry-After", "X-Trace-Id"])


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    t0 = time.monotonic()
    with tracing.tracer().start_as_current_span(f"{request.method} {request.url.path}", kind=SpanKind.SERVER) as sp:
        response = await call_next(request)
        route = request.scope.get("route")
        sp.update_name(f"{request.method} {getattr(route, 'path', request.url.path)}")
        sp.set_attribute("http.request.method", request.method)
        sp.set_attribute("http.response.status_code", response.status_code)
        sp.set_attribute("http.server.duration_ms", int((time.monotonic() - t0) * 1000))
        if tid := tracing.current_trace_id():
            response.headers["X-Trace-Id"] = tid
        return response


for r in (auth.router, jobs.router, feedback.router, integrations.router, prompts.router, analytics.router):
    app.include_router(r)


@app.get("/health")
def health():
    return {"status": "ok"}
