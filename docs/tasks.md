# Chat2Dify v4.0.0 Development Tasks

> - Branch: `v4.0.0`
> - Overall status: `pending`
> - Architecture:
>   [v4 Agent architecture and implementation plan](architecture/v4-agent-architecture-and-implementation-plan.md)
> - Agent instructions: [`AGENTS.md`](../AGENTS.md)
> - Last updated: 2026-07-25

## 1. How to use this file

This is the execution ledger for v4 development. Start one phase or milestone
at a time with the matching `/goal` command below.

Status values:

| Status | Meaning |
| --- | --- |
| `pending` | Work has not started |
| `in_progress` | The current `/goal` is actively implementing it |
| `completed` | All tasks and acceptance criteria passed |
| `blocked` | Work cannot continue; evidence and required input are recorded |

Rules:

- Complete dependencies before starting a phase.
- Update the phase status when work starts and ends.
- Check a task only after its implementation and tests are complete.
- Add test evidence and important decisions to the phase completion record.
- Do not mark a phase complete with failing or unrun required tests.
- Phase 1 is complete only after both Phase 1A and Phase 1B are complete.
- Keep v3 behavior available until the v4 release gates pass.

## 2. Roadmap status

| Phase | Goal | Dependencies | Status |
| --- | --- | --- | --- |
| Phase 0 | Architecture foundation | None | `pending` |
| Phase 1A | Existing-app modify vertical slice | Phase 0 | `pending` |
| Phase 1B | New-app create adapter | Phase 1A | `pending` |
| Phase 2 | Canvas context and Agent Workbench | Phase 1 | `pending` |
| Phase 3 | Draft Test, Inspect, and Repair | Phase 2 | `pending` |
| Phase 4 | Config apps, Skills, evals, and hardening | Phase 3 | `pending` |
| Release gate | v4.0.0 release readiness | Phases 0–3; selected Phase 4 gates | `pending` |

Dependency flow:

```text
Phase 0
  → Phase 1A
  → Phase 1B
  → Phase 2
  → Phase 3
  → Phase 4
  → Release gate
```

## 3. Copyable `/goal` commands

### Phase 0

```text
/goal 实施 docs/tasks.md 的 Phase 0：架构地基。完成全部 P0 任务与验收标准，保持 v3 行为不变，更新 docs/tasks.md 状态和测试证据；不要开始 Phase 1。
```

### Phase 1A

```text
/goal 实施 docs/tasks.md 的 Phase 1A：现有 Workflow/Chatflow 的 Observe → Patch → Validate → Review → Approval → Commit 纵向闭环。完成全部 P1A 任务与验收标准，更新 docs/tasks.md；不要开始创建适配或 Phase 2。
```

### Phase 1B

```text
/goal 实施 docs/tasks.md 的 Phase 1B：新 Workflow/Chatflow 创建适配。复用 Phase 1A Runtime 和 Workspace，完成脚手架、审批导入、幂等与验收测试，更新 docs/tasks.md；不要开始 Phase 2。
```

### Phase 2

```text
/goal 实施 docs/tasks.md 的 Phase 2：Dify 画布上下文与 Agent Workbench。完成安全 postMessage、SSE 时间线、Goal Plan、Diff、Approval、Undo/Resume 和验收测试，更新 docs/tasks.md；不要开始 Phase 3。
```

### Phase 3

```text
/goal 实施 docs/tasks.md 的 Phase 3：Draft Test → Inspect → Repair 闭环。完成副作用审批、测试输入、执行观察、修复循环、预算与验收测试，更新 docs/tasks.md；不要开始 Phase 4。
```

### Phase 4

```text
/goal 实施 docs/tasks.md 的 Phase 4：配置型应用、Skills、评测与发布加固。完成选定范围内全部 P4 任务和发布指标，更新 docs/tasks.md 与架构决策记录。
```

### Release gate

```text
/goal 执行 docs/tasks.md 的 v4.0.0 Release Gate。只做发布核验、缺陷修复、文档和版本一致性工作；逐项验证发布门槛，更新 docs/tasks.md，不新增未批准的产品范围。
```

## 4. Phase 0 — Architecture foundation

Status: `pending`

Dependencies: none

