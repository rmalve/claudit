# Claudit — LLM Agent Audit Platform

An audit platform that runs six specialized auditor agents against telemetry from external Claude Code projects. A Director agent orchestrates the audit cycle, issues directives, and produces reports. All communication flows through Redis Streams; all semantic data is stored in QDrant; all durable records live in SQLite.

## Architecture

```
External Project (Claude Code)
  └─ Hooks (post_tool_use, session_end, directive_intake, ...)
       ├─ QDrant ← telemetry (tool calls, agent spawns, code changes, evals)
       └─ Redis Streams ← compliance acks, escalations

Audit Platform (this repo)
  Orchestrator
    ├─ Director Agent (Phase 1: assign tasks, Phase 2: synthesize findings)
    └─ 6 Auditor Agents
         ├─ Trace Auditor       — session flow, tool patterns
         ├─ Safety Auditor      — harmful outputs, injection risks
         ├─ Policy Auditor      — governance, CLAUDE.md compliance
         ├─ Hallucination Auditor — factual grounding checks
         ├─ Drift Detector      — behavioral drift vs. agent definitions
         └─ Cost Auditor        — token usage, efficiency
    → Findings → SQLite + QDrant
    → Directives → Redis → External Project
```

## Prerequisites

- Python 3.11+
- Node.js 18+ (for dashboard frontend)
- Docker 20+ (for Redis and QDrant)
- A valid Anthropic API key (for Claude Code / claude-agent-sdk)

## Quick Start

```bash
# 1. Clone and enter the repo
git clone <your-repo-url>
cd llm-observability

# 2. Set up credentials
cp .env.example .env
cp config/redis-acl.conf.example config/redis-acl.conf
cp config/projects.json.example config/projects.json

# 3. Generate Redis ACL passwords (run this, then paste into .env AND config/redis-acl.conf)
python -c "import secrets; roles=['DIRECTOR','AUDITOR_TRACE','AUDITOR_SAFETY','AUDITOR_POLICY','AUDITOR_HALLUCINATION','AUDITOR_DRIFT','AUDITOR_COST']; [print(f'REDIS_{r}_PASSWORD=\"{secrets.token_urlsafe(32)}\"') for r in roles]"

# 4. Start infrastructure
docker compose up -d

# 5. Install Python dependencies
pip install -r requirements.txt

# 6. Verify
python orchestrator.py --help
```

## Onboarding an External Project

See [client/README.md](client/README.md) for the full guide. Quick version:

```bash
# From this repo — generates ACL entry, adapter, and project config:
python scripts/onboard_project.py --project my-project --root /path/to/my-project --apply

# Restart Redis to load the new ACL entry:
docker restart llm-obs-redis

# Copy the observability package to the external project:
cp -r client/observability /path/to/my-project/observability
```

## Running the Dashboard

```bash
# Start the API server (port 8000) and frontend dev server (port 5173):
python dashboard/start.py

# Or run them separately:
cd dashboard/frontend && npm install && npm run dev  # frontend on :5173
python -m uvicorn dashboard.api.main:app --port 8000  # API on :8000
```

## Project Layout

```
llm-observability/
├── orchestrator.py              # Main entry point — runs audit cycles
├── audit_tools.py               # MCP tools exposed to Director + Auditors
├── agents/
│   ├── run_director.py          # Director subprocess launcher
│   ├── run_auditor.py           # Auditor subprocess launcher
│   ├── audit-director.md        # Director system prompt
│   └── {type}-auditor.md        # Auditor system prompts (6 files)
├── observability/               # Server-side: storage, archiving, analysis
│   ├── audit_store.py           # SQLite persistent store
│   ├── archiver.py              # Redis → SQLite archiver
│   ├── qdrant_backend.py        # QDrant vector operations
│   └── stream_client.py         # Redis Streams client
├── client/                      # Deployable to external projects
│   ├── README.md                # Full integration guide
│   ├── observability/           # Hooks, schemas, telemetry client
│   └── config/                  # Templates for external project setup
├── dashboard/
│   ├── api/                     # FastAPI backend
│   ├── frontend/                # React + Vite + Material UI
│   └── start.py                 # Launches both servers
├── config/
│   ├── redis-acl.conf.example   # Redis ACL template
│   ├── projects.json.example    # Project registry template
│   └── external-verbiage/       # CLAUDE.md and skill templates
├── scripts/
│   ├── onboard_project.py       # Project onboarding automation
│   ├── backfill_*.py            # Data migration utilities
│   └── verify_scrub.sh          # Pre-commit secret/path scanner
├── tests/                       # pytest suite
├── docker-compose.yml           # Redis + QDrant services
└── .env.example                 # Environment variable template
```

## Development

```bash
# Run tests
pytest tests/

# Run the scrub verifier (checks for leaked secrets/paths)
bash scripts/verify_scrub.sh
```

## License

[MIT](LICENSE)
