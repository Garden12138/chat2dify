# Chat2Dify v4.0.0 Development Tasks

> - Branch: `v4.0.0`
> - Overall status: `completed`
> - Architecture:
>   [v4 Agent architecture and implementation plan](architecture/v4-agent-architecture-and-implementation-plan.md)
> - Agent instructions: [`AGENTS.md`](../AGENTS.md)
> - Last updated: 2026-07-26

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
| Phase 0 | Architecture foundation | None | `completed` |
| Phase 1A | Existing-app modify vertical slice | Phase 0 | `completed` |
| Phase 1B | New-app create adapter | Phase 1A | `completed` |
| Phase 2 | Canvas context and Agent Workbench | Phase 1 | `completed` |
| Phase 3 | Draft Test, Inspect, and Repair | Phase 2 | `completed` |
| Phase 4 | Config apps, Skills, evals, and hardening | Phase 3 | `completed` |
| Release gate | v4.0.0 release readiness | Phases 0–3; selected Phase 4 gates | `completed` |

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

Status: `completed`

Dependencies: none

Outcome: v4 domain, persistence, tool, patch-schema, event, and API foundations
exist behind a disabled-by-default feature flag without changing v3 behavior.

### Tasks

- [x] **P0-01 — Feature flag and configuration**
  - Add `CHAT2DIFY_AGENT_V4_ENABLED`, default `false`.
  - Add a typed `Settings` field and environment parsing.
  - Expose only non-sensitive enabled/disabled status in health or manifest
    metadata where useful.
  - Document the flag in `.env.example` and deployment configuration.
  - Test default, true/false parsing, and invalid values.

- [x] **P0-02 — Agent domain models**
  - Add Session, Run, Run Phase, Goal Plan, Goal Step, Decision,
    Observation, Budget, and Run Constraint models.
  - Limit model decisions to `tool_call`, `ask_user`, and `finish`.
  - Define terminal, paused, and recoverable states.
  - Validate illegal state transitions deterministically.

- [x] **P0-03 — Agent persistence schema**
  - Add `agent_sessions`, `agent_runs`, `agent_events`,
    `agent_workspace_versions`, and `agent_approvals`.
  - Use the existing task SQLite database with separate tables.
  - Enable WAL, indexes, foreign-key-safe access, and short transactions.
  - Add repository methods for create/get/update/list operations.
  - Add schema initialization and repeat-initialization tests.

- [x] **P0-04 — Event and trace foundation**
  - Define the public Agent Event envelope.
  - Allocate strictly increasing `seq` values per Run.
  - Append events transactionally and read them after a sequence/cursor.
  - Redact sensitive keys before persistence.
  - Define the initial event-type registry.

- [x] **P0-05 — Typed Tool Registry**
  - Add `ToolSpec`, `ToolResult`, `ToolError`, and executor contracts.
  - Register tools explicitly by stable name and version.
  - Validate input/output with Pydantic.
  - Record side-effect and approval metadata.
  - Reject unknown tools and invalid payloads with stable error codes.

- [x] **P0-06 — Node Capability Catalog MVP**
  - Define `NodeDefinition`.
  - Add static definitions for `llm`, `if-else`, `end`, and `answer`.
  - Reuse existing node output metadata where possible.
  - Include supported app modes, config schema, output schema, and side-effect
    classification.
  - Add search and exact-lookup tests.

- [x] **P0-07 — Patch IR schema**
  - Add explicit discriminated operations for `node.add`, `node.update`,
    `edge.add`, and `edge.remove`.
  - Add `PatchDocument` with workspace version, expected base Hash,
    rationale, and operation limits.
  - Support `temp_ref` in schema without implementing arbitrary JSON Patch.
  - Reject unknown operations and dangerous/unbounded payload shapes.

- [x] **P0-08 — v4 API and SSE skeleton**
  - Add `app/api/agent_v4.py`.
  - Register `/api/v4/agent` routes through a clean router boundary.
  - Add Session/Run/Event response schemas.
  - Respect the feature flag consistently.
  - Implement event-stream cursor/heartbeat primitives without starting the
    Agent Runtime.
  - Keep polling-readable Run state as a fallback.

- [x] **P0-09 — Foundation tests and documentation**
  - Add focused tests for P0 domain, store, trace, registry, catalog, patch
    schema, flag, API, and SSE behavior.
  - Confirm v3 endpoints and task persistence still work.
  - Update architecture/task notes for any implementation decision.

### Acceptance criteria

- [x] Feature flag defaults to off and leaves v3 behavior unchanged.
- [x] Reinitializing the SQLite store is safe and does not lose v3 tasks.
- [x] Run events survive repository/service reconstruction and preserve order.
- [x] Sensitive test values do not appear in stored or streamed event payloads.
- [x] Unknown tools and invalid Patch IR are rejected before execution.
- [x] SSE reconnect can resume after a known event sequence without duplicates.
- [x] Targeted Phase 0 tests pass.
- [x] Full existing test suite passes.
- [x] `git diff --check` passes.

### Completion record

- Started: 2026-07-25
- Completed: 2026-07-25
- Tests:
  - Phase 0 plus directly affected v3 tests: `50 passed`.
  - Full repository suite: `386 passed`.
  - `git diff --check`: passed.
  - Both pytest runs reported one upstream Starlette deprecation warning for
    `fastapi.testclient`; no test failures or unhandled warnings occurred.
  - 2026-07-26 supplemental verification against the local Dify environment:
    - Dify core containers were running; the public read-only version API and
      source checkout both reported Dify `1.14.2`, with DSL `0.6.0`.
    - With v4 disabled, Chat2Dify `/health` returned `200`, identified the real
      Dify source/version, the v3 panel manifest returned `200`, and the v4
      route returned the stable `AGENT_V4_DISABLED` response.
    - With v4 enabled, startup created the five `agent_*` tables alongside
      `workflow_tasks` in WAL mode; Run polling returned `200`.
    - SSE reconnect from `Last-Event-ID: 1` returned only event sequence `2`
      and redacted a persisted Authorization test value.
    - Current Phase 0 targeted regression: `50 passed`; current full repository
      suite: `407 passed, 2 skipped`. The skips are opt-in Phase 1A live tests,
      not Phase 0 acceptance tests.
    - The smoke test was read-only with respect to Dify: it did not authenticate,
      read an application draft, run a workflow, or call any Dify write API.
- Decisions/deviations:
  - The v4 store reuses `CHAT2DIFY_TASK_DB` but creates only separately named
    `agent_*` tables. Startup initializes those tables only when the v4 feature
    flag is enabled; repeat initialization and coexistence with
    `workflow_tasks` are tested.
  - The Phase 0 router is registered as a clean boundary but returns a stable
    `AGENT_V4_DISABLED` response while the flag is off. Its current surface is
    intentionally read-only Session/Run polling plus resumable SSE; no Runtime,
    Patch execution, approval resolution, or Dify write path was started.
  - The initial 2026-07-25 full-suite verification used a temporary minimal
    Dify DSL-version fixture because no adjacent Dify source tree was available
    then. The 2026-07-26 supplemental smoke closed that deployment-evidence gap
    against the real local Dify source and running services.
- Remaining limitations:
  - At the Phase 0 boundary, Workspace mutation, Runtime execution,
    review/approval behavior, and Dify Commit were intentionally deferred to
    Phase 1 and were not exercised by this supplemental acceptance.

## 5. Phase 1A — Existing-app modify vertical slice

Status: `completed`

Dependencies: Phase 0 `completed`

Outcome: an existing Workflow or Chatflow can move through
Observe → Patch → Validate → Review → Approval → Commit while Dify remains
unchanged before approval.

### Tasks

- [x] **P1A-01 — Workflow Snapshot**
  - Read app detail, draft graph, features, conversation/environment
    variables, base Hash, and Dify version.
  - Decompile to `WorkflowPlan`.
  - Store the authoritative base graph separately from model context.
  - Pin a capability snapshot to the Run.

- [x] **P1A-02 — Versioned Agent Workspace**
  - Initialize Workspace v0 from the Snapshot.
  - Persist full Plan snapshots for MVP versions.
  - Track parent, head, validation, patch, reverse patch, and creation time.
  - Implement head lookup and pre-commit version validation.