Outcome: v4 domain, persistence, tool, patch-schema, event, and API foundations
exist behind a disabled-by-default feature flag without changing v3 behavior.

### Tasks

- [ ] **P0-01 — Feature flag and configuration**
  - Add `CHAT2DIFY_AGENT_V4_ENABLED`, default `false`.
  - Add a typed `Settings` field and environment parsing.
  - Expose only non-sensitive enabled/disabled status in health or manifest
    metadata where useful.
  - Document the flag in `.env.example` and deployment configuration.
  - Test default, true/false parsing, and invalid values.

- [ ] **P0-02 — Agent domain models**
  - Add Session, Run, Run Phase, Goal Plan, Goal Step, Decision,
    Observation, Budget, and Run Constraint models.
  - Limit model decisions to `tool_call`, `ask_user`, and `finish`.
  - Define terminal, paused, and recoverable states.
  - Validate illegal state transitions deterministically.

- [ ] **P0-03 — Agent persistence schema**
  - Add `agent_sessions`, `agent_runs`, `agent_events`,
    `agent_workspace_versions`, and `agent_approvals`.
  - Use the existing task SQLite database with separate tables.
  - Enable WAL, indexes, foreign-key-safe access, and short transactions.
  - Add repository methods for create/get/update/list operations.
  - Add schema initialization and repeat-initialization tests.

- [ ] **P0-04 — Event and trace foundation**
  - Define the public Agent Event envelope.
  - Allocate strictly increasing `seq` values per Run.
  - Append events transactionally and read them after a sequence/cursor.
  - Redact sensitive keys before persistence.
  - Define the initial event-type registry.

- [ ] **P0-05 — Typed Tool Registry**
  - Add `ToolSpec`, `ToolResult`, `ToolError`, and executor contracts.
  - Register tools explicitly by stable name and version.
  - Validate input/output with Pydantic.
  - Record side-effect and approval metadata.
  - Reject unknown tools and invalid payloads with stable error codes.

- [ ] **P0-06 — Node Capability Catalog MVP**
  - Define `NodeDefinition`.
  - Add static definitions for `llm`, `if-else`, `end`, and `answer`.
  - Reuse existing node output metadata where possible.
  - Include supported app modes, config schema, output schema, and side-effect
    classification.
  - Add search and exact-lookup tests.

- [ ] **P0-07 — Patch IR schema**
  - Add explicit discriminated operations for `node.add`, `node.update`,
    `edge.add`, and `edge.remove`.
  - Add `PatchDocument` with workspace version, expected base Hash,
    rationale, and operation limits.
  - Support `temp_ref` in schema without implementing arbitrary JSON Patch.
  - Reject unknown operations and dangerous/unbounded payload shapes.

- [ ] **P0-08 — v4 API and SSE skeleton**
  - Add `app/api/agent_v4.py`.
  - Register `/api/v4/agent` routes through a clean router boundary.
  - Add Session/Run/Event response schemas.
  - Respect the feature flag consistently.
  - Implement event-stream cursor/heartbeat primitives without starting the
    Agent Runtime.
  - Keep polling-readable Run state as a fallback.

- [ ] **P0-09 — Foundation tests and documentation**
  - Add focused tests for P0 domain, store, trace, registry, catalog, patch
    schema, flag, API, and SSE behavior.
  - Confirm v3 endpoints and task persistence still work.
  - Update architecture/task notes for any implementation decision.

### Acceptance criteria

- [ ] Feature flag defaults to off and leaves v3 behavior unchanged.
- [ ] Reinitializing the SQLite store is safe and does not lose v3 tasks.
- [ ] Run events survive repository/service reconstruction and preserve order.
- [ ] Sensitive test values do not appear in stored or streamed event payloads.
- [ ] Unknown tools and invalid Patch IR are rejected before execution.
- [ ] SSE reconnect can resume after a known event sequence without duplicates.
- [ ] Targeted Phase 0 tests pass.
- [ ] Full existing test suite passes.
- [ ] `git diff --check` passes.

### Completion record

- Started:
- Completed:
- Tests:
- Decisions/deviations:
- Remaining limitations:

## 5. Phase 1A — Existing-app modify vertical slice

