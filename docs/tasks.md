# Chat2Dify v4.0.0 Development Tasks

> - Branch: `v4.0.0`
> - Overall status: `in_progress`
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
    never import again.
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
  - Full repository suite: `418 passed, 2 skipped`. The skips are the
    opt-in, localhost-only Dify live acceptance tests.
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
  - The repository-level Workbench core and backend acceptance suites are
    self-contained and passed. A host-component Vitest fixture was also added
    to the vendored Dify adapter, but the adjacent Dify checkout has no
    installed web `node_modules`, so that optional host-repository fixture was
    not executed here.
  - Draft Run approval, execution inspection, repair, and all related
    side-effect budgets remain Phase 3 and were not started.

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
| 2026-07-25 | Phase 0 | Gate the registered v4 router and initialize its store only when `CHAT2DIFY_AGENT_V4_ENABLED` is true | Keeps v3 as the effective default path while making flag-on startup deterministic | `app/main.py`, `app/api/agent_v4.py` |
| 2026-07-25 | Phase 0 | Keep the Phase 0 API read-only and limit it to persisted Session/Run reads plus resumable SSE | Establishes API and event primitives without starting Phase 1 Runtime or mutation behavior | `app/api/agent_v4.py` |
| 2026-07-26 | Phase 0 supplemental | Keep formal Phase 0 tests deterministic and use the running Dify instance only for read-only deployment smoke | Phase 0 has no Dify mutation boundary; real-environment startup, version recognition, flag gating, persistence, and SSE can still be verified without credentials or draft writes | `docs/tasks.md` |
| 2026-07-25 | Phase 1A | Persist the authoritative base Graph in a private Run Snapshot and expose only bounded Plan summaries/tools to the model and public Run API | Commit needs exact Graph metadata, while credentials and environment values must not cross the model/public boundary | `app/agent/snapshot.py`, `app/agent/context.py`, `app/api/agent_v4.py` |
| 2026-07-25 | Phase 1A | Use transactional full-Plan Workspace versions with an internal snapshot-restore reverse Patch while keeping the model-visible Patch union limited to the four Phase 1A operations | Guarantees exact reversal and atomic Head movement without prematurely exposing `node.remove` or arbitrary JSON Patch | `app/agent/workspace.py`, `app/agent/store.py` |
| 2026-07-25 | Phase 1A | Keep Commit outside the Tool Registry and require version/base-Hash-bound persisted approval, plus a separate destructive approval when Guard reports high risk | Prevents prompt injection, stale approval, and Hash races from authorizing a Dify write | `app/agent/approval.py`, `app/agent/commit.py`, `app/agent/policy.py` |
| 2026-07-26 | Phase 1A supplemental | Keep the default suite deterministic and add an explicitly enabled localhost-only real Dify acceptance that creates and deletes isolated Workflow/Chatflow fixtures | Real Dify 1.14.2 evidence closes the protocol gap for draft Hash, metadata-preserving writeback, duplicate Commit, and conflict behavior without introducing a default external dependency or publishing an app | `tests/test_agent_phase1a_live.py`, `pyproject.toml`, `docs/tasks.md` |
| 2026-07-25 | Phase 1B | Represent new-app work as an explicit create Session and null-Hash Snapshot with a stable server-generated scaffold, then promote the Session to modify mode after import | Reuses the Phase 1A Runtime, Workspace, tools, validation, review, and approval chain without pretending a Dify app or base Hash exists before approval | `app/agent/state.py`, `app/agent/snapshot.py`, `app/agent/service.py`, `app/agent/workspace.py` |
| 2026-07-25 | Phase 1B | Persist an import checkpoint before the Dify call and a successful-import receipt before draft recovery; fail closed on ambiguous outcomes | Makes duplicate Commit retries idempotent and separates a definitive import failure from recovery of an app that Dify already created | `app/agent/commit.py`, `app/agent/store.py`, `app/dify/client.py` |