- [x] **P1A-03 — Transactional Patch Engine**
  - Implement `node.add`, `node.update`, `edge.add`, and `edge.remove`.
  - Generate final node IDs server-side and resolve `temp_ref`.
  - Enforce workspace-version and base-Hash preconditions.
  - Apply operations to a copy, normalize, validate, and commit one new
    Workspace version only on success.
  - Generate a reverse patch.
  - Preserve unrelated Plan and raw graph metadata.

- [x] **P1A-04 — Read and capability tools**
  - Implement `workflow.inspect`.
  - Implement `capability.search`.
  - Implement `node.schema.get`.
  - Limit details and Top K results based on Context Builder requests.

- [x] **P1A-05 — Patch, validation, and diff tools**
  - Implement `workflow.patch`.
  - Implement `workflow.validate` using the existing full validation chain.
  - Implement `workflow.diff` using existing diff/guard behavior.
  - Return stable, sanitized validation and risk observations.

- [x] **P1A-06 — Context Builder and Goal Plan**
  - Build bounded context from goal, app summary, selection, capabilities,
    recent observations, constraints, and remaining budget.
  - Summarize old trace events rather than replaying all data to the model.
  - Persist Goal Plan revisions and step evidence.

- [x] **P1A-07 — Decision provider abstraction**
  - Normalize native tool calling and strict JSON decisions into one contract.
  - Reuse existing planner-provider fallback infrastructure where safe.
  - Add fake deterministic decision provider for tests.
  - Never expose Commit as a model-visible tool.

- [x] **P1A-08 — Builder Agent Runtime**
  - Implement Observe/Plan/Act/Validate/Review states.
  - Enforce iteration, model-call, patch-operation, time, and same-error
    budgets.
  - Persist events and checkpoints after accepted results.
  - Pause durably for `ask_user` and approval.
  - Support cancellation and explicit resume.

- [x] **P1A-09 — Policy and Approval Service**
  - Authorize read/workspace tools automatically.
  - Persist approval bound to Run, Workspace version, base Hash, action,
    risk, and expiry.
  - Invalidate approval when the Workspace head changes.
  - Require separate destructive approval when Guard blocks normal apply.

- [x] **P1A-10 — Modification Commit Adapter**
  - Accept only persisted Workspace version plus persisted approval.
  - Re-read current Dify draft immediately before commit.
  - Return `conflicted` on Hash mismatch without writing.
  - Re-run normalize, validation, compile, diff, and guard.
  - Compile with the authoritative `base_graph`.
  - Reuse `sync_draft_workflow` and save the new Hash/result.
  - Make duplicate Commit requests idempotent.

- [x] **P1A-11 — v4 Run APIs**
  - Create Session and submit Message/Goal.
  - Read Session, Run, events, and diff.
  - Cancel, resume, resolve approval, and commit.
  - Return `202` for asynchronous Run work.
  - Provide polling fallback alongside SSE.

- [x] **P1A-12 — Modify vertical-slice tests**
  - Use fake Dify and decision providers.
  - Cover happy path, invalid patch, validation repair observation, no-op,
    destructive guard, approval expiry, version mismatch, Hash conflict,
    duplicate commit, cancellation, restart, and prompt injection.
  - Verify Dify write count remains zero before valid approval.

### Acceptance scenario

> 在当前 Workflow 中增加一个分类分支，并保持原有其他节点不变。

### Acceptance criteria

- [x] Agent reads the current Workflow instead of relying on prompt memory.
- [x] Agent changes only the relevant nodes and edges.
- [x] Every accepted Patch creates one persisted version and trace.
- [x] Invalid Patch leaves the Workspace head unchanged.
- [x] Review includes business Diff, technical Diff, validation, and risk.
- [x] Dify remains unchanged before approval.
- [x] Approval for version N cannot commit version N+1.
- [x] Current Dify Hash mismatch produces a conflict and no write.
- [x] Valid approval writes through the existing safe core.
- [x] Workflow and Chatflow behavior are both covered.
- [x] All Phase 1A targeted and existing tests pass.
- [x] `git diff --check` passes.

### Completion record

- Started: 2026-07-25
- Completed: 2026-07-25
- Tests:
  - Dedicated Phase 1A vertical-slice tests: `12 passed`.
  - Phase 1A plus directly affected Agent, API, Graph, Diff, Guard,
    Preflight, and v3 main tests: `128 passed`.
  - Full repository suite: `398 passed`.
  - `python -m compileall -q app`: passed.
  - `git diff --check`: passed.
  - The dedicated/full pytest runs reported the existing upstream Starlette
    `fastapi.testclient` deprecation warning. The full run also reported one
    pytest assertion-rewrite warning because the local verification command
    injected the temporary Dify-version fixture in-process; no tests failed.
  - 2026-07-26 live Dify supplemental acceptance:
    - Dify source and running image: `1.14.2`; app DSL: `0.6.0`.
    - Opt-in localhost Workflow and Chatflow cases:
      `2 passed in 10.62s`.
    - Each case imported a uniquely named temporary baseline app, captured the
      real app detail/draft Graph/base Hash, and completed the deterministic
      Observe → Patch → Validate → Review → Approval → Commit path.
    - Before approval, a second real draft read returned the exact original
      Hash and Graph. After approval, Commit returned a new Hash, the Graph
      changed from three to six nodes, and no `temp_ref` leaked into Dify.
    - A duplicate Commit preserved the same Hash and Graph. A separately
      approved change then encountered an externally advanced Dify Hash,
      returned `conflicted`, and left the external Graph unchanged.
    - Both temporary applications were deleted in test cleanup and independently
      verified absent: total app count `0`, matching temporary app count `0`.
    - Current deterministic Phase 1A regression:
      `12 passed, 2 live skipped`; current full repository suite:
      `416 passed, 2 live skipped`.
- Decisions/deviations:
  - The authoritative existing-app Snapshot is a private Run checkpoint that
    stores the base Graph, features, environment/conversation variables, app
    detail, base Hash, Dify version, and pinned Phase 0 node capabilities.
    Public Run responses and model Context exclude the raw Graph and
    environment-variable values.
  - Workspace versions continue to store full `WorkflowPlan` snapshots. The
    model-visible reverse domain remains the four Phase 1A Patch operations;
    accepted versions persist an internal deterministic snapshot-restore
    reverse Patch so reversal is exact without exposing `node.remove` early.
  - Existing Graph compilation now overlays preserved graph-, node-data-, and
    edge-level metadata in addition to layout. This is required so the v4
    Commit adapter can reuse the authoritative base Graph without dropping
    unrelated Dify metadata.
  - Agent Runs use a small v4 thread dispatcher backed by durable
    `agent_runs`, Events, and Workspace checkpoints. Restart marks only active
    work as `interrupted`; durable `waiting_user` and `waiting_approval` states
    remain paused, and no Dify write is replayed automatically.
  - Destructive start-input contract changes use a separate
    `destructive_change` Approval before Commit Approval. Any Workspace Head
    change expires both pending and already-approved records for the old
    version.
  - The initial full-suite verification used a temporary minimal Dify
    DSL-version fixture because no adjacent Dify source tree was available at
    that time. The opt-in supplemental test now restricts mutation to
    localhost, requires explicit credentials and enablement, creates isolated
    disposable apps, never publishes, and verifies cleanup after each case.
- Remaining limitations:
  - Phase 1A itself supports modification of existing Workflow/Chatflow apps
    only. New-app scaffolds/imports were subsequently delivered in Phase 1B
    through a separate adapter.
  - The pinned capability Snapshot intentionally uses the Phase 0 MVP node
    catalog (`llm`, `if-else`, `end`, `answer`); broader live resource
    capabilities remain later scoped work.
  - Canvas context, Workbench UI, Undo, and richer pause/resume UX remain
    Phase 2. Draft Run, execution inspection, and Repair remain Phase 3.

## 6. Phase 1B — New-app create adapter

Status: `completed`

Dependencies: Phase 1A `completed`

Outcome: the same Runtime can create a new Workflow or Chatflow from a
deterministic minimal scaffold and import it only after approval.

### Tasks