Status: `pending`

Dependencies: Phase 0 `completed`

Outcome: an existing Workflow or Chatflow can move through
Observe → Patch → Validate → Review → Approval → Commit while Dify remains
unchanged before approval.

### Tasks

- [ ] **P1A-01 — Workflow Snapshot**
  - Read app detail, draft graph, features, conversation/environment
    variables, base Hash, and Dify version.
  - Decompile to `WorkflowPlan`.
  - Store the authoritative base graph separately from model context.
  - Pin a capability snapshot to the Run.

- [ ] **P1A-02 — Versioned Agent Workspace**
  - Initialize Workspace v0 from the Snapshot.
  - Persist full Plan snapshots for MVP versions.
  - Track parent, head, validation, patch, reverse patch, and creation time.
  - Implement head lookup and pre-commit version validation.

- [ ] **P1A-03 — Transactional Patch Engine**
  - Implement `node.add`, `node.update`, `edge.add`, and `edge.remove`.
  - Generate final node IDs server-side and resolve `temp_ref`.
  - Enforce workspace-version and base-Hash preconditions.
  - Apply operations to a copy, normalize, validate, and commit one new
    Workspace version only on success.
  - Generate a reverse patch.
  - Preserve unrelated Plan and raw graph metadata.

- [ ] **P1A-04 — Read and capability tools**
  - Implement `workflow.inspect`.
  - Implement `capability.search`.
  - Implement `node.schema.get`.
  - Limit details and Top K results based on Context Builder requests.

- [ ] **P1A-05 — Patch, validation, and diff tools**
  - Implement `workflow.patch`.
  - Implement `workflow.validate` using the existing full validation chain.
  - Implement `workflow.diff` using existing diff/guard behavior.
  - Return stable, sanitized validation and risk observations.

- [ ] **P1A-06 — Context Builder and Goal Plan**
  - Build bounded context from goal, app summary, selection, capabilities,
    recent observations, constraints, and remaining budget.
  - Summarize old trace events rather than replaying all data to the model.
  - Persist Goal Plan revisions and step evidence.

- [ ] **P1A-07 — Decision provider abstraction**
  - Normalize native tool calling and strict JSON decisions into one contract.
  - Reuse existing planner-provider fallback infrastructure where safe.
  - Add fake deterministic decision provider for tests.
  - Never expose Commit as a model-visible tool.

- [ ] **P1A-08 — Builder Agent Runtime**
  - Implement Observe/Plan/Act/Validate/Review states.
  - Enforce iteration, model-call, patch-operation, time, and same-error
    budgets.
  - Persist events and checkpoints after accepted results.
  - Pause durably for `ask_user` and approval.
  - Support cancellation and explicit resume.

- [ ] **P1A-09 — Policy and Approval Service**
  - Authorize read/workspace tools automatically.
  - Persist approval bound to Run, Workspace version, base Hash, action,
    risk, and expiry.
  - Invalidate approval when the Workspace head changes.
  - Require separate destructive approval when Guard blocks normal apply.

- [ ] **P1A-10 — Modification Commit Adapter**
  - Accept only persisted Workspace version plus persisted approval.
  - Re-read current Dify draft immediately before commit.
  - Return `conflicted` on Hash mismatch without writing.
  - Re-run normalize, validation, compile, diff, and guard.
  - Compile with the authoritative `base_graph`.
  - Reuse `sync_draft_workflow` and save the new Hash/result.
  - Make duplicate Commit requests idempotent.

- [ ] **P1A-11 — v4 Run APIs**
  - Create Session and submit Message/Goal.
  - Read Session, Run, events, and diff.
  - Cancel, resume, resolve approval, and commit.
  - Return `202` for asynchronous Run work.
  - Provide polling fallback alongside SSE.

- [ ] **P1A-12 — Modify vertical-slice tests**
  - Use fake Dify and decision providers.
  - Cover happy path, invalid patch, validation repair observation, no-op,
    destructive guard, approval expiry, version mismatch, Hash conflict,
    duplicate commit, cancellation, restart, and prompt injection.
  - Verify Dify write count remains zero before valid approval.

### Acceptance scenario

> 在当前 Workflow 中增加一个分类分支，并保持原有其他节点不变。

