"""
LLM Observability Client

Drop-in observability package for external projects audited by the
LLM Observability Audit Platform. Provides telemetry capture, directive
delivery, and compliance hooks.

See the README.md in the parent directory for setup instructions.
"""

from observability.schemas import (
    ToolCallEvent, SessionSummary, HallucinationEvent, AgentSpawnEvent,
    EvalResult, CodeChangeEvent, BugEvent,
)
from observability.client import ObservabilityClient
from observability.project_stream_client import ProjectStreamClient

__all__ = [
    "ObservabilityClient",
    "ProjectStreamClient",
    "ToolCallEvent",
    "SessionSummary",
    "HallucinationEvent",
    "AgentSpawnEvent",
    "EvalResult",
    "CodeChangeEvent",
    "BugEvent",
]