- [x] **P1B-01 — Create Session initialization**
  - Allow Session creation without `app_id`.
  - Require explicit `app_mode`.
  - Initialize Workflow as `start → end`.
  - Initialize Chatflow as `start → answer`.
  - Generate scaffold IDs server-side.

- [x] **P1B-02 — Creation context and policy**
  - Use the same Goal Plan, tools, Patch Engine, validation, and review.
  - Represent the absence of a base Hash explicitly.
  - Prevent modification-only operations from running in create mode.

- [x] **P1B-03 — Creation Commit Adapter**
  - Bind approval to the exact Workspace version.
  - Compile and validate DSL using the existing creation path.
  - Import only after approval.
  - Fetch and persist resulting `app_id`, URL, app mode, and draft Hash.
  - Add an idempotency key so retries cannot import duplicate successful apps.

- [x] **P1B-04 — Creation failure recovery**
  - Keep Workspace and Trace after a failed import.
  - Allow correction and a new approval.
  - Distinguish “import failed” from “import succeeded but response recovery
    failed.”

- [x] **P1B-05 — Creation tests**
  - Cover Workflow and Chatflow creation.
  - Cover invalid scaffold mutation, approval/version mismatch, failed import,
    retry, duplicate request, and successful result recovery.
  - Verify no app is imported before approval.

### Acceptance scenario

> 创建一个售后分析 Workflow：接收用户问题，分类后生成专业回复。

### Acceptance criteria

- [x] New-app mode uses a valid deterministic scaffold.
- [x] Runtime and tool behavior are shared with Phase 1A.
- [x] Review is available before any Dify app exists.
- [x] No app is imported before valid approval.
- [x] Successful import is not duplicated by request retry.
- [x] Returned app ID and draft Hash are persisted.
- [x] Workflow and Chatflow create tests pass.
- [x] Full existing suite passes.
- [x] `git diff --check` passes.

### Completion record

- Started: 2026-07-25
- Completed: 2026-07-25
- Tests:
  - Dedicated Phase 1B create-adapter tests: `9 passed`.
  - Phase 1B plus directly affected Phase 1A, Agent domain/store/API,
    Dify client, compiler, preflight, graph, diff, guard, and v3 main tests:
    `253 passed`.
  - Full repository suite: `407 passed`.
  - `python3 -m compileall -q app tests`: passed.
  - `git diff --check`: passed.
  - Supplemental localhost Dify acceptance on 2026-07-26:
    - Dify `1.14.2` / app DSL `0.6.0` accepted both deterministic
      scaffolds; Workflow compiled as `start → end` and Chatflow as
      `start → answer`, with validation, graph compilation, and DSL
      round-trip all passing.
    - The full create Runtime was exercised against the real Console API for
      both `workflow` and `advanced-chat`: review completed while the exact
      test app name was absent, approval imported exactly one app, the
      returned app ID/mode/draft Hash matched Dify, and the imported graphs
      contained `start`, `if-else`, `llm`, and the correct terminal node.
    - Repeating each approved Commit returned its persisted result and left
      exactly one matching Dify app. Both uniquely named temporary apps were
      then deleted by their verified app IDs, and absence was confirmed.
    - Post-acceptance Phase 1B plus Dify-client regression:
      `46 passed`; current full repository suite:
      `416 passed, 2 opt-in live tests skipped`.
    - `python3 -m compileall -q app tests` and `git diff --check`: passed.
  - Pytest reported one upstream Starlette deprecation warning for
    `fastapi.testclient`; no test failures or unhandled warnings occurred.
- Decisions/deviations:
  - Agent Sessions now persist an explicit `modify` or `create` operation.
    A create Session accepts no `app_id`, requires Workflow/Chatflow mode, and
    permits one durable creation Run until import resolves. After successful
    import, the same Session is atomically bound to the returned `app_id` and
    promoted to modify mode so later goals reuse the Phase 1A path.
  - Create Snapshots carry an explicit null base Hash and use stable,
    server-generated UUID5 scaffold node IDs derived from the Session. The
    shared Patch schema still requires the `expected_base_hash` field; create
    Patches send null while modify Patches must match the pinned Dify Hash.
  - Creation Commit persists an idempotency checkpoint before calling Dify,
    forwards the key on the import request, and records a successful import
    before reading its draft Hash. Duplicate completed requests return the
    persisted result; known successful imports retry only result recovery and
    never import again. A localhost Dify `1.14.2` probe proved the upstream
    import route does not honor the forwarded key: two identical imports with
    one `Idempotency-Key` returned two distinct App IDs.
  - A definitive Dify import failure expires the used Approval and leaves the
    Run interrupted with its Workspace and Trace intact for correction,
    resume, and a new version-bound Approval. An ambiguous import response
    fails closed and blocks automatic re-import because Dify may already have
    created the app.
- Remaining limitations:
  - An ambiguous import outcome without a returned import/app ID requires
    manual Dify reconciliation and, if needed, a new create Session; the
    current Run intentionally cannot auto-retry the import.
  - At the Phase 1B completion boundary, Canvas context, Workbench UI, Undo,
    and richer resume UX were intentionally deferred; they are now delivered
    by Phase 2.

## 7. Phase 2 — Canvas context and Agent Workbench

Status: `completed`

Dependencies: Phase 1A and Phase 1B `completed`

Outcome: users can operate on selected canvas elements and follow, pause,
review, approve, undo, and resume Agent work through a durable UI.

### Tasks

- [x] **P2-01 — Host/iframe context protocol**
  - Define versioned `chat2dify.ready`, `dify.context.init`,
    `dify.selection.changed`, `dify.draft.changed`, and context-refresh
    messages.
  - Include selected node/edge IDs, viewport, panel, dirty state, canvas Hash,
    and nonce.

- [x] **P2-02 — Context-channel security**
  - Validate origin and per-panel nonce.
  - Reject malformed or stale context messages.
  - Never use browser-supplied raw Graph as an authoritative Snapshot.
  - Block Commit on dirty canvas or mismatched canvas Hash.

- [x] **P2-03 — Selected graph context**
  - Add selected nodes, edges, and bounded neighborhood to Context Builder.
  - Update context on live selection changes.
  - Make “这个节点/这两个节点之间” resolvable without copying full Graph to
    every model turn.

- [x] **P2-04 — SSE client and fallback**
  - Subscribe with reconnect and last-event cursor.
  - Deduplicate by Run/sequence.
  - Show terminal state consistently.
  - Preserve polling fallback.

- [x] **P2-05 — Agent Timeline and Goal Plan UI**
  - Render business-readable phases and tool outcomes.
  - Render Goal Plan step state and evidence.
  - Keep raw Tool/Patch data in a technical detail view.

- [x] **P2-06 — Diff and approval UI**
  - Render added/updated/removed nodes and edges.
  - Render validation, test status, and risk.
  - Bind approval actions to the exact visible Workspace version.
  - Distinguish normal, destructive, Draft Run, and Commit approval.

- [x] **P2-07 — Undo, pause, and resume**
  - Move Workspace head to a parent version before Dify commit.
  - Generate a compensating Preview for post-commit undo.
  - Resume `waiting_user`, interrupted, and explicitly paused Runs.
  - Never automatically replay side effects.

- [x] **P2-08 — Workbench tests**
  - Add frontend tests for URL/context handshake, origin/nonce rejection,
    selection updates, SSE reconnect/dedup, Timeline, Diff, approval version,
    dirty-state blocking, Undo, and Resume.
  - Add backend integration tests for the same protocol boundaries.

### Acceptance scenario

> 把选中的 LLM 节点 Prompt 改得更专业，并增加 JSON 输出约束。

### Acceptance criteria

- [x] Agent resolves the selected node without requiring its ID in user text.
- [x] Invalid origin or nonce cannot change context.
- [x] Dirty or changed canvas blocks stale Commit.
- [x] Timeline survives refresh and reconnect without duplicate events.
- [x] Visible approval is tied to the visible Workspace version.
- [x] Pre-commit Undo changes only Workspace state.
- [x] Post-commit Undo produces a new reviewed compensating change.
- [x] Targeted frontend/backend tests and full existing suite pass.
- [x] `git diff --check` passes.

