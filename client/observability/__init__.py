"""
LLM Observability Client

Drop-in observability package for external projects audited by the
LLM Observability Audit Platform. Provides telemetry capture, directive
delivery, and compliance hooks.

See the README.md in the parent directory for setup instructions.
"""

import logging as _logging
import os as _os

__version__ = "1.0.0"

_VERSION_MARKER_FILENAME = ".observability-version"
_version_warning_emitted = False


def _check_version_drift() -> None:
    """Warn once if the sync marker disagrees with the installed __version__.

    Only runs in projects that have been synced via scripts/sync_client.py
    (which writes the marker). When the marker is absent (e.g., platform repo,
    test environments) this is a no-op.
    """
    global _version_warning_emitted
    if _version_warning_emitted:
        return
    try:
        package_dir = _os.path.dirname(_os.path.abspath(__file__))
        marker = _os.path.join(package_dir, "..", _VERSION_MARKER_FILENAME)
        if not _os.path.isfile(marker):
            return
        with open(marker, "r", encoding="utf-8") as fh:
            marker_version = fh.read().strip()
        if marker_version and marker_version != __version__:
            _logging.getLogger("observability").warning(
                "observability version drift: installed %s, marker %s at %s. "
                "Run scripts/sync_client.py from the platform to resync.",
                __version__, marker_version, marker,
            )
    except Exception:
        pass
    finally:
        _version_warning_emitted = True


_check_version_drift()


from observability.schemas import (
    ToolCallEvent, SessionSummary, HallucinationEvent, AgentSpawnEvent,
    EvalResult, CodeChangeEvent, BugEvent,
)
from observability.client import ObservabilityClient
from observability.project_stream_client import ProjectStreamClient

__all__ = [
    "__version__",
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