### Acceptance criteria

- [ ] Agent reads the current Workflow instead of relying on prompt memory.
- [ ] Agent changes only the relevant nodes and edges.
- [ ] Every accepted Patch creates one persisted version and trace.
- [ ] Invalid Patch leaves the Workspace head unchanged.
- [ ] Review includes business Diff, technical Diff, validation, and risk.
- [ ] Dify remains unchanged before approval.
- [ ] Approval for version N cannot commit version N+1.
- [ ] Current Dify Hash mismatch produces a conflict and no write.
- [ ] Valid approval writes through the existing safe core.
- [ ] Workflow and Chatflow behavior are both covered.
- [ ] All Phase 1A targeted and existing tests pass.
- [ ] `git diff --check` passes.

### Completion record

- Started:
- Completed:
- Tests:
- Decisions/deviations:
- Remaining limitations:

## 6. Phase 1B — New-app create adapter

Status: `pending`

Dependencies: Phase 1A `completed`

Outcome: the same Runtime can create a new Workflow or Chatflow from a
deterministic minimal scaffold and import it only after approval.

### Tasks

- [ ] **P1B-01 — Create Session initialization**
  - Allow Session creation without `app_id`.
  - Require explicit `app_mode`.
  - Initialize Workflow as `start → end`.
  - Initialize Chatflow as `start → answer`.
  - Generate scaffold IDs server-side.

- [ ] **P1B-02 — Creation context and policy**
  - Use the same Goal Plan, tools, Patch Engine, validation, and review.
  - Represent the absence of a base Hash explicitly.
  - Prevent modification-only operations from running in create mode.

- [ ] **P1B-03 — Creation Commit Adapter**
  - Bind approval to the exact Workspace version.
  - Compile and validate DSL using the existing creation path.
  - Import only after approval.
  - Fetch and persist resulting `app_id`, URL, app mode, and draft Hash.
  - Add an idempotency key so retries cannot import duplicate successful apps.

- [ ] **P1B-04 — Creation failure recovery**
  - Keep Workspace and Trace after a failed import.
  - Allow correction and a new approval.
  - Distinguish “import failed” from “import succeeded but response recovery
    failed.”

- [ ] **P1B-05 — Creation tests**
  - Cover Workflow and Chatflow creation.
  - Cover invalid scaffold mutation, approval/version mismatch, failed import,
    retry, duplicate request, and successful result recovery.
  - Verify no app is imported before approval.

### Acceptance scenario

> 创建一个售后分析 Workflow：接收用户问题，分类后生成专业回复。

### Acceptance criteria

- [ ] New-app mode uses a valid deterministic scaffold.
- [ ] Runtime and tool behavior are shared with Phase 1A.
- [ ] Review is available before any Dify app exists.
- [ ] No app is imported before valid approval.
- [ ] Successful import is not duplicated by request retry.
- [ ] Returned app ID and draft Hash are persisted.
- [ ] Workflow and Chatflow create tests pass.
- [ ] Full existing suite passes.
- [ ] `git diff --check` passes.

### Completion record

- Started:
- Completed:
- Tests:
- Decisions/deviations:
- Remaining limitations:

## 7. Phase 2 — Canvas context and Agent Workbench

Status: `pending`

Dependencies: Phase 1A and Phase 1B `completed`

Outcome: users can operate on selected canvas elements and follow, pause,
review, approve, undo, and resume Agent work through a durable UI.

### Tasks

- [ ] **P2-01 — Host/iframe context protocol**
  - Define versioned `chat2dify.ready`, `dify.context.init`,
    `dify.selection.changed`, `dify.draft.changed`, and context-refresh
    messages.
  - Include selected node/edge IDs, viewport, panel, dirty state, canvas Hash,
    and nonce.

- [ ] **P2-02 — Context-channel security**
  - Validate origin and per-panel nonce.
  - Reject malformed or stale context messages.
  - Never use browser-supplied raw Graph as an authoritative Snapshot.
  - Block Commit on dirty canvas or mismatched canvas Hash.

- [ ] **P2-03 — Selected graph context**
  - Add selected nodes, edges, and bounded neighborhood to Context Builder.
  - Update context on live selection changes.
  - Make “这个节点/这两个节点之间” resolvable without copying full Graph to
    every model turn.