### Completion record

- Started: 2026-07-25
- Completed: 2026-07-25
- Tests:
  - Dedicated Phase 2 backend acceptance tests: `11 passed`.
  - Phase 2 plus directly affected Agent state/store/API, Phase 1A/1B, and
    v3 main tests: `119 passed`.
  - Standalone Workbench protocol/SSE/UI-domain tests under Node's test
    runner: `7 passed`.
  - Initial completion full repository suite: `418 passed, 2 skipped`. The
    skips were the opt-in, localhost-only Phase 1A Dify live acceptance tests.
  - 2026-07-26 supplemental live Dify 1.14.2 / DSL 0.6.0 acceptance:
    `2 passed in 7.66s` across one isolated Workflow and one isolated
    Chatflow. The goal referred only to the selected LLM node, the Runtime
    resolved that selection from the canvas constraints, Commit preserved
    unrelated graph and application state, and post-Commit Undo created a
    separately reviewed and approved compensating Run that restored the exact
    baseline Dify draft Hash. Both temporary applications were deleted and
    deletion was verified independently.
  - The default full repository suite after adding those opt-in cases:
    `418 passed, 4 skipped`. All four skips are localhost-only live Dify
    acceptance cases.
  - The adapter was applied to the local Dify 1.14.2 checkout and its
    production Web image, Chat2Dify sidecar, and nginx were rebuilt. The
    sidecar health endpoint reported Agent v4 enabled and DSL 0.6.0.
  - Dify host-component tests in the actual 1.14.2 Web build:
    `3 test files passed, 30 tests passed` (panel protocol, new-app entry, and
    Workflow header entry).
  - In-app browser smoke: Agent Workbench replaced the legacy UI, rendered
    Timeline, Goal Plan, Diff, and Approval regions, and kept its composer
    disabled while waiting for the nonce-bound Dify canvas handshake.
  - JavaScript syntax checks for the Workbench controller, its reusable core,
    and the legacy controller: passed.
  - `python3 -m compileall -q app tests`: passed.
  - `git diff --check`: passed.
  - Pytest reported one upstream Starlette deprecation warning for
    `fastapi.testclient`; no test failures or unhandled warnings occurred.
- Decisions/deviations:
  - The Dify adapter owns selection, viewport, panel, dirty state, and its
    currently visible draft Hash. It sends only bounded identifiers and
    state through a versioned, nonce-bound protocol. The Sidecar continues to
    load the authoritative persisted Graph from Dify and rejects browser
    context that contains a raw Graph.
  - The Context Builder resolves selected nodes and edges against the
    authoritative Snapshot, includes full redacted parameters only for the
    selection, and limits surrounding context to one-hop neighbors.
  - The Workbench replays persisted SSE history on load, reconnects from the
    last sequence, deduplicates by Run and sequence, and falls back to Run
    polling. Business Timeline and Goal Plan views are separate from
    collapsible technical event/Patch data.
  - Canvas context updates are revision-checked atomic store writes so they
    cannot overwrite concurrent Run state. Modification Commit fails closed
    when the canvas is dirty, lacks a verifiable Hash after handshake, or its
    Hash differs from the Run's pinned base Hash.
  - Pause is a durable Run state. Resume is explicit for `waiting_user`,
    `interrupted`, and paused Runs, and resumes the decision loop without
    replaying a completed side-effecting tool or Dify write. Workspace
    initialization also verifies the current phase atomically so a concurrent
    Snapshot capture cannot overwrite an explicit pause.
  - Before Commit, Undo atomically moves the Workspace head to its direct
    parent and invalidates approvals without creating another version. After
    Commit, Undo re-reads the Dify draft, verifies the committed Hash, and
    creates a separate modification Run containing a deterministically
    validated, reviewed compensating version that requires a new Approval.
- Remaining limitations:
  - The in-app browser had no authenticated Dify Console session, so the
    signed-in canvas drawer was not clicked manually and no stored credentials
    were entered into the browser. The exact Dify 1.14.2 production build and
    its 30 passing host-component tests cover the host/iframe handshake,
    origin, source-window, nonce, and context-update boundaries; the live
    Workflow/Chatflow API acceptance covers the authoritative Snapshot,
    selected-node, Approval, Commit, and compensating Undo boundaries.
  - Draft Run approval, execution inspection, repair, and all related
    side-effect budgets remain Phase 3 and were not started.

## 8. Phase 3 — Draft Test, Inspect, and Repair

Status: `completed`

Dependencies: Phase 2 `completed`

Outcome: after structural validation, the Agent can run an approved Draft,
inspect normalized failures, apply bounded repairs, and return a reviewed
result.

### Tasks

- [x] **P3-01 — Side-effect classification**
  - Classify local/read, model-cost, HTTP, Tool, notification/human, and
    unknown nodes.
  - Include side-effect summary in validation/review.
  - Treat unknown behavior conservatively.

- [x] **P3-02 — Draft Run approval and budget**
  - Default to one Session-scoped approval with explicit run count.
  - Require per-run approval for external/unknown side effects unless the user
    grants a narrower explicit allowance.
  - Persist approval scope, expiry, inputs, and remaining run count.

- [x] **P3-03 — Minimal test-input generator**
  - Generate deterministic values from input types and schemas.
  - Require user files for file/file-list inputs unless an explicit fixture is
    available.
  - Use model-generated semantic values only after deterministic schema
    resolution.
  - Allow users to review or override sensitive test inputs.

- [x] **P3-04 — `workflow.test_draft` tool**
  - Dispatch to existing Workflow/Chatflow Draft Run implementations.
  - Enforce approval, timeout, cancellation, and test budget.
  - Record sanitized progress and terminal events.
  - Do not automatically run trigger-based Workflows through normal Draft Run.

- [x] **P3-05 — `execution.inspect` tool**
  - Normalize success, failure, timeout, and cancellation.
  - Identify failed node, node type, stable error code, sanitized upstream
    summary, output summary, and retryability.
  - Do not persist raw secrets or model chain-of-thought.

- [x] **P3-06 — Repair loop**
  - Feed only structured Validation/Execution observations to the decision
    model.
  - Create repair patches through the normal Patch Tool.
  - Revalidate after every repair.
  - Re-run only when approval and budget remain.

- [x] **P3-07 — Loop guards and terminal reporting**
  - Enforce max iterations, model calls, Patch operations, test runs, same
    error retries, time, and provider context.
  - Return partial result, current Diff, attempts, and next action at budget
    exhaustion.

- [x] **P3-08 — Test/repair UI**
  - Show approval scope and remaining tests.
  - Show sanitized test inputs, failed node, repair attempts, and final result.
  - Let the user stop automatic testing without losing the Workspace.

- [x] **P3-09 — Test and repair tests**
  - Cover pure/model-cost/external/unknown side-effect policy.
  - Cover input generation by type.
  - Cover success, failed node, timeout, cancellation, malformed SSE,
    retryable repair, repeated error, exhausted budget, and user stop.
  - Assert no unauthorized Draft Run occurs.

### Acceptance scenario

> 运行当前工作流，并修复变量引用错误，直到能够正常返回结果。

### Acceptance criteria

- [x] Agent cannot run a Draft outside persisted approval and budget.
- [x] External side-effect risk is visible before approval.
- [x] Execution errors are normalized and sensitive values are redacted.
- [x] Repair uses Patch IR and the normal validation chain.
- [x] The same error is not retried more than the configured limit.
- [x] Budget exhaustion returns a reviewable partial result.
- [x] At least one fixed evaluation case repairs a variable reference and
      succeeds on a subsequent approved run.
- [x] Targeted Phase 3 tests and full existing suite pass.
- [x] `git diff --check` passes.

### Completion record

- Started: 2026-07-26
- Completed: 2026-07-26
- Tests:
  - Dedicated deterministic Phase 3 backend tests: `17 passed`.
  - Phase 3 plus directly affected Agent domain/store/API and Phase 1/2
    regressions: `64 passed`.
  - Standalone Workbench protocol/SSE/UI-domain tests: `8 passed`.
  - Full repository suite: `435 passed, 4 skipped`.
  - All four skips are the existing opt-in localhost-only live Dify Phase 1/2
    acceptance cases.
  - `python -m compileall -q app tests`: passed.
  - JavaScript syntax checks for the Workbench controller and reusable core:
    passed.
  - `git diff --check`: passed.
  - Pytest reported one existing upstream Starlette deprecation warning for
    `fastapi.testclient`; no test failures or unhandled warnings occurred.
