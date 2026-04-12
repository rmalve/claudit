"""
OpenTelemetry metrics and tracing for agent observability.

Provides standardized OTel instrumentation with:
- Metrics: counters, histograms via OTel SDK
- Traces: spans for tool calls, agent spawns, sessions
- Export: OTLP (to any OTel-compatible backend) + optional Prometheus

Configured via environment variables:
    OTEL_EXPORTER_OTLP_ENDPOINT: OTLP collector endpoint (default: none)
    OTEL_SERVICE_NAME: service name for traces (default: llm-agent-observability)
    OTEL_EXPORT_MODE: console, otlp, prometheus, none (default: none)
"""

import os
from functools import lru_cache

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes

try:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    HAS_OTLP = True
except ImportError:
    HAS_OTLP = False

try:
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "llm-agent-observability")
OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
EXPORT_MODE = os.environ.get("OTEL_EXPORT_MODE", "none")


def _build_resource() -> Resource:
    """Build OTel resource with service metadata."""
    return Resource.create({
        ResourceAttributes.SERVICE_NAME: SERVICE_NAME,
        ResourceAttributes.SERVICE_VERSION: "0.1.0",
        "deployment.environment": os.environ.get("OTEL_ENV", "development"),
    })


@lru_cache(maxsize=1)
def _init_providers() -> tuple:
    """Initialize OTel trace and metric providers (once)."""
    resource = _build_resource()

    # ── Trace provider ──
    tracer_provider = TracerProvider(resource=resource)

    if EXPORT_MODE == "otlp" and HAS_OTLP and OTLP_ENDPOINT:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{OTLP_ENDPOINT}/v1/traces"))
        )
    elif EXPORT_MODE == "console":
        tracer_provider.add_span_processor(
            BatchSpanProcessor(ConsoleSpanExporter())
        )

    trace.set_tracer_provider(tracer_provider)

    # ── Metrics provider ──
    readers = []

    if EXPORT_MODE == "otlp" and HAS_OTLP and OTLP_ENDPOINT:
        readers.append(PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{OTLP_ENDPOINT}/v1/metrics"),
            export_interval_millis=int(os.environ.get("OTEL_METRICS_EXPORT_INTERVAL_MS", "30000")),
        ))
    elif EXPORT_MODE == "console":
        readers.append(PeriodicExportingMetricReader(
            ConsoleMetricExporter(),
            export_interval_millis=60000,
        ))

    if EXPORT_MODE == "prometheus" and HAS_PROMETHEUS:
        readers.append(PrometheusMetricReader())

    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(meter_provider)

    return tracer_provider, meter_provider


def get_tracer(name: str = "agent.observability") -> trace.Tracer:
    """Get an OTel tracer for creating spans."""
    _init_providers()
    return trace.get_tracer(name)


def get_meter(name: str = "agent.observability") -> metrics.Meter:
    """Get an OTel meter for creating metrics."""
    _init_providers()
    return metrics.get_meter(name)


@lru_cache(maxsize=1)
def _instruments():
    """Create all metric instruments (once)."""
    meter = get_meter()

    return {
        "tool_calls_total": meter.create_counter(
            name="agent.tool_calls.total",
            description="Total tool calls by tool name, agent, and status",
            unit="1",
        ),
        "hallucinations_total": meter.create_counter(
            name="agent.hallucinations.total",
            description="Total hallucinations detected by type and agent",
            unit="1",
        ),
        "agent_spawns_total": meter.create_counter(
            name="agent.spawns.total",
            description="Total sub-agent launches",
            unit="1",
        ),
        "evals_total": meter.create_counter(
            name="agent.evals.total",
            description="Total eval checks by name and result",
            unit="1",
        ),
        "sessions_total": meter.create_counter(
            name="agent.sessions.total",
            description="Total completed sessions",
            unit="1",
        ),
        "files_modified_total": meter.create_counter(
            name="agent.files_modified.total",
            description="Total files created or modified",
            unit="1",
        ),
        "tool_call_duration": meter.create_histogram(
            name="agent.tool_call.duration",
            description="Tool call duration",
            unit="ms",
        ),
        "session_duration": meter.create_histogram(
            name="agent.session.duration",
            description="Session duration",
            unit="s",
        ),
    }


# ── Recording functions ──

def record_tool_call(tool_name: str, agent: str, status: str,
                     duration_ms: float | None, project: str) -> None:
    """Record a single tool call."""
    instruments = _instruments()
    attrs = {"tool_name": tool_name, "agent": agent, "status": status, "project": project}
    instruments["tool_calls_total"].add(1, attrs)
    if duration_ms is not None:
        instruments["tool_call_duration"].record(duration_ms, attrs)


def record_hallucination(h_type: str, agent: str, severity: str,
                         project: str) -> None:
    """Record a hallucination detection."""
    instruments = _instruments()
    instruments["hallucinations_total"].add(1, {
        "type": h_type, "agent": agent, "severity": severity, "project": project,
    })


def record_agent_spawn(parent: str, child: str, project: str) -> None:
    """Record a sub-agent launch."""
    instruments = _instruments()
    instruments["agent_spawns_total"].add(1, {
        "parent_agent": parent, "child_agent": child, "project": project,
    })


def record_eval(eval_name: str, agent: str, passed: bool, project: str) -> None:
    """Record an eval result."""
    instruments = _instruments()
    instruments["evals_total"].add(1, {
        "eval_name": eval_name, "agent": agent, "result": "pass" if passed else "fail",
        "project": project,
    })


def record_session_end(duration_seconds: float, tool_failures: int,
                       project: str) -> None:
    """Record session completion."""
    instruments = _instruments()
    instruments["sessions_total"].add(1, {"project": project})
    instruments["session_duration"].record(duration_seconds, {"project": project})


def flush_metrics() -> None:
    """Force flush all pending metrics and traces."""
    _, meter_provider = _init_providers()
    tracer_provider = trace.get_tracer_provider()
    if hasattr(meter_provider, "force_flush"):
        meter_provider.force_flush()
    if hasattr(tracer_provider, "force_flush"):
        tracer_provider.force_flush()