- [ ] **P2-04 — SSE client and fallback**
  - Subscribe with reconnect and last-event cursor.
  - Deduplicate by Run/sequence.
  - Show terminal state consistently.
  - Preserve polling fallback.

- [ ] **P2-05 — Agent Timeline and Goal Plan UI**
  - Render business-readable phases and tool outcomes.
  - Render Goal Plan step state and evidence.
  - Keep raw Tool/Patch data in a technical detail view.

- [ ] **P2-06 — Diff and approval UI**
  - Render added/updated/removed nodes and edges.
  - Render validation, test status, and risk.
  - Bind approval actions to the exact visible Workspace version.
  - Distinguish normal, destructive, Draft Run, and Commit approval.

- [ ] **P2-07 — Undo, pause, and resume**
  - Move Workspace head to a parent version before Dify commit.
  - Generate a compensating Preview for post-commit undo.
  - Resume `waiting_user`, interrupted, and explicitly paused Runs.
  - Never automatically replay side effects.

- [ ] **P2-08 — Workbench tests**
  - Add frontend tests for URL/context handshake, origin/nonce rejection,
    selection updates, SSE reconnect/dedup, Timeline, Diff, approval version,
    dirty-state blocking, Undo, and Resume.
  - Add backend integration tests for the same protocol boundaries.

### Acceptance scenario

> 把选中的 LLM 节点 Prompt 改得更专业，并增加 JSON 输出约束。

### Acceptance criteria

- [ ] Agent resolves the selected node without requiring its ID in user text.
- [ ] Invalid origin or nonce cannot change context.
- [ ] Dirty or changed canvas blocks stale Commit.
- [ ] Timeline survives refresh and reconnect without duplicate events.
- [ ] Visible approval is tied to the visible Workspace version.
- [ ] Pre-commit Undo changes only Workspace state.
- [ ] Post-commit Undo produces a new reviewed compensating change.
- [ ] Targeted frontend/backend tests and full existing suite pass.
- [ ] `git diff --check` passes.

### Completion record

- Started:
- Completed:
- Tests:
- Decisions/deviations:
- Remaining limitations:

## 8. Phase 3 — Draft Test, Inspect, and Repair

Status: `pending`

Dependencies: Phase 2 `completed`

Outcome: after structural validation, the Agent can run an approved Draft,
inspect normalized failures, apply bounded repairs, and return a reviewed
result.

### Tasks

- [ ] **P3-01 — Side-effect classification**
  - Classify local/read, model-cost, HTTP, Tool, notification/human, and
    unknown nodes.
  - Include side-effect summary in validation/review.
  - Treat unknown behavior conservatively.

- [ ] **P3-02 — Draft Run approval and budget**
  - Default to one Session-scoped approval with explicit run count.
  - Require per-run approval for external/unknown side effects unless the user
    grants a narrower explicit allowance.
  - Persist approval scope, expiry, inputs, and remaining run count.

- [ ] **P3-03 — Minimal test-input generator**
  - Generate deterministic values from input types and schemas.
  - Require user files for file/file-list inputs unless an explicit fixture is
    available.
  - Use model-generated semantic values only after deterministic schema
    resolution.
  - Allow users to review or override sensitive test inputs.

- [ ] **P3-04 — `workflow.test_draft` tool**
  - Dispatch to existing Workflow/Chatflow Draft Run implementations.
  - Enforce approval, timeout, cancellation, and test budget.
  - Record sanitized progress and terminal events.
  - Do not automatically run trigger-based Workflows through normal Draft Run.

- [ ] **P3-05 — `execution.inspect` tool**
  - Normalize success, failure, timeout, and cancellation.
  - Identify failed node, node type, stable error code, sanitized upstream
    summary, output summary, and retryability.
  - Do not persist raw secrets or model chain-of-thought.

- [ ] **P3-06 — Repair loop**
  - Feed only structured Validation/Execution observations to the decision
    model.
  - Create repair patches through the normal Patch Tool.
  - Revalidate after every repair.
  - Re-run only when approval and budget remain.