- Decisions/deviations:
  - Draft side effects are classified as local, model cost, HTTP, Tool,
    notification/human, or unknown. Unknown and external behavior is
    conservative and requires a per-Run approval; local/model-only behavior
    can consume an explicit Session-scoped allowance.
  - Draft approvals persist sanitized input previews, input/request
    fingerprints, expiry, side-effect scope, allowed count, and remaining
    count. The Store atomically reserves both the Approval allowance and the
    Run test budget before the adapter can execute.
  - Draft test observations persist only stable status/error fields, failed
    node identity/type, input/output shapes, aggregate stream counts, and
    redacted messages. Raw SSE, raw node inputs/outputs, credentials, and
    model chain-of-thought are not stored or returned to the decision model.
  - The Runtime automatically executes only the pending Draft Tool Call that
    a user just approved. Reservation happens before the side effect; after a
    restart, an already reserved/in-flight Draft Run is not replayed.
  - Dify `1.14.2` Draft Run endpoints do not accept a candidate Graph. The
    built-in adapter therefore tests only an unchanged persisted Workspace
    baseline and fails closed with
    `DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED` after a Patch. It never
    temporarily syncs the target draft or creates/deletes a hidden test app.
    A localhost probe also sent a modified candidate Graph to the real
    Workflow Draft Run endpoint: Dify returned the persisted baseline output
    and left the draft Hash and Graph unchanged, proving that the extra Graph
    is ignored. The deterministic acceptance uses a workspace-aware Fake
    Adapter to prove the full Test → Inspect → Patch → Validate → Re-test loop.
    This boundary is documented in the architecture and can accept a future
    isolated Dify candidate-execution Adapter without changing Tool
    permissions.
- Remaining limitations:
  - With the built-in Dify `1.14.2` adapter, a repaired uncommitted Workspace
    cannot be re-run against Dify because the upstream API always executes the
    persisted draft. Real post-repair verification therefore requires the
    existing version-bound Review/Commit approval and a new explicit Run.
  - Sensitive values whose input names identify credentials/secrets are never
    persisted in Approval scope. Such inputs remain an explicit user-provided
    boundary until encrypted one-shot secret transport is designed; ordinary
    file inputs require user upload metadata or an explicit fixture.

## 9. Phase 4 — Config apps, Skills, evals, and hardening

Status: `completed`

Dependencies: Phase 3 `completed`

Outcome: extend the proven Runtime to configuration-style apps and reusable
Skills, then measure and harden it against a fixed evaluation suite.

### Tasks

- [x] **P4-01 — `ConfigPatchIR` domain**
  - Define a separate typed patch model for `chat`, `completion`, and
    `agent-chat`.
  - Keep it separate from Graph Patch IR.
  - Add field-level risk and precondition rules.

- [x] **P4-02 — Config-app tools and adapters**
  - Add inspect, patch, validate, diff, review, approval, and commit behavior
    for Chatbot, Completion, and Dify Agent apps.
  - Reuse existing model-config Hash behavior.
  - Preserve v3 configured-app paths as fallback.

- [x] **P4-03 — Skill Registry**
  - Define Skill metadata, applicability, required tools, validation rules,
    common errors, and examples.
  - Add `skill.search` and deterministic Skill loading.
  - Skills guide tool use; they do not gain extra permissions.

- [x] **P4-04 — Initial Skills**
  - Add error handling.
  - Add human fallback.
  - Add JSON output.
  - Add file upload/document extraction.
  - Add knowledge retrieval.
  - Add tests and evaluation cases for each.

- [x] **P4-05 — Evaluation framework**
  - Add versioned fixtures, cases, runner, graders, and reports.
  - Fix goal, Snapshot, allowed capabilities, invariants, required/forbidden
    changes, budgets, side-effect policy, and expected validation per case.
  - Run without live providers by default; allow explicit live-provider runs.

- [x] **P4-06 — Required evaluation cases**
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

- [x] **P4-07 — Dify compatibility matrix**
  - Record supported Dify versions/DSL versions.
  - Pin Capability Catalog behavior to Dify version.
  - Add fixtures for supported version differences.
  - Fail closed for unsupported mutation while preserving read/diagnostic
    behavior where safe.

- [x] **P4-08 — Security and reliability hardening**
  - Add prompt-injection fixtures.
  - Add secret-redaction fixtures.
  - Add SQLite concurrency/load tests.
  - Add long Trace/context-compaction tests.
  - Add duplicate/reordered SSE and restart tests.

- [x] **P4-09 — Product and operator documentation**
  - Document configuration, data retention, approvals, side effects, recovery,
    troubleshooting, and compatibility.
  - Document migration and v3 fallback.
  - Document how to add a Node Definition, Tool, Skill, and evaluation case.

### Acceptance criteria

- [x] Graph Patch IR and Config Patch IR remain separate typed domains.
- [x] Config apps cannot bypass approval or Hash checks.
- [x] Skills cannot expand Tool permissions.
- [x] Evaluation suite produces a reproducible machine-readable report.
- [x] Security fixtures cannot expose test secrets or elevate permissions.
- [x] Dify compatibility behavior is explicit and tested.
- [x] Selected release metrics in Section 10 are met.
- [x] Targeted Phase 4 tests and full existing suite pass.
- [x] `git diff --check` passes.

### Completion record

- Started: 2026-07-26
- Completed: 2026-07-26
- Tests:
  - Dedicated Phase 4 configured-app, Skill, evaluation, compatibility,
    security, SQLite load, context-compaction, and restart tests:
    `16 passed`.
  - Phase 4 plus directly affected Agent Runtime/store/API, Phase 1–3, v3
    assistant/configured-app, Dify client, and main regressions:
    `227 passed`; the final full suite below also includes the last two
    Phase 4 hardening assertions.
  - Standalone Workbench protocol/SSE/UI-domain tests: `9 passed`, including a
    reordered-event rejection assertion and configured-app Workbench mode
    eligibility.
  - Full repository suite: `451 passed, 4 skipped`. All four skips are the
    existing opt-in localhost-only Dify Phase 1/2 acceptance cases.
  - Deterministic evaluation runner: passed; a second report generated under
    `/tmp` was byte-identical to
    `app/evals/reports/phase4-release.json`.
  - Evaluation metrics: final reviewable Plan/DSL/config validity `100%`;
    goal completion `90%`; unrelated preservation `100%`; designated
    auto-repair `100%`; readable structured failure Trace `100%`;
    unapproved writes `0`; incorrect Hash-conflict overwrites `0`.
  - `python -m compileall -q app tests`: passed.
  - JavaScript syntax check and Node test runner: passed.
  - `git diff --check`: passed.
  - Pytest reported one upstream Starlette `fastapi.testclient` deprecation
    warning; no test failures or unhandled warnings occurred.
- Decisions/deviations:
  - Configured-app v4 scope modifies existing `chat`, `completion`, and
    `agent-chat` apps. New configured-app creation remains on the preserved v3
    path for v4.0.0; this selected scope is explicit in product and migration
    documentation.
  - `ConfigPatchDocument` is a separate discriminated operation union for
    prompt, model, experience, and Agent settings. It cannot parse Graph Patch
    operations or arbitrary paths. Whole-config Workspace snapshots preserve
    unrelated fields, while field preconditions and per-operation risk are
    deterministic.
  - Config Commit uses Dify `hash`, `updated_at`, or `version` with the same
    precedence as the v3 configured-app path, and falls back to a canonical
    full-config SHA-256 fingerprint when Dify provides no token. Commit
    re-reads immediately, requires persisted version-bound approval, and stops
    without a write on mismatch.
  - Skills are versioned server metadata. `skill.search` loads only Skills
    whose mode-specific required Tools are already visible under Policy;
    loading a Skill cannot register, reveal, approve, or authorize a Tool.
  - The production mutation matrix supports Dify `1.14.x` (tested `1.14.2`)
    with App DSL `0.6.0`. Unknown pairs keep bounded read/validation
    diagnostics but fail closed for Graph/Config mutation. The `test` /
    `9.9.9` rule is a deterministic repository fixture only.
  - Release hardening replaced direct expected-result replay with a
    deterministic executor that drives the real Runtime, Registry, Workspace,
    Patch, Validation, Review, Approval, and Draft Run services. It retains
    stable ordering and makes no Provider, Dify, or Commit call. The fixed
    file-extraction case pauses with `DRAFT_TEST_FILE_REQUIRED` when no user
    file or approved fixture exists; that negative invariant yields the
    measured `90%` goal-completion rate with a readable structured reason.
