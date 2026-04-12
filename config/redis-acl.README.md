# Redis ACL Configuration

Reference documentation for `redis-acl.conf`. Redis ACL files cannot contain comments — this file preserves the context.

## Key Permissions

| Permission | Purpose |
|---|---|
| `~audit:*` | Key pattern access for audit streams |
| `~directives:*` | Key pattern access for per-project directive queues |
| `~compliance:*` | Key pattern access for per-project compliance queues |
| `~promotions:*` | Key pattern access for per-project promotion instruction queues |
| `~promotion_ack:*` | Key pattern access for per-project promotion acknowledgment queues |
| `~escalation_resolutions:*` | Key pattern access for per-project escalation resolution guidance |
| `+XADD` | Write to stream |
| `+XREADGROUP` | Read from consumer group |
| `+XACK` | Acknowledge message |
| `+XGROUP` | Manage consumer groups |
| `+XINFO` | Stream info (monitoring) |
| `+XLEN` | Stream length |
| `+XTRIM` | Trim stream (retention) |
| `+XRANGE` / `+XREVRANGE` | Read stream ranges (Director only) |
| `+PING` | Health checks |
| `+AUTH` | Authentication |

## Users

### default
Disabled for security. No anonymous access.

### director
Full access to all audit streams (`audit:*`) and all per-project streams (`directives:*`, `compliance:*`, `promotions:*`, `promotion_ack:*`, `escalation_resolutions:*`). This is the Audit Director process.

### auditor-{type} (trace, safety, policy, hallucination, drift, cost)
Each auditor gets:
- **Write**: `audit:findings`, `audit:status`
- **Read**: `audit:tasks` (own consumer group only)

Auditors cannot read each other's findings, cannot issue directives, and cannot escalate directly.

### External Project Users
Each onboarded project gets a scoped user with:
- **Read**: `directives:{project}` (receive directives from Director)
- **Write**: `compliance:{project}` (send acknowledgments back)
- **Read**: `promotions:{project}` (receive standing directive promotion instructions)
- **Write**: `promotion_ack:{project}` (confirm promotion verbiage was applied)

Template for onboarding (add to `redis-acl.conf`):
```
user project-{name} on >{generated-password} ~directives:{name} ~compliance:{name} ~promotions:{name} ~promotion_ack:{name} +XREADGROUP +XACK +XADD +XINFO +XLEN +XGROUP +PING +AUTH
```

Use the onboarding script to generate these automatically:
```bash
python scripts/onboard_project.py --project {name} --root /path/to/project --apply
```
