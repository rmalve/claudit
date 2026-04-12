#!/bin/bash
# ── LLM Observability Audit Platform — Environment Variables ──
#
# Copy this file, fill in the values, and source it before running
# your project's Claude Code sessions.
#
# Usage:
#   cp env-template.sh .env.audit
#   # Edit .env.audit with your values
#   source .env.audit

# Required: your project's unique identifier (lowercase, alphanumeric, hyphens)
export OBSERVABILITY_PROJECT=""

# QDrant connection (default: local Docker instance)
export QDRANT_URL="http://localhost:6333"

# Redis connection for audit directive delivery
export REDIS_URL="redis://localhost:6379"
export REDIS_USERNAME="project-${OBSERVABILITY_PROJECT}"
export REDIS_PASSWORD=""  # generated during onboarding

# Optional: project root override (defaults to auto-detection)
# export PROJECT_ROOT="/path/to/your/project"

# Optional: OpenTelemetry export mode (none, console, otlp, prometheus)
# export OTEL_EXPORT_MODE="none"
# export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