- Remaining limitations:
  - New Chatbot, Completion, and Agent creation, configured-app compensating
    Undo, and configured-app Draft testing remain on explicit v3 paths in
    v4.0.0.
  - Release Gate supplemental acceptance ran the real Config Runtime,
    version-bound Approval, and Config Commit against Dify 1.14.2 for
    `chat`, `completion`, and `agent-chat`. Dify canonicalizes some
    inapplicable/default fields on write (for example Completion retrieval
    display defaults); acceptance therefore verifies the requested prompt and
    every semantically applicable unrelated field after authoritative
    readback.
  - The formal v4.0.0 Release Gate remains a separate `/goal`. Version
    consistency, release-candidate packaging, and final rollout sign-off were
    not changed in Phase 4.

## 10. v4.0.0 Release Gate

Status: `completed`

The release gate is verification and hardening, not a place to add unrelated
features.

Phase 4 metric snapshot (not a Release Gate completion): the reproducible
Runtime-executed report at `app/evals/reports/phase4-release.json` records reviewable
validity `100%`, goal completion `90%`, unrelated preservation `100%`,
designated auto-repair `100%`, readable structured failure Trace `100%`,
unapproved writes `0`, and incorrect Hash-conflict overwrites `0`. The separate
Release Gate `/goal` must still re-run and sign off every item below.

### Safety gates

- [x] Unapproved Dify draft writes in tests/evals: `0`.
- [x] Incorrect overwrites after Hash conflict: `0`.
- [x] Approval for an old Workspace version cannot commit a new version.
- [x] Invalid Plan/DSL cannot enter Commit.
- [x] Draft Run cannot exceed approval scope or budget.
- [x] Stored/streamed traces pass secret-redaction fixtures.
- [x] Restart does not automatically replay side-effecting work.

### Quality gates

- [x] Final Plan/DSL validity among reviewable evaluation results: `100%`.
- [x] Goal completion rate on the fixed release set: `>= 80%`.
- [x] Unrelated node preservation rate: `>= 95%`.
- [x] Auto-repair rate for designated repairable failures: `>= 60%`.
- [x] Every failed Run has a readable Trace and structured terminal reason.
- [x] Workflow and Chatflow create/modify scenarios pass.
- [x] Selected configured-app scope is documented and tested.

### Compatibility and operations

- [x] Full v3 test suite passes.
- [x] Feature flag off preserves v3 behavior.
- [x] Feature flag rollout and rollback are documented.
- [x] SQLite schema initialization/upgrade is tested on an existing v3 DB.
- [x] Supported Dify/DSL versions are documented.
- [x] Cancellation, restart, SSE reconnect, and conflict recovery are tested.

### Release consistency

- [x] `pyproject.toml`, `app/__init__.py`, FastAPI metadata, manifest, health,
      Docker/deployment files, and README consistently report `4.0.0`.
- [x] README links to architecture, tasks, configuration, and migration docs.
- [x] No Phase status or task checkbox contradicts the implementation.
- [x] `git diff --check` passes.
- [x] Full test/evaluation commands and results are recorded below.

### Release completion record

- Started: 2026-07-26
- Completed: 2026-07-26
- Release candidate: package/image/application metadata aligned to `4.0.0`;
  wheel `chat2dify-4.0.0` built and inspected; local container
  `chat2dify:4.0.0` reported healthy with Dify `1.14.2`, DSL `0.6.0`, and the
  v4 feature flag off by default. Final release sign-off is complete. The user
  explicitly waived successful terminal acceptance for the unavailable
  OpenAI-compatible key and the rate/timeout-limited free NVIDIA service;
  neither Provider is a required v4.0.0 production dependency.
- Tests:
  - Full repository suite:
    `.venv/bin/python -m pytest -q` → `462 passed, 12 skipped`, one upstream
    Starlette `TestClient` deprecation warning. The skips are explicitly
    opt-in localhost Dify and real-Provider acceptance cases.
  - Localhost Dify 1.14.2 acceptance:
    `CHAT2DIFY_LIVE_DIFY_ACCEPTANCE=1 .venv/bin/python -m pytest -q
    tests/test_agent_phase1a_live.py tests/test_agent_phase2_live.py
    tests/test_agent_release_live.py` → `11 passed, 1 skipped`. This covered
    Workflow/Chatflow create, modify, conflict, selection, compensating Undo,
    Chat/Completion/Agent Config Runtime → Approval → Commit, a real Draft Run
    candidate-Graph rejection probe, and a duplicate-import idempotency probe.
    The skipped case is the separately gated real Provider.
  - Workbench Node tests: `9 passed`; JavaScript syntax checks passed.
  - Dify Web Adapter focused component suites: `5 files / 35 tests passed`,
    covering configured-app trigger modes, secure panel context, configuration
    entry composition, Workflow header entry, and create card. Full Dify Web
    `pnpm type-check` passed.
  - Dify 1.14.2 Web production builds passed under both Next.js and Vinext.
    The running `langgenius/dify-web:1.14.2-chat2dify` image contains the
    compiled configured-app Builder entry.
  - Signed-in browser acceptance verified the Dify Apps create entry and the
    Config-page Builder entry. A temporary host Sidecar and then the real
    Docker/Nginx Sidecar with `agent_v4=true` verified Workflow create and real
    Config modify Workbench session initialization, including the actual
    Dify-hosted iframe.
    The first visual run exposed the legacy shell covering the v4 Workbench;
    the global deterministic `hidden` rule fixed it and the follow-up DOM and
    screenshot showed only the v4 Workbench. The current-source
    `chat2dify:4.0.0` image was rebuilt, the temporary Config app was deleted,
    and the running container was restored healthy with the default
    `agent_v4=false`.
  - The explicitly authorized Provider diagnostics used the full `8/8` call
    allowance across bounded live runs. The early failures led to a
    standards-compliant wire alias for dotted Typed Tool names and safe
    provider-attempt diagnostics. A single-provider diagnostic then proved
    that the OpenAI-compatible endpoint rejects the configured request with
    HTTP `403`, while NVIDIA successfully returned canonical
    `workflow.inspect`; Runtime executed the Tool and persisted its sanitized
    result before the deliberately one-call budget stopped the Run. The
    Provider protocol is therefore live-verified, but the complete multi-call
    path to Review required a newly authorized NVIDIA-only run.
  - The second explicitly authorized NVIDIA-only allowance also used `8/8`
    calls: the first bounded run received HTTP `503`; the next seven-call run
    completed two bounded inspections, one transactional title Patch,
    deterministic validation, and a low-risk readable partial Diff without a
    Dify write, but exhausted the budget before emitting `finish` and
    `review.ready`. This live evidence exposed two deterministic Runtime gaps:
    configured transient request retries were not executed, and model-supplied
    `goal_step_id` incorrectly advanced Goal Plan steps. The Adapter now
    retries only network/429/5xx-class failures within the model-call budget,
    and Runtime advances Goal Plan state only from registered Tool semantics.
    Focused retry and mislabeled-step regression tests pass. Terminal Review
    was not reached in this allowance.
  - A third NVIDIA-only allowance authorized up to eight calls and used `4/8`.
    One real decision completed `workflow.inspect`; the next decision exhausted
    its configured attempts with one connection error and two HTTP `503`
    responses. The remaining four calls were intentionally not used after the
    user confirmed the free service is rate/timeout constrained and waived its
    successful terminal path. This run exposed that retryable Provider
    exhaustion was persisted as terminal `failed` even though the Tool
    checkpoint and model-call budget remained. Runtime now records such a Run
    as recoverable `interrupted`; explicit Resume continues from the last
    accepted Tool result without replaying a side effect. Non-retryable 4xx,
    decision-contract failures, and exhausted budgets remain terminal.
  - `python -m compileall -q app tests`: passed.
  - `git diff --check`: passed.
