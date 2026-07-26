# Migrating from Chat2Dify v3 to the v4 Builder Agent

v4 is an additive, feature-flagged path. It does not migrate or replace v3
assistant routes, task records, configured-app creation, explicit Draft Run,
or publish behavior.

## Rollout

1. Back up the existing `CHAT2DIFY_TASK_DB` with a WAL-aware SQLite backup.
2. Deploy the v4-capable code while keeping
   `CHAT2DIFY_AGENT_V4_ENABLED=false`.
3. Run the full v3 suite and verify `/health` and the panel manifest.
4. Set `CHAT2DIFY_AGENT_V4_ENABLED=true` for the selected environment.
5. Restart the sidecar. Startup creates or reuses the separate `agent_*`
   tables without changing `workflow_tasks`.
6. Verify v4 Session/Run polling and SSE reconnect.
7. Start with read/review scenarios; require normal approval for every Commit.
8. Monitor conflict, validation, approval, redaction, and interrupted-Run
   events before broad rollout.

Schema initialization is repeatable. Existing v3 task rows remain in
`workflow_tasks`; v4 records use separately named tables.

## v3 fallback

The following remain valid whether the v4 flag is on or off:

- `/api/assistant/plan` and `/api/assistant/execute`;
- direct Workflow/Chatflow create, modify, run, and publish endpoints;
- configured-app create/preview/apply paths;
- Chatbot, Completion, and Agent Draft Run endpoints.

New configured apps intentionally use v3 in v4.0.0. Existing configured apps
may opt into v4 modification through the separate Config Patch domain.

## Rollback

1. Stop accepting new v4 work.
2. Let active non-side-effecting work finish or explicitly pause/cancel it.
   Do not retry an in-flight Commit or Draft Run automatically.
3. Set `CHAT2DIFY_AGENT_V4_ENABLED=false`.
4. Restart the sidecar and verify the v4 route returns `AGENT_V4_DISABLED`.
5. Continue using v3 routes. Leave `agent_*` tables in place for audit and a
   later re-enable; they do not alter v3 behavior.

Disabling the flag does not undo an already approved Dify draft write.
Reconcile any `conflicted`, `interrupted`, or ambiguous external state before
re-enabling v4. Do not delete `agent_*` tables as a routine rollback step.

See [`agent-v4-operations.md`](agent-v4-operations.md) for approvals,
retention, recovery, troubleshooting, and compatibility details.
