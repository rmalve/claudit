# Contributing

Thank you for considering contributing to Claudit.

## Development Setup

1. Follow the [Quick Start](README.md#quick-start) to get the platform running locally.
2. Install dev dependencies: `pip install pytest`
3. Run the test suite: `pytest tests/`

## Secret Discipline

This project handles credentials (Redis ACL passwords, project tokens). Follow these rules strictly:

- **Never commit** `.env`, `config/redis-acl.conf`, or `config/projects.json`. They are in `.gitignore`.
- **Never hardcode** passwords, API keys, or absolute filesystem paths in source code.
- **Run `scripts/verify_scrub.sh`** before every commit. It checks for leaked secrets and personal paths.
- If you add a new secret or env var, update `.env.example` with a `CHANGE_ME_*` placeholder.

## Pull Request Guidelines

1. Create a feature branch from `main`.
2. Write tests for new functionality.
3. Ensure `pytest tests/` passes.
4. Run `bash scripts/verify_scrub.sh` — it must exit 0.
5. Keep PRs focused. One feature or fix per PR.

## Architecture Pointers

- Adding an auditor: create `agents/{type}-auditor.md` (system prompt) and register the type in `orchestrator.py`.
- Changing the storage schema: update `observability/audit_store.py` and add migration logic.
- Adding a project adapter: use `scripts/onboard_project.py` — it generates everything.
- Modifying hooks: update `client/observability/hooks/` and sync to external projects.

## Reporting Issues

- Bugs and feature requests: open a GitHub issue.
- Security vulnerabilities: see [SECURITY.md](SECURITY.md).