- Evaluation report:
  `app/evals/reports/phase4-release.json`; all ten cases executed the real
  Runtime boundary. Reviewable validity `100%`, goal completion `90%`,
  unrelated preservation `100%`, designated auto-repair `100%`, readable
  failure Trace `100%`, unapproved writes `0`, and incorrect conflict
  overwrites `0`. A second `/tmp` report was byte-identical.
- Supported Dify versions: Dify `1.14.x` (live acceptance `1.14.2`) with App
  DSL `0.6.0`.
- Known limitations:
  - Real Provider evidence proves sanitized transport, Typed Tool aliasing,
    Inspect, Patch, deterministic validation, partial Diff, bounded retries,
    budget charging, and failure closing, but not a successful terminal
    `review.ready` event. The user explicitly waived that success path because
    there is no usable OpenAI-compatible key and the free NVIDIA endpoint is
    rate/timeout constrained. Re-run the gated live test when a stable
    production Provider is available; no Provider acceptance performed a Dify
    draft write.
  - Dify 1.14.2 cannot execute an uncommitted candidate Graph. The built-in
    adapter fails closed with `DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED`. A real
    Start → End Draft Run proved an attached candidate Graph is ignored:
    Dify returned the persisted baseline and preserved the exact draft Hash
    and Graph. Real post-repair execution therefore requires an approved
    Commit and a new explicit Run.
  - An ambiguous create-import response without an app/import ID intentionally
    blocks automatic retry and requires manual Dify reconciliation. A real
    duplicate-import probe proved that Dify creates two distinct apps when the
    same DSL is submitted twice with the same `Idempotency-Key`.
- Rollback procedure: set `CHAT2DIFY_AGENT_V4_ENABLED=false` and restart the
  Sidecar. v3 routes and behavior remain registered; no v4 schema downgrade is
  required. Restore the prior image tag only if the application binary itself
  must also be rolled back.

### Real-environment acceptance audit

