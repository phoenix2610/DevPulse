from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

from ..config import get_settings

_provider: TracerProvider | None = None


def setup(extra_processor: SpanProcessor | None = None) -> TracerProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        _provider = TracerProvider(resource=Resource.create({"service.name": s.otel_service_name}))
        if s.otel_exporter_otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            _provider.add_span_processor(BatchSpanProcessor(
                OTLPSpanExporter(endpoint=s.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(_provider)
    if extra_processor:
        _provider.add_span_processor(extra_processor)
    return _provider


def tracer():
    setup()
    return trace.get_tracer("ai-code-reviewer")


@contextmanager
def span(name: str, **attrs):
    with tracer().start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v)
        yield sp


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
