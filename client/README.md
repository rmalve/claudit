# Claudit Client

Everything an external project needs to integrate with the Claudit audit platform. This guide covers both sides: what the audit platform admin does in `llm-observability` and what happens in the external project.

## Prerequisites

- Python 3.11+
- Docker running (QDrant and Redis)
- Claude Code with hooks support
- Access to both the `llm-observability` repo and the external project

---

## Setup

### Step 1: Generate onboarding artifacts (audit platform)

From the `llm-observability` project root:

```bash
# Dry run — review what will be generated:
python scripts/onboard_project.py --project my-project --root /path/to/my-project

# Apply when ready:
python scripts/onboard_project.py --project my-project --root /path/to/my-project --apply
```

This does three things on the **audit platform** side:
- Appends a Redis ACL entry to `config/redis-acl.conf`
- Creates a project adapter in `adapters/`
- Registers the project as active in `config/projects.json`

And two things in the **external project**:
- Creates `.env.audit` with the generated credentials
- Creates `.claude/settings.json` with hook configuration (if one doesn't already exist)

**Save the generated password.** It is shown once and not stored.

### Step 2: Reload Redis ACL (audit platform)

```bash
docker restart llm-obs-redis
```

### Step 3: Sync the observability package (external project)

The external project keeps its own copy of `observability/` for portability — hooks in `.claude/settings.json` use relative paths (`observability/hooks/foo.py`) so the same configuration works on any machine, including remote hosts that do not share a filesystem with the audit platform.

From the audit platform, sync the package into the registered project:

```bash
# Preview what will change:
python scripts/sync_client.py --project my-project

# Apply:
python scripts/sync_client.py --project my-project --apply
```

The script preserves any project-specific files in `observability/` (standing directives, version archives, custom hooks) and writes a `.observability-version` marker at the project root so future syncs can detect drift.

**Re-run `sync_client.py` whenever the platform publishes updates.** The hooks log a WARNING if the installed package version disagrees with the marker, but there is no automatic sync — you control when updates land.

> ⚠️ **Don't run `sync_client.py --apply` while a Claude Code session is active in the target project.** Individual files are replaced atomically, but cross-file consistency during a running session isn't guaranteed — a hook that imports `observability.*` mid-sync may see a mix of old and new modules. Stop the session, sync, then resume.

(Legacy: the raw `cp -r llm-observability/client/observability /path/to/my-project/observability` still works for one-shot setup.)

Resulting structure:

```
my-project/
  observability/
    __init__.py
    hooks/
      __init__.py
      post_tool_use.py        # telemetry capture
      session_end.py          # session summary
      directive_intake.py     # reads directives from Audit Director
      directive_compliance.py # sends acknowledgments back
      test_runner.py          # runs tests, produces evals and bug events
      version_archive.py      # snapshots changed agent definitions
    schemas.py
    client.py
    qdrant_backend.py
    jsonl_parser.py
    metrics.py
    messages.py
    project_stream_client.py
    version_resolver.py
    hallucination_detector.py
    validation.py
    standing_directives.md       # created empty by onboard; populated by promotion system
  .claude/
    settings.json
  .env                        # add audit credentials here
  CLAUDE.md
```

### Step 4: Install dependencies (external project)

```bash
pip install -r client/requirements.txt
```

Or install individually:

```bash
pip install python-dotenv>=1.0 pydantic>=2.0 qdrant-client>=1.7 fastembed>=0.2 redis>=5.0 \
    opentelemetry-api>=1.20 opentelemetry-sdk>=1.20 \
    opentelemetry-exporter-otlp-proto-http>=1.20
```

### Step 5: Set environment variables (external project)

Add the credentials from `.env.audit` to your project's `.env` file:

```bash
# Required
OBSERVABILITY_PROJECT="my-project"
QDRANT_URL="http://localhost:6333"
REDIS_URL="redis://localhost:6379"
REDIS_USERNAME="project-my-project"
REDIS_PASSWORD="the-generated-password"

# Optional — override auto-detection of project root
# PROJECT_ROOT="/path/to/my-project"

# Optional — override agent name (defaults to "main")
# AGENT_NAME="lead-engineer"

# Optional — OpenTelemetry export (defaults to "none")
# OTEL_EXPORT_MODE="none"   # none | console | otlp | prometheus
# OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
```

The hooks load `.env` automatically via `python-dotenv`.

#### Connecting to a remote audit platform

If the external project runs on a different machine from the audit platform's QDrant + Redis instances, point `QDRANT_URL` and `REDIS_URL` at the platform's reachable network address instead of `localhost`:

```bash
QDRANT_URL="http://audit-platform.internal:6333"
REDIS_URL="redis://audit-platform.internal:6379"
```

- **Firewall / NAT**: Open outbound access from the remote project host to the platform's QDrant (default port 6333) and Redis (default port 6379).
- **ACL credentials are per-project**: `REDIS_USERNAME="project-my-project"` can only read/write that project's streams. Sharing a single audit platform across multiple remote projects is safe — each project is scoped to its own keyspace by Redis ACL.
- **Time sync**: Cross-machine telemetry relies on each host having an accurate clock. If the project host drifts, session ordering on the dashboard will too. Install `chrony` or equivalent.
- **Python**: The client hooks require Python 3.11+ (union type hints). Confirm `python --version` on the remote machine before onboarding.

### Step 6: Configure hooks (external project)

If the onboarding script created `.claude/settings.json`, you're done. If your project already has hooks, merge the entries from `config/settings-merge-snippet.json`.

| Hook | Event | Purpose |
|------|-------|---------|
| PostToolUse | Every tool call | Captures tool calls, agent spawns, code changes, data quality |
| PostToolUse (Write/Edit) | .py file changes | Runs tests (evals) and lint checks |
| Stop | Session end | Aggregates session summary from QDrant |
| PreToolUse | Every tool call | Snapshots changed agent definitions (version archiving) |
| PreToolUse | Every 10 tool calls | Checks for new directives and promotions from the Audit Director |
| PreToolUse (session start) | First tool call | Applies standing directive promotions, loads standing directives |

### Step 7: Add audit authority to CLAUDE.md (external project)

Paste the contents of `config/claude-md-audit-authority.md` near the top of the project's `CLAUDE.md`.

This establishes:
- The Audit Director's authority over agents in this project
- How directives are delivered (continuously during sessions, not just at start)
- Compliance deadlines (1 session for DIRECTIVEs, 2 sessions for RECOMMENDATIONs)
- That compliance is independently verified by the audit team
- Cooperation requirements and consequences for tampering

### Step 8: Add audit interface to each agent (external project)

Paste the contents of `config/skill-audit-interface.md` into each agent's skill definition or system prompt.

### Step 9: Initialize agent version tracking (external project)

If your project already has agent definitions in `.claude/agents/*.md`, run the initial version archive:

```bash
python scripts/version_archive.py --root /path/to/my-project
```

This creates a `.versions/` directory alongside each agent definition:

```
.claude/agents/
  architect.md                          # current definition
  architect.versions/
    INDEX.json                          # version index
    architect.v1.20260408-143000.md     # first snapshot
  api-engineer.md
  api-engineer.versions/
    INDEX.json
    api-engineer.v1.20260408-143000.md
```

The `version_archive.py` hook (configured in Step 6) will automatically snapshot any changed definitions at the start of each Claude Code session. You do not need to run the archiver manually after initial setup.

**Why this matters:** The Drift Detector and Policy Auditor correlate behavioral changes with agent definition changes. Without version data, `agent_version` fields in telemetry will be null and these auditors cannot do their primary job. The data quality validator flags missing version data as a HIGH severity issue.

### Step 10: Start the audit platform (audit platform)

```bash
# Per-session audit (default) — audits new/unaudited sessions only
python orchestrator.py

# Cross-session audit (on-demand) — trend analysis across session history
python orchestrator.py --mode cross-session

# Scope to specific projects
python orchestrator.py --projects my-project
python orchestrator.py --mode cross-session --projects my-project
```

Per-session mode runs trace, safety, policy, and hallucination auditors against unaudited sessions. Cross-session mode runs drift and cost auditors with progressive summarization (3 most recent sessions get raw access, older sessions use summaries only). Cross-session audits can also be triggered from the dashboard Overview page.

### Step 11: Verify end-to-end (both sides)

```python
# From the external project — verify QDrant:
from observability.client import ObservabilityClient
client = ObservabilityClient(project="my-project")
print(client.get_stats())
client.close()

# Verify Redis:
from observability.project_stream_client import ProjectStreamClient
client = ProjectStreamClient(project="my-project")
print("Redis connected:", client.ping())
client.close()
```

---

## What Gets Captured

| Data | Hook | QDrant Collection | When |
|------|------|-------------------|------|
| Every tool call | PostToolUse | `tool_calls` | Every tool invocation |
| Agent spawns with full prompt | PostToolUse | `agent_spawns` + `prompts` | When Agent tool is used |
| Code changes (Write/Edit diffs) | PostToolUse | `code_changes` | Write or Edit tool calls |
| Data quality issues | PostToolUse | `data_quality` | Missing required fields |
| Test pass rate eval | test_runner | `evals` | Every .py file Write/Edit |
| Lint check eval | test_runner | `evals` | Every .py file Write/Edit |
| Bug events | test_runner | `bugs` | Test failures only |
| Session summaries | Stop | `sessions` | Session end |
| Directive acknowledgments | directive_compliance | Redis `compliance:{project}` | Agent acks a directive |
| Promotion acknowledgments | directive_intake | Redis `promotion_ack:{project}` | Standing directive applied |
| Agent version snapshots | version_archive | `.claude/agents/*.versions/` | Session start (if changed) |

---

## Agent Definition Versioning

The audit platform tracks changes to agent definitions (`.claude/agents/*.md`) to correlate behavioral drift with definition changes. This is not optional — without version data, the Drift Detector and Policy Auditor cannot function.

### How It Works

```
Session start
  └─ PreToolUse hook fires
      └─ version_archive.py runs
          ├─ Scans .claude/agents/*.md
          ├─ Computes SHA-256 of each file
          ├─ Compares against last archived hash in INDEX.json
          ├─ If changed: copies file → .versions/{name}.v{N}.{timestamp}.md
          └─ Updates INDEX.json with new version entry

During session
  └─ PostToolUse hook fires
      └─ post_tool_use.py calls version_resolver
          ├─ Reads INDEX.json for current agent
          ├─ Tags every telemetry event with agent_version (e.g. "v3")
          └─ Tags with agent_version_path (path to versioned snapshot)
```

### INDEX.json Format

Each agent gets a `.versions/` directory with an `INDEX.json`:

```json
{
  "versions": [
    {
      "version": 1,
      "filename": "architect.v1.20260405-100000.md",
      "timestamp": "2026-04-05T10:00:00+00:00",
      "sha256": "a1b2c3..."
    },
    {
      "version": 2,
      "filename": "architect.v2.20260408-143000.md",
      "timestamp": "2026-04-08T14:30:00+00:00",
      "sha256": "d4e5f6..."
    }
  ]
}
```

Version numbers are monotonically increasing integers. Timestamps are UTC ISO-8601. The SHA-256 hash is of the agent definition file's contents at the time of archiving.

### What Gets Versioned

| Versioned | Not Versioned |
|-----------|---------------|
| `.claude/agents/*.md` (agent definitions) | `CLAUDE.md` (tracked separately by governance auditing) |
| | `.claude/settings.json` (tracked by policy auditor) |
| | Skills in other directories (not yet supported) |

### Manual Archiving

To archive agent definitions outside of the hook lifecycle:

```bash
# Archive all changed agents:
python scripts/version_archive.py --root /path/to/project

# Archive a specific agent:
python scripts/version_archive.py --root /path/to/project --agent architect

# Dry run — see what would be archived:
python scripts/version_archive.py --root /path/to/project --dry-run
```

### When Versions Are Missing

If `agent_version` is null in telemetry, the audit platform flags this:

- **Data quality validator** reports it as HIGH severity
- **Drift Detector** cannot correlate behavioral changes with definition changes
- **Policy Auditor** cannot verify agent definitions match governance requirements

Common causes:
- `version_archive.py` hook is not configured (check `.claude/settings.json`)
- `PROJECT_ROOT` env var is not set (version resolver can't find `.claude/agents/`)
- No `.versions/` directories exist yet (run `version_archive.py --root` manually)

---

## Directive Lifecycle

Directives arrive in your agent's context automatically (every 10 tool calls).

```
PENDING         → Directive issued, agent hasn't responded
ACKNOWLEDGED    → Agent said "I comply" (unverified)
VERIFICATION    → Audit team checking if behavior actually changed
VERIFIED        → Behavior change confirmed (or non-compliance flagged)
```

- **DIRECTIVEs**: Must acknowledge within the current session
- **RECOMMENDATIONs**: Must respond within 2 sessions
- Acknowledgment without behavioral change is flagged as verified non-compliance
- Conflicts should be escalated via `--conflict` flag, not silently ignored

### Acknowledging a directive:
```bash
python -m observability.hooks.directive_compliance \
    --directive-id "DIRECTIVE-ID" \
    --agent "your-agent-name" \
    --action "Description of what was done"
```

### Escalating a conflict:
```bash
python -m observability.hooks.directive_compliance \
    --directive-id "DIRECTIVE-ID" \
    --agent "your-agent-name" \
    --conflict "Specific reason this conflicts with core directive X"
```

---

## Standing Directives

Standing directives are permanent behavioral requirements that persist across sessions. Unlike regular directives (which are delivered via Redis and acknowledged once), standing directives are loaded into every session automatically.

### How They Work

1. The Audit Director evaluates a verified directive for promotion
2. If the directive targets a **pattern** (not a specific fix), the Director publishes exact add/remove verbiage to `promotions:{project}`
3. On the next session start, the `directive_intake` hook:
   - Reads the promotion instruction
   - Applies the exact verbiage to `observability/standing_directives.md`
   - Sends an acknowledgment (with SHA-256 hash) to `promotion_ack:{project}`
   - Loads the file contents into the agent's context
4. The Director verifies the acknowledgment matches intent
5. If unsatisfactory, it escalates to the user as a critical `PROMOTION_FAILURE`

### The Standing File

`observability/standing_directives.md` is a lean instruction sheet — verbiage only, no metadata. It is:
- Written by the `directive_intake` hook (never by the Director directly)
- Loaded into every session at every check interval (not just session start)
- Framed as "permanently in effect" in the agent's context
- Pruned by the Director when directives are superseded

All decision context (deliberation, reasoning, alternatives) lives in the audit platform's SQLite — not in this file.

---

## Managing Projects (audit platform)

Projects are registered in `config/projects.json`:

```json
{
  "projects": [
    {"name": "my-project", "active": true, "root": "/path/to/project"},
    {"name": "old-project", "active": false, "root": "/path/to/old"}
  ]
}
```

- `active: true` — orchestrator audits this project
- `active: false` — skipped; historical data remains

---

## Claudit Dashboard

Start the dashboard to visualize audit data:

```bash
python dashboard/start.py
```

Opens at `http://localhost:5173` with views for:
- Overview (severity/auditor/confidence charts, date filtering)
- Findings (filterable by severity, auditor, type)
- Directives (lifecycle tracking, compliance timeline, promotion decisions)
- Escalations (full context with pros/cons, conversational resolution for promotion failures)
- Evals (test pass rates, lint scores)
- Reports (Director's narrative audit reports in Source Serif 4)
- Sessions (tool call timeline drilldown)
- Data Quality (missing field analysis)
- System Health (QDrant/Redis status, audit task pipeline per auditor)

---

## Troubleshooting

**Hooks aren't firing**: Verify `.claude/settings.json` paths. Ensure `observability/` is at the project root. Check dependencies: `pip list | grep pydantic`.

**No data in QDrant**: Verify `QDRANT_URL` is set in `.env`. Check QDrant is running: `curl http://localhost:6333/collections`.

**Directives not appearing**: Verify `REDIS_URL`, `REDIS_USERNAME`, `REDIS_PASSWORD` in `.env`. Check the `PreToolUse` hook is configured.

**Evals not appearing**: Evals are produced when agents edit `.py` files. If no Python files have been edited, the `evals` collection will be empty.

**`OBSERVABILITY_PROJECT` not set**: Required. Without it, data records under an empty project name.

**Standing directives not loading**: Verify `observability/standing_directives.md` exists (can be empty). Check that `promotions:{project}` stream is accessible (ACL must include `~promotions:{project} ~promotion_ack:{project}`).

---

## Updating

Replace `observability/` in the external project with the updated `client/observability/`. Configuration files (`.env`, `.claude/settings.json`, CLAUDE.md verbiage) should not need to change unless noted in the release.
