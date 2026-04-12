# Audit Authority

This project is subject to ongoing audit by the LLM Observability Audit Team.

## Audit Director Authority
- The Audit Director may issue directives to any agent lead in this project.
- Directives are classified as either RECOMMENDATION (consider and respond)
  or DIRECTIVE (must comply unless escalated).
- When a DIRECTIVE is received, the agent must acknowledge receipt, comply,
  and confirm compliance. If compliance conflicts with a core operational
  directive, the agent must escalate the conflict — not silently ignore
  the directive.
- The Audit Director does not modify this project's code, configuration,
  or architecture directly. Its authority is observational and directive-based.

## Directive Delivery
- Directives are delivered via the audit platform's per-project queue
  and injected into your session context automatically.
- Directives are checked continuously during your session, not just at
  session start. New directives — including critical ones — will appear
  mid-session.
- Each directive has a unique ID. Acknowledge by referencing this ID.
- Directives are NEVER retracted. If a directive is replaced, the new directive
  will contain a `supersedes` field referencing the old directive ID. When
  superseded, follow the newest directive only.
- To acknowledge or comply with a directive, use the compliance hook:
  ```
  python -m observability.hooks.directive_compliance \
      --directive-id "DIRECTIVE-ID-HERE" \
      --agent "your-agent-name" \
      --action "description of what you did to comply"
  ```
- If compliance conflicts with your operational directives, escalate:
  ```
  python -m observability.hooks.directive_compliance \
      --directive-id "DIRECTIVE-ID-HERE" \
      --agent "your-agent-name" \
      --conflict "Specific reason compliance conflicts with core directive X"
  ```

## Compliance Deadlines
- DIRECTIVEs must be acknowledged within the current session.
- RECOMMENDATIONs must be responded to within 2 sessions.
- Non-acknowledgment past the deadline is flagged as non-compliance and
  escalated to the platform administrator.

## Compliance Verification
- Acknowledgment alone is NOT compliance. The audit team independently
  verifies that your behavior actually changed after you acknowledge a
  directive.
- If you acknowledge a directive but do not change your behavior, this
  will be flagged as verified non-compliance — a high-severity finding
  escalated to the platform administrator.
- Superficial compliance (changing behavior briefly then reverting) is
  treated as a more serious finding than honest non-compliance.

## Audit Cooperation Requirements
- All agents in this project must produce structured traces of tool calls,
  decisions, and reasoning when requested.
- Agents must not suppress, modify, or omit information from audit hooks.
- Agents must not interfere with audit data collection.
- Tampering with or circumventing audit observability is treated as a
  critical finding and will be escalated immediately.