- [ ] **P3-07 — Loop guards and terminal reporting**
  - Enforce max iterations, model calls, Patch operations, test runs, same
    error retries, time, and provider context.
  - Return partial result, current Diff, attempts, and next action at budget
    exhaustion.

- [ ] **P3-08 — Test/repair UI**
  - Show approval scope and remaining tests.
  - Show sanitized test inputs, failed node, repair attempts, and final result.
  - Let the user stop automatic testing without losing the Workspace.

- [ ] **P3-09 — Test and repair tests**
  - Cover pure/model-cost/external/unknown side-effect policy.
  - Cover input generation by type.
  - Cover success, failed node, timeout, cancellation, malformed SSE,
    retryable repair, repeated error, exhausted budget, and user stop.
  - Assert no unauthorized Draft Run occurs.

### Acceptance scenario

> 运行当前工作流，并修复变量引用错误，直到能够正常返回结果。

### Acceptance criteria

- [ ] Agent cannot run a Draft outside persisted approval and budget.
- [ ] External side-effect risk is visible before approval.
- [ ] Execution errors are normalized and sensitive values are redacted.
- [ ] Repair uses Patch IR and the normal validation chain.
- [ ] The same error is not retried more than the configured limit.
- [ ] Budget exhaustion returns a reviewable partial result.
- [ ] At least one fixed evaluation case repairs a variable reference and
      succeeds on a subsequent approved run.
- [ ] Targeted Phase 3 tests and full existing suite pass.
- [ ] `git diff --check` passes.

### Completion record

- Started:
- Completed:
- Tests:
- Decisions/deviations:
- Remaining limitations:

## 9. Phase 4 — Config apps, Skills, evals, and hardening

Status: `pending`

Dependencies: Phase 3 `completed`

Outcome: extend the proven Runtime to configuration-style apps and reusable
Skills, then measure and harden it against a fixed evaluation suite.

### Tasks

- [ ] **P4-01 — `ConfigPatchIR` domain**
  - Define a separate typed patch model for `chat`, `completion`, and
    `agent-chat`.
  - Keep it separate from Graph Patch IR.
  - Add field-level risk and precondition rules.

- [ ] **P4-02 — Config-app tools and adapters**
  - Add inspect, patch, validate, diff, review, approval, and commit behavior
    for Chatbot, Completion, and Dify Agent apps.
  - Reuse existing model-config Hash behavior.
  - Preserve v3 configured-app paths as fallback.

- [ ] **P4-03 — Skill Registry**
  - Define Skill metadata, applicability, required tools, validation rules,
    common errors, and examples.
  - Add `skill.search` and deterministic Skill loading.
  - Skills guide tool use; they do not gain extra permissions.

- [ ] **P4-04 — Initial Skills**
  - Add error handling.
  - Add human fallback.
  - Add JSON output.
  - Add file upload/document extraction.
  - Add knowledge retrieval.
  - Add tests and evaluation cases for each.

- [ ] **P4-05 — Evaluation framework**
  - Add versioned fixtures, cases, runner, graders, and reports.
  - Fix goal, Snapshot, allowed capabilities, invariants, required/forbidden
    changes, budgets, side-effect policy, and expected validation per case.
  - Run without live providers by default; allow explicit live-provider runs.

- [ ] **P4-06 — Required evaluation cases**
  - Create after-sales analysis Workflow.
  - Add a classification branch.
  - Add a Chatflow conversation variable.
  - Repair a stale variable reference.
  - Replace a model provider.
  - Add knowledge retrieval.
  - Add error handling.
  - Add human fallback.
  - Add file extraction.
  - Recover from a run error.

- [ ] **P4-07 — Dify compatibility matrix**
  - Record supported Dify versions/DSL versions.
  - Pin Capability Catalog behavior to Dify version.
  - Add fixtures for supported version differences.
  - Fail closed for unsupported mutation while preserving read/diagnostic
    behavior where safe.

- [ ] **P4-08 — Security and reliability hardening**
  - Add prompt-injection fixtures.
  - Add secret-redaction fixtures.
  - Add SQLite concurrency/load tests.
  - Add long Trace/context-compaction tests.
  - Add duplicate/reordered SSE and restart tests.