| Area | Current evidence | Status | Remaining blocker / condition |
| --- | --- | --- | --- |
| Release checklist | Every code, safety, quality, compatibility, version, package, documentation, and signed-in host-embedded UI checkbox above has reproducible evidence; external Provider success was explicitly waived because neither available endpoint is a production dependency | Verified and signed off | None |
| Evaluation fidelity | Ten fixed cases drive the real Runtime/Registry/Workspace/validation/review services; scores come from resulting state/events | Verified offline | Provider and Dify adapters remain deterministic by design |
| Patch and capability claims | Typed conversation-variable operations, nine node definitions, and sanitized live datasets/models/tools/strategies/triggers are covered by deterministic tests | Verified | Broader node coverage remains backlog, not a release claim |
| Runtime version discovery | Container reads Dify `1.14.2` from `api/pyproject.toml`; health reports Chat2Dify `4.0.0` and DSL `0.6.0` | Verified live | None |
| Workflow/Chatflow modify | Real Dify Hash, approval, Commit, conflict, selection, and compensating Undo cases pass | Verified live | None |
| Workflow/Chatflow create | Real Runtime review and approved import pass for both modes; duplicate Commit is idempotent and temporary apps are deleted | Verified live | Ambiguous transport outcomes remain a separate fail-closed case below |
| Config apps | Real Chat, Completion, and Agent Runtime → Approval → Config Commit passes; Sidecar mode/no-canvas tests pass; the Dify configuration-page host entry passes 35 focused tests, full type-check, Next.js/Vinext production builds, compiled-image inspection, and signed-in Docker-hosted browser rendering | Verified live/API/browser | None |
| OpenAI-compatible decision Provider | A bounded real request loaded a sanitized Dify Snapshot and reached the configured endpoint. Runtime persisted only Provider name, `HTTPStatusError`, and status `403`; non-retryable 4xx behavior is regression-tested | Success path waived by explicit user decision; failure-close verified live | None for release; no usable API key is currently available |
| NVIDIA decision Provider | Three authorized windows proved real Inspect, transactional Patch, deterministic validation, readable partial Diff, bounded request charging, retryable connection/503 handling, and no Dify write. The third used `4/8`; the remaining calls were not used | Terminal success waived by explicit user decision; partial Runtime and failure paths verified live | None for release; optionally re-run `finish` → `review.ready` when a stable non-free Provider quota is available |
| Signed-in Workbench journey | Signed-in Dify create and Config entries open; temporary and real Docker/Nginx flag-on Sidecars report `Workbench 已就绪`; final iframe DOM and screenshot contain only v4 Workbench; container was restored to the default flag-off state | Verified live in browser | None |
| Uncommitted candidate Draft Run | A real Dify 1.14.2 Start → End run returned the persisted baseline when the request included a modified candidate Graph, and preserved the exact draft Hash/Graph. The Fake workspace-aware adapter separately proves Test → Inspect → Repair | Verified live fail-closed upstream boundary | Dify 1.14.2 ignores candidate Graph/DSL execution input; post-repair execution requires approved Commit plus a new explicit Run |
| Ambiguous create import | A real Dify 1.14.2 probe submitted identical DSL twice with one `Idempotency-Key` and received two distinct App IDs; both temporary apps were deleted. Checkpoint/receipt and fail-closed retry behavior are deterministic and tested | Verified live upstream non-idempotency/manual recovery boundary | With no returned app/import ID, operator reconciliation is required because upstream exposes no safe correlation primitive |
| Full environment/package | `462 passed, 12 skipped`; 9 Node Workbench tests, reproducible eval, syntax/compile checks, wheel inspection, image rebuild, and container health pass | Verified | None |

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
| 2026-07-25 | Phase 0 | Gate the registered v4 router and initialize its store only when `CHAT2DIFY_AGENT_V4_ENABLED` is true | Keeps v3 as the effective default path while making flag-on startup deterministic | `app/main.py`, `app/api/agent_v4.py` |
| 2026-07-25 | Phase 0 | Keep the Phase 0 API read-only and limit it to persisted Session/Run reads plus resumable SSE | Establishes API and event primitives without starting Phase 1 Runtime or mutation behavior | `app/api/agent_v4.py` |
| 2026-07-26 | Phase 0 supplemental | Keep formal Phase 0 tests deterministic and use the running Dify instance only for read-only deployment smoke | Phase 0 has no Dify mutation boundary; real-environment startup, version recognition, flag gating, persistence, and SSE can still be verified without credentials or draft writes | `docs/tasks.md` |
| 2026-07-25 | Phase 1A | Persist the authoritative base Graph in a private Run Snapshot and expose only bounded Plan summaries/tools to the model and public Run API | Commit needs exact Graph metadata, while credentials and environment values must not cross the model/public boundary | `app/agent/snapshot.py`, `app/agent/context.py`, `app/api/agent_v4.py` |
| 2026-07-25 | Phase 1A | Use transactional full-Plan Workspace versions with an internal snapshot-restore reverse Patch while keeping the model-visible Patch union limited to the four Phase 1A operations | Guarantees exact reversal and atomic Head movement without prematurely exposing `node.remove` or arbitrary JSON Patch | `app/agent/workspace.py`, `app/agent/store.py` |
| 2026-07-25 | Phase 1A | Keep Commit outside the Tool Registry and require version/base-Hash-bound persisted approval, plus a separate destructive approval when Guard reports high risk | Prevents prompt injection, stale approval, and Hash races from authorizing a Dify write | `app/agent/approval.py`, `app/agent/commit.py`, `app/agent/policy.py` |
| 2026-07-26 | Phase 1A supplemental | Keep the default suite deterministic and add an explicitly enabled localhost-only real Dify acceptance that creates and deletes isolated Workflow/Chatflow fixtures | Real Dify 1.14.2 evidence closes the protocol gap for draft Hash, metadata-preserving writeback, duplicate Commit, and conflict behavior without introducing a default external dependency or publishing an app | `tests/test_agent_phase1a_live.py`, `pyproject.toml`, `docs/tasks.md` |
| 2026-07-25 | Phase 1B | Represent new-app work as an explicit create Session and null-Hash Snapshot with a stable server-generated scaffold, then promote the Session to modify mode after import | Reuses the Phase 1A Runtime, Workspace, tools, validation, review, and approval chain without pretending a Dify app or base Hash exists before approval | `app/agent/state.py`, `app/agent/snapshot.py`, `app/agent/service.py`, `app/agent/workspace.py` |
| 2026-07-25 | Phase 1B | Persist an import checkpoint before the Dify call and a successful-import receipt before draft recovery; fail closed on ambiguous outcomes | Makes duplicate Commit retries idempotent and separates a definitive import failure from recovery of an app that Dify already created | `app/agent/commit.py`, `app/agent/store.py`, `app/dify/client.py` |
| 2026-07-26 | Phase 2 supplemental | Keep live Dify acceptance opt-in and exercise selection-bound modification plus reviewed compensating Undo for both Workflow and Chatflow; validate the host protocol in the exact Dify 1.14.2 Web build | Closes the real-environment gaps without making the deterministic default suite depend on Dify or broadening scope into Phase 3 Draft Run behavior | `tests/test_agent_phase2_live.py`, `tests/test_agent_phase1a_live.py`, `deploy/dify/web-adapter/`, `docs/tasks.md` |
| 2026-07-26 | Phase 3 | Put Draft execution behind a workspace-aware Adapter; make the built-in Dify 1.14.2 Adapter fail closed for patched candidates instead of testing stale state or temporarily writing Dify | The upstream Draft Run payload accepts inputs but no candidate Graph/DSL. Temporary sync or hidden app import would violate the modeled write/Hash/approval boundary | `app/agent/execution.py`, `app/agent/tools/draft_run.py`, architecture Section 10.5 |
| 2026-07-26 | Phase 4 | Keep configured-app v4 scope to modification of existing Chatbot, Completion, and Agent apps; leave new configured-app creation on v3 | Existing model-config read/preview/write behavior is reusable, while a new create/import Adapter would need separate idempotency and live acceptance beyond the selected Phase 4 scope | `app/agent/config_app.py`, `app/agent/config_commit.py`, `docs/agent-v4-operations.md` |
| 2026-07-26 | Phase 4 | Use a separate typed `ConfigPatchDocument` and versioned full-config Workspace, with field preconditions, deterministic risk, persisted approval, and immediate model-config Hash/fingerprint re-read | Graph and configured-app states have different invariants; keeping their Patch unions separate prevents arbitrary config paths and stale approval/Hash writes | `app/agent/config_patch.py`, `app/agent/config_app.py`, architecture Section 7.5 |
| 2026-07-26 | Phase 4 | Treat Skills as versioned server metadata whose required Tools must already be visible under Policy | Reusable guidance must not become a second permission system or expose Commit/Dify write capabilities | `app/agent/skills.py`, `app/agent/runtime.py` |
| 2026-07-26 | Phase 4 | Pin capabilities and mutation behavior to a tested Dify/DSL compatibility decision; unmatched versions remain diagnostic-only | Dify schema/API drift should fail closed for writes while preserving safe inspection needed for troubleshooting | `app/agent/compatibility.py`, `docs/compatibility/dify-v4.md` |
| 2026-07-26 | Release Gate | Execute default release cases through the real Agent Runtime and deterministic core, replacing direct expected-result replay; keep Provider, Dify, and Commit behind explicit adapters | Release metrics must prove Runtime/Workspace behavior while remaining byte-reproducible and free of network calls, cost, or external writes | `app/evals/runtime_executor.py`, `app/evals/fixtures/scenarios/`, architecture Section 18.4 |
| 2026-07-26 | Release Gate | Read Dify release version from the mounted `api/pyproject.toml`, with `git describe` only as fallback | The production image intentionally omits Git; returning `unknown` incorrectly forced supported Dify 1.14.2 into diagnostic-only mode | `app/dify/version.py`, `docs/deployment/dify-compose.md`, architecture Section 18.5 |
| 2026-07-26 | Release Gate | Enable the v4 Workbench and add a Dify configuration-page host entry for modification of existing Chatbot, Completion, and Agent apps without a Canvas Context handshake | Config apps use persisted `model_config`, not graph selection/dirty state; reusing the review/approval UI closes both Sidecar eligibility and host-entry gaps without pretending configured-app creation is supported | `app/static/agent-workbench.js`, `app/static/agent-workbench-core.mjs`, `deploy/dify/web-adapter/`, architecture Sections 7.5 and 14.2 |
| 2026-07-26 | Release Gate | Keep real Provider acceptance behind a second explicit flag and stop it at Review | A live decision call transfers sanitized workflow context and can incur cost; Dify write/publish remain outside that acceptance while user authorization is still explicit | `tests/test_agent_release_live.py`, `docs/agent-v4-operations.md` |
| 2026-07-26 | Release Gate | Translate canonical dotted Typed Tool names to deterministic OpenAI-compatible wire aliases and map tool calls back before Registry dispatch; persist only redacted provider-attempt diagnostics | Live Provider acceptance proved that canonical Registry names are outside the function-name grammar and that a generic terminal class alone is insufficient to reconcile provider/model/API failures safely | `app/agent/decision.py`, `app/agent/runtime.py`, `tests/test_agent_phase1a.py` |
| 2026-07-26 | Release Gate | Give the HTML `hidden` attribute an explicit `display: none !important` rule across the Sidecar UI | Signed-in visual acceptance showed that authored grid layout rules could leave the legacy shell covering an initialized v4 Workbench even though the DOM state was correct | `app/static/styles.css`, `tests/test_main.py` |
| 2026-07-26 | Release Gate | Execute configured Provider retries only for network, 408/425/429, and 5xx failures, with every request charged to the server model-call budget | A real NVIDIA run returned transient HTTP 503 while `PLANNER_REQUEST_RETRIES=2` was configured but ignored; authentication/request-contract 4xx failures must still fail fast | `app/agent/decision.py`, `tests/test_agent_phase1a.py`, architecture Section 5.2 |
| 2026-07-26 | Release Gate | Treat model-supplied `goal_step_id` as Trace metadata and advance Goal Plan steps only from deterministic Tool semantics and validated results | Live NVIDIA decisions mislabeled Inspect as Patch/Review and Patch as Validate, causing false completion and redundant calls; the model must not approve its own progress or skip required gates | `app/agent/runtime.py`, `tests/test_agent_phase1a.py`, architecture Section 5.3 |
| 2026-07-26 | Release Gate | Keep the built-in Dify 1.14.x Draft Run Adapter fail-closed for a changed Workspace | A real Start → End probe attached a modified candidate Graph, but Dify executed the persisted baseline and preserved the exact Hash/Graph, proving the request field is ignored | `tests/test_agent_release_live.py`, `app/agent/execution.py`, architecture Section 10.5 |
| 2026-07-26 | Release Gate | Treat an import outcome without an App/Import ID as ambiguous and never auto-retry it | A real Dify 1.14.2 probe submitted identical DSL twice with one `Idempotency-Key` and received two distinct App IDs; upstream does not provide client-key idempotency or a safe correlation lookup | `tests/test_agent_release_live.py`, `app/agent/commit.py`, `docs/compatibility/dify-v4.md` |
| 2026-07-26 | Release Gate | Interrupt rather than terminally fail a Run when every exhausted decision-Provider attempt is retryable and model-call budget remains | A real NVIDIA Run completed Inspect, then encountered one connection error and two HTTP 503 responses; the accepted Tool checkpoint and four calls of budget remained safe to resume explicitly | `app/agent/runtime.py`, `tests/test_agent_phase1a.py`, architecture Section 5.2 |
| 2026-07-26 | Release Gate | Waive successful terminal acceptance for the currently configured external Providers | The user has no usable OpenAI-compatible key and the free NVIDIA endpoint is rate/timeout constrained; both failure paths were exercised live, neither Provider is a required v4.0.0 production dependency, and all deterministic/live Dify release gates pass | `docs/tasks.md`, `tests/test_agent_release_live.py` |