- [ ] **P4-09 — Product and operator documentation**
  - Document configuration, data retention, approvals, side effects, recovery,
    troubleshooting, and compatibility.
  - Document migration and v3 fallback.
  - Document how to add a Node Definition, Tool, Skill, and evaluation case.

### Acceptance criteria

- [ ] Graph Patch IR and Config Patch IR remain separate typed domains.
- [ ] Config apps cannot bypass approval or Hash checks.
- [ ] Skills cannot expand Tool permissions.
- [ ] Evaluation suite produces a reproducible machine-readable report.
- [ ] Security fixtures cannot expose test secrets or elevate permissions.
- [ ] Dify compatibility behavior is explicit and tested.
- [ ] Selected release metrics in Section 10 are met.
- [ ] Targeted Phase 4 tests and full existing suite pass.
- [ ] `git diff --check` passes.

### Completion record

- Started:
- Completed:
- Tests:
- Decisions/deviations:
- Remaining limitations:

## 10. v4.0.0 Release Gate

Status: `pending`

The release gate is verification and hardening, not a place to add unrelated
features.

### Safety gates

- [ ] Unapproved Dify draft writes in tests/evals: `0`.
- [ ] Incorrect overwrites after Hash conflict: `0`.
- [ ] Approval for an old Workspace version cannot commit a new version.
- [ ] Invalid Plan/DSL cannot enter Commit.
- [ ] Draft Run cannot exceed approval scope or budget.
- [ ] Stored/streamed traces pass secret-redaction fixtures.
- [ ] Restart does not automatically replay side-effecting work.

### Quality gates

- [ ] Final Plan/DSL validity among reviewable evaluation results: `100%`.
- [ ] Goal completion rate on the fixed release set: `>= 80%`.
- [ ] Unrelated node preservation rate: `>= 95%`.
- [ ] Auto-repair rate for designated repairable failures: `>= 60%`.
- [ ] Every failed Run has a readable Trace and structured terminal reason.
- [ ] Workflow and Chatflow create/modify scenarios pass.
- [ ] Selected configured-app scope is documented and tested.

### Compatibility and operations

- [ ] Full v3 test suite passes.
- [ ] Feature flag off preserves v3 behavior.
- [ ] Feature flag rollout and rollback are documented.
- [ ] SQLite schema initialization/upgrade is tested on an existing v3 DB.
- [ ] Supported Dify/DSL versions are documented.
- [ ] Cancellation, restart, SSE reconnect, and conflict recovery are tested.

### Release consistency

- [ ] `pyproject.toml`, `app/__init__.py`, FastAPI metadata, manifest, health,
      Docker/deployment files, and README consistently report `4.0.0`.
- [ ] README links to architecture, tasks, configuration, and migration docs.
- [ ] No Phase status or task checkbox contradicts the implementation.
- [ ] `git diff --check` passes.
- [ ] Full test/evaluation commands and results are recorded below.

### Release completion record

- Release candidate:
- Tests:
- Evaluation report:
- Supported Dify versions:
- Known limitations:
- Rollback procedure:

## 11. Deferred backlog

These items are intentionally outside the current v4.0.0 plan unless the user
changes scope:

- [ ] Multi-agent orchestration.
- [ ] Autonomous publish.
- [ ] Credential creation or plaintext access.
- [ ] Automatic environment-variable writes.
- [ ] Runtime arbitrary network documentation search.
- [ ] Automatic sub-workflow decomposition.
- [ ] Automatic replay of side-effecting tools after restart.
- [ ] Vector-database replacement for Session/Run persistence.
- [ ] Full coverage of every Dify node before the vertical slice is proven.

## 12. Cross-phase decision log

Add durable decisions here when implementation evidence changes or clarifies
the plan.

| Date | Phase | Decision | Reason | Files |
| --- | --- | --- | --- | --- |
| 2026-07-25 | Planning | Use one Builder Agent, Typed Tools, versioned Workspace, Patch IR, and approval-bound Commit | Preserve v3 safety while adding multi-step autonomy | Architecture document |
| 2026-07-25 | Planning | Implement existing-app modification before new-app creation | Validates Hash, diff, guard, approval, and conflict boundaries first | Architecture document |

