# Chat2Dify v5.0.0 Development Tasks

> - Branch: `v5.0.0`
> - Overall status: `in_progress`
> - Product architecture:
>   [v5 AI Workflow Studio](architecture/v5-ai-workflow-studio.md)
> - Agent instructions: [`AGENTS.md`](../AGENTS.md)
> - Copyable goals:
>   [`docs/goals/v5.0.0-goal-prompts.md`](goals/v5.0.0-goal-prompts.md)
> - v4 evidence:
>   [`docs/archive/v4.0.0-tasks.md`](archive/v4.0.0-tasks.md)
> - Last updated: 2026-07-30

## 1. Product outcome

v5.0.0 delivers **Chat2Dify AI Workflow Studio**, one product journey for
building, reusing, testing, reviewing, releasing, and operating Dify
applications.

The visible product surfaces are:

| Surface | User outcome |
| --- | --- |
| Studio Home | See applications, active drafts, reviews, quality regressions, releases, and incidents |
| Build Studio | Co-build with AI and Dify context, compare safe candidate variants, and understand every change |
| Blueprint Gallery | Apply proven Dify-native patterns through guided setup instead of rebuilding common graphs |
| Scenario Lab | Test candidates against business scenarios and compare quality, latency, model usage, cost, and risk |
| Review & Release Center | Collaborate, approve the exact version, map environments, release safely, and roll back |
| Run Center | Connect executions to released versions and turn failures into evidence-backed repair proposals |

Infrastructure work completes a phase only when it enables the specified user
journey in the real Workbench.

## 2. Version decision

This roadmap belongs on `v5.0.0`, not `v4.0.1`, because it changes:

- the product information architecture and primary navigation;
- the Builder experience from one candidate to a multi-candidate Studio;
- the reusable content model through Blueprints;
- testing from individual Draft Runs to a Scenario Lab;
- release from separate Commit/Publish actions to a governed Release Center;
- the post-release experience through a Run Center;
- collaboration, identity, persistence, environment, and worker architecture.

v5 reuses the v4 safety core. It does not replace it with a more permissive
agent.

## 3. Status and `/goal` rules

Valid statuses:

| Status | Meaning |
| --- | --- |
| `pending` | Work has not started |
| `in_progress` | The selected `/goal` is actively being implemented |
| `completed` | Every task and acceptance criterion passed |
| `blocked` | Work cannot continue; evidence and required input are recorded |

Rules:

- Start one phase or milestone at a time with `/goal`.
- Verify dependencies before changing a phase to `in_progress`.
- Preserve v3 and v4 behavior behind the v5 default-off flag.
- Deliver vertical product slices; backend schema alone is not completion.
- Add deterministic tests and real UI acceptance with every slice.
- Check a task only after its behavior and negative paths pass.
- Run targeted tests first, then the full supported suite.
- Update evidence, decisions, limitations, and screenshots/fixtures where
  visual behavior changes.
- Do not start a later phase opportunistically.

## 4. Roadmap

| Phase | Product milestone | Dependencies | Status |
| --- | --- | --- | --- |
| Phase 0 | Studio shell, Home, projects, identity, and migration foundation | v4.0.0 completed | `completed` |
| Phase 1 | Build Studio and safe candidate variants | Phase 0 | `pending` |
| Phase 2 | Blueprint Gallery and guided pattern reuse | Phase 1 | `pending` |
| Phase 3 | Scenario Lab and isolated candidate Preview | Phase 2 | `pending` |
| Phase 4 | Collaborative Review and Release Center | Phase 3 | `pending` |
| Phase 5 | Run Center, repair proposals, and safe automation | Phase 4 | `pending` |
| Release Gate | v5.0.0 product, safety, migration, and real user journeys | Phases 0–5 | `pending` |

```text
Phase 0: Studio Home
  → Phase 1: Build Studio
  → Phase 2: Blueprint Gallery
  → Phase 3: Scenario Lab
  → Phase 4: Review & Release Center
  → Phase 5: Run Center
  → Release Gate
```

## 5. Copyable `/goal` commands

Longer conversation-ready prompts are in
[`docs/goals/v5.0.0-goal-prompts.md`](goals/v5.0.0-goal-prompts.md).

### Phase 0

```text
/goal 实施 docs/tasks.md 的 v5 Phase 0：Studio Shell、Studio Home、Project、可信身份与迁移地基。完成全部 P0 任务和产品验收，保持 v3/v4 行为不变，更新 docs/tasks.md 状态、测试和真实 UI 证据；不要开始 Phase 1。
```

### Phase 1

```text
/goal 实施 docs/tasks.md 的 v5 Phase 1：Build Studio 与安全候选方案。完成多候选、上下文共建、方案对比、节点能力覆盖、配置型应用创建和产品验收，更新 docs/tasks.md；不要开始 Phase 2。
```

### Phase 2

```text
/goal 实施 docs/tasks.md 的 v5 Phase 2：Blueprint Gallery 与引导式复用。完成 Blueprint 浏览、配置、预览、应用、版本、团队共享和首批模板验收，更新 docs/tasks.md；不要开始 Phase 3。
```

### Phase 3

```text
/goal 实施 docs/tasks.md 的 v5 Phase 3：Scenario Lab 与隔离候选 Preview。完成场景数据集、多候选对比、质量/时延/用量/成本指标、回归门槛、Preview Receipt/Cleanup 和真实 UI 验收，更新 docs/tasks.md；不要开始 Phase 4。
```

### Phase 4

```text
/goal 实施 docs/tasks.md 的 v5 Phase 4：协作 Review 与 Release Center。完成评论、审批策略、Artifact、环境映射、漂移、Apply Draft、显式 Publish、Release Note、Rollback 和产品验收，更新 docs/tasks.md；不要开始 Phase 5。
```

### Phase 5

```text
/goal 实施 docs/tasks.md 的 v5 Phase 5：Run Center、Repair Proposal 与安全自动化。完成版本关联执行、错误聚类、修复建议、告警、持久化 Worker、受限 MCP/API 和产品验收，更新 docs/tasks.md；不要开始 Release Gate。
```

### Release Gate

```text
/goal 执行 docs/tasks.md 的 v5.0.0 Release Gate。只做发布核验、阻断缺陷修复、迁移/回滚、文档和版本一致性；完成全部产品旅程、可用性、安全、质量、兼容和真实环境验收，更新 docs/tasks.md，不新增未批准范围。
```

## 6. Phase 0 — Studio shell, Home, projects, identity, and migration

Status: `completed`

Dependencies: archived v4.0.0 Release Gate `completed`

Outcome: a signed-in user enters a real v5 Studio, sees project-scoped Dify
applications and current work, can return to the v4 Workbench when v5 is off,
and the new persistence/identity foundation is safe for later product phases.

### Tasks

- [x] **P0-01 — Product information architecture**
  - Define Studio Home, Build, Blueprints, Scenarios, Reviews/Releases, and
    Runs navigation.
  - Define empty, loading, partial, permission-denied, offline, and error
    states.
  - Define responsive Dify-hosted drawer/full-page behavior.
  - Record usability tasks and success metrics before implementation.

- [x] **P0-02 — v5 flag and Studio API**
  - Add `CHAT2DIFY_AI_STUDIO_V5_ENABLED`, default `false`.
  - Add `/api/v5/studio` with a typed public error envelope.
  - Do not start v5 workers or replace v4 UI while the flag is off.
  - Keep v3 and `/api/v4/agent` behavior unchanged.

- [x] **P0-03 — Trusted identity bridge**
  - Add a server-verifiable Principal for the Dify-hosted Studio.
  - Validate signature, issuer, audience, expiry, replay, origin, and nonce.
  - Never use browser-claimed user, role, project, app, or environment as
    authorization.

- [x] **P0-04 — Projects and membership foundation**
  - Add Project, Membership, owner/admin/builder/reviewer/viewer roles.
  - Scope every v5 object and read to a Project.
  - Add a low-friction personal project for single-user migration.
  - Deny cross-project reads before returning any data.

- [x] **P0-05 — Studio Home aggregation**
  - Show recent Dify apps, v4 Runs that can be resumed, active Studio drafts,
    reviews assigned to the user, latest releases, quality regressions, and
    incidents as data becomes available.
  - Phase 0 may render later categories as truthful empty states, not fake
    metrics.
  - Add search, app mode filters, and “continue building” entry.

- [x] **P0-06 — Product persistence**
  - Support SQLite for local/single-user use and PostgreSQL for team use.
  - Add additive migrations, Project scope, append-only product activity, job
    lease/outbox/receipt primitives, and optimistic versions.
  - Do not destructively rewrite `workflow_tasks` or `agent_*`.

- [x] **P0-07 — v4 continuity and import**
  - Link eligible v4 Sessions/Runs into a personal project without copying
    secrets.
  - Preserve direct v4 URLs and flag-off rollback.
  - Explain unsupported or incomplete v4 work instead of silently dropping it.

- [x] **P0-08 — Studio shell and Home tests**
  - Cover identity forgery/replay, cross-project access, migration,
    PostgreSQL/SQLite contracts, navigation, empty/error states, v4 deep links,
    flag off, accessibility, and signed-in Dify-host rendering.

### Acceptance scenario

> A current v4 user enables v5, enters Studio Home inside Dify, sees their
> existing applications and resumable work in a personal project, opens one
> app, then disables v5 and returns to the unchanged v4 experience.

### Acceptance criteria

- [x] Studio Home is usable in the real Dify host, not only a test page.
- [x] Browser-supplied identity never authorizes access.
- [x] Cross-project data does not appear in API, UI, event, or activity feeds.
- [x] A populated v4 SQLite database migrates without loss.
- [x] Flag off restores the v4 product path without schema downgrade.
- [x] Accessibility smoke and the fixed Phase 0 usability tasks pass.
- [x] Targeted tests, full supported suite, and `git diff --check` pass.

### Implementation record

- Started: 2026-07-30
- Completed: 2026-07-30
- Fixed usability tasks:
  1. With v5 enabled and a valid signed Dify-host token, open Studio Home,
     identify the active personal project, find an existing app by search or
     app-mode filter, and open its existing v4 Build entry without copying an
     ID.
  2. Resume an eligible paused/interrupted v4 Run from its Home card; when a
     Run is not resumable, show a stable business-readable reason and a safe
     alternative.
  3. Verify truthful loading, empty, partial/error, permission-denied, and
     offline states with keyboard focus preserved and a retry or recovery
     action where one is valid.
  4. Disable v5, reload the same Dify-host entry, and observe the unchanged v4
     Workbench without deleting or downgrading v5 data.
- Phase 0 usability targets:
  - the fixed “find an app and continue building” path requires at most three
    primary interactions after Studio Home loads;
  - every interactive Home control is keyboard reachable and has an
    accessible name;
  - no state claims data that was not authoritatively loaded;
  - cross-project, invalid-token, and disabled-feature paths reveal no Studio
    data.
- Delivered product slice:
  - `CHAT2DIFY_AI_STUDIO_V5_ENABLED` defaults off; enabling it adds the
    Studio Shell/Home and `/api/v5/studio`, while flag off starts no v5
    service and leaves v3/v4 schemas and endpoints intact.
  - Dify account, Workspace, applications, CSRF, cookie refresh, short-lived
    Studio token, one-time nonce, origin, issuer/audience, JTI, membership,
    and tenant are verified server-side. Only allowlisted Dify auth cookies
    cross the internal verification call and cookie values are not persisted.
  - Personal Projects, Membership roles, project app/v4 links, append-only
    Activity, optimistic Project versions, durable Job/Outbox leases, and
    external Receipts run on additive SQLite or PostgreSQL repositories.
  - Home shows verified Dify apps, project-linked resumable v4 work,
    search/app-mode filters, business-readable reasons, and truthful empty
    states for later v5 surfaces.
  - Home-to-Build links preserve a validated context nonce but explicitly use
    the Dify-persisted Draft instead of inventing canvas context. True
    canvas-opened v4 links retain origin/source/nonce and dirty/Hash checks.
  - When Dify omits `app_mode` on the create entry, v4 resolves it to Workflow;
    with v5 off and v4 on, the same real host entry opens the v4 Builder.
- Real product and visual evidence:
  - Dify `1.14.2`, signed-in in-app Browser, `1280 × 720` viewport: opened
    “Chat2Dify 创建”, received “Dify 已验证”, the personal Project, complete
    Studio navigation, truthful application/work empty states, and the
    `720 × 696` hosted drawer fully inside the viewport.
  - Imported one unpublished temporary Workflow, refreshed Home, found it by
    text and Workflow filter, opened its verified card, observed the
    Dify-persisted-Draft context and an enabled Builder composer, then returned
    to Home without copying an ID.
  - Deleted the temporary Workflow and independently verified Dify returned
    `404`; Home refresh removed the card. No test application was published or
    left behind.
  - Recreated the local service with v5 off/v4 on and reopened the same Dify
    entry into “新建工作流”; restored the final local stack to v5 enabled.
  - Accessibility DOM smoke found 10 visible interactive Home controls,
    including the skip link; all had accessible names, `tabIndex >= 0`, focus
    styling, responsive breakpoints, and reduced-motion rules.
- Security, migration, and persistence evidence:
  - Forged token/browser claims, nonce replay, wrong origin, Dify account
    change, cross-project reads, invalid membership, stale optimistic version,
    hidden v4 sessions, and disabled-feature data access are covered by
    deterministic negative tests.
  - A populated v4 SQLite database retained its existing session/run rows and
    gained only `studio_*` tables; the same file remained usable when v5 was
    disabled.
  - A disposable PostgreSQL `15-alpine` instance passed the Project,
    Membership, optimistic version, Job, Outbox, and Receipt repository
    contract, then was stopped and auto-removed.
- Verification:
  - `.venv/bin/python -m pytest -q tests/test_studio_v5.py
    tests/test_studio_store.py tests/test_config_and_version.py
    tests/test_main.py` → `125 passed, 1 skipped`.
  - `node --test tests/frontend/*.test.mjs` → `16 passed`.
  - `CHAT2DIFY_TEST_POSTGRES_URL=... .venv/bin/python -m pytest -q
    tests/test_studio_store.py::test_postgresql_repository_contract` →
    `1 passed`.
  - `CHAT2DIFY_LIVE_DIFY_ACCEPTANCE=1 .venv/bin/python -m pytest -q
    tests/test_studio_v5_live.py` → `1 passed` against localhost Dify.
  - `.venv/bin/python -m pytest -q` → `478 passed, 14 skipped`; skipped tests
    are explicit live/provider/PostgreSQL opt-ins, with the v5 Dify and
    PostgreSQL cases run separately above.
  - `git diff --check` → passed.
- Decisions:
  - Use one `app/studio/store.py` repository boundary for both dialects to
    keep transactions and SQL translation consistent; small public
    Project/Job/Receipt modules re-export the typed domain surface.
  - Revalidate Dify on every authenticated v5 request. Dify `1.14.2` requires
    `X-CSRF-Token` even for protected GETs; a single bounded refresh forwards
    only Dify-issued auth/CSRF `Set-Cookie` headers back to the same-origin
    browser.
  - Treat Studio Home as an authorized discovery surface, not a fake canvas.
    Build reads the persisted Dify Draft server-side; canvas dirty/Hash
    assertions remain mandatory only when Dify actually supplied canvas
    context.
- Remaining Phase 0 limitations:
  - Reviews, releases, quality regressions, incidents, Blueprints, Scenarios,
    and Runs are intentionally truthful empty states until their selected
    phases.
  - Team Project administration UI and long-running multi-worker stress are
    later-phase work; Phase 0 provides the authorization and dual-dialect
    persistence foundation only.
  - `/private/tmp/chat2dify-studio-browser.sqlite3` is a 160 KB local test
    database created before a sandboxed dev server failed to bind. No request
    reached that server; exact-file removal was requested but the managed
    approval channel rejected the operation, so the OS-temporary file remains.
  - No repository commit, push, image publish, or pull request was created.

## 7. Phase 1 — Build Studio and safe candidate variants

Status: `pending`

Dependencies: Phase 0 `completed`

Outcome: users can co-build all supported Dify application types in one Studio,
ask for alternatives, compare candidate versions, and choose a validated
candidate without changing Dify.

### Tasks

- [ ] **P1-01 — Unified Build Studio**
  - Combine composer, Goal Plan, selected Dify context, node inspector,
    Timeline, business preview, validation, risk, and technical details.
  - Preserve v4 origin/nonce/dirty-state/Hash security.
  - Provide “explain first” and “show alternatives” modes.

- [ ] **P1-02 — Candidate variant model**
  - Fork two or three candidate Workspaces from the same base.
  - Give each candidate a business summary, assumptions, changed path, risk,
    validation state, and provenance.
  - Keep every edit as normal typed Patch history.
  - Never merge candidates through raw graph replacement.

- [ ] **P1-03 — Candidate comparison**
  - Compare business behavior, nodes/edges, model/resources, side effects,
    estimated cost inputs, validation, and unresolved questions.
  - Let users select one candidate or ask the Agent to synthesize a new one
    through explicit Patch operations.

- [ ] **P1-04 — Dify-native contextual commands**
  - Explain selected nodes and variable flow.
  - Propose safer fallback/error paths.
  - Generate scenarios for a branch.
  - Suggest compatible models/datasets/tools from the pinned catalog.
  - Keep external metadata untrusted and sanitized.

- [ ] **P1-05 — Config app creation**
  - Add v5 creation for Chatbot, Completion, and Dify Agent.
  - Reuse typed Config Patch, validation, review, and exact approval.
  - Do not fall back to model-authored arbitrary config dictionaries.

- [ ] **P1-06 — Typed capability coverage**
  - Add versioned mutation definitions for the node families already supported
    by the v3 Plan IR.
  - Add explicit, guarded `node.remove` and only the minimum additional typed
    operations needed for supported containers/triggers.
  - Continue to forbid arbitrary JSON Pointer and raw DSL.

- [ ] **P1-07 — Layout and preview**
  - Generate non-destructive candidate layout previews.
  - Preserve unrelated positions and container metadata.
  - Allow “focus changed path” and “fit candidate” without treating browser
    graph state as authoritative.

- [ ] **P1-08 — Build Studio tests**
  - Cover candidate fork/isolation, comparison, synthesis, selected context,
    configured-app creation, node coverage, removal preconditions, layout,
    invalid candidate, cancellation, restart, prompt injection, and no Dify
    write.

### Acceptance scenario

> “为当前售后 Chatflow 提供两个低置信度兜底方案：人工接管和二次追问。”
> Build Studio creates two isolated valid candidates, explains tradeoffs, and
> lets the user choose one without changing Dify.

### Acceptance criteria

- [ ] Candidates share a base but cannot mutate one another.
- [ ] Every candidate is reconstructable from typed Workspace history.
- [ ] Comparison is understandable without opening raw Plan/DSL.
- [ ] All claimed node types have deterministic compile/decompile/validation
      and negative coverage.
- [ ] No Dify write occurs in Phase 1.
- [ ] Fixed usability users reach a valid first candidate in median
      `< 3 minutes`.
- [ ] Targeted tests, full supported suite, and `git diff --check` pass.

## 8. Phase 2 — Blueprint Gallery and guided reuse

Status: `pending`

Dependencies: Phase 1 `completed`

Outcome: users discover, configure, preview, apply, save, and share proven
Dify-native patterns from a productized Gallery.

### Tasks

- [ ] **P2-01 — Blueprint product model**
  - Define business outcome, preview diagram, supported modes/versions, setup
    schema, capabilities, resource references, estimated cost/risk, validators,
    scenarios, provenance, version, deprecation, and upgrade notes.
  - Treat Blueprint content as untrusted data and never as permission.

- [ ] **P2-02 — Gallery discovery**
  - Add category, use case, app mode, Dify version, resource availability,
    risk, and team/private filters.
  - Rank only compatible Blueprints by default.
  - Explain unavailable requirements before application.

- [ ] **P2-03 — Guided setup**
  - Render typed setup forms for models, datasets, tools, triggers, prompts,
    variables, and policy options.
  - Show availability without secret values.
  - Validate and preview before creating a Patch.

- [ ] **P2-04 — Safe Blueprint application**
  - Expand one Blueprint into one normal transactional Patch.
  - Resolve temporary references and final IDs server-side.
  - Run normal validation, Diff, risk, policy, and candidate comparison.
  - Prove failure leaves the Workspace head unchanged.

- [ ] **P2-05 — Save selected pattern**
  - Let an authorized user extract selected nodes as a private/team Blueprint.
  - Require an explicit typed interface and remove environment-specific IDs or
    secret values.
  - Show a generated preview and compatibility report.

- [ ] **P2-06 — Versioning and upgrades**
  - Publish a new Blueprint version through review.
  - Show installed/source version and upgrade Diff.
  - Never auto-upgrade an application.

- [ ] **P2-07 — Initial Gallery**
  - Knowledge retrieval with grounded answer.
  - Human fallback.
  - Structured JSON extraction.
  - Document intake.
  - Webhook ingestion.
  - Scheduled report.
  - Error handling and retry.
  - Model fallback/routing.
  - Customer-support classification.

- [ ] **P2-08 — Gallery tests and usability**
  - Cover search, compatibility, setup validation, malicious metadata,
    resource availability, extraction secret scan, version upgrade, Patch
    invariants, unrelated preservation, accessibility, and real Workbench use.

### Acceptance scenario

> A business builder chooses “knowledge retrieval with human fallback,” maps a
> staging dataset and review channel through a guided form, previews the
> resulting graph and expected behavior, and applies it as one valid candidate.

### Acceptance criteria

- [ ] A user can apply an initial Blueprint without editing raw JSON.
- [ ] Blueprint application cannot expand Tool visibility or permissions.
- [ ] Failed application does not change the candidate.
- [ ] Extracted Blueprints contain no environment-specific secret values.
- [ ] Initial Blueprint application success is `>= 95%` on the fixed set.
- [ ] Unrelated graph preservation is `>= 99%`.
- [ ] Targeted tests, full supported suite, and `git diff --check` pass.

## 9. Phase 3 — Scenario Lab and isolated candidate Preview

Status: `pending`

Dependencies: Phase 2 `completed`

Outcome: users create business scenarios, run real uncommitted candidates in a
safe Preview target, compare candidates, and define regression gates.

### Tasks

- [ ] **P3-01 — Scenario and dataset product model**
  - Add manual, generated, fixture, and explicitly approved sanitized-run
    sources.
  - Support expected output, invariants, rubric, tags, owner, retention, and
    version.
  - Mark all dataset content untrusted.

- [ ] **P3-02 — Scenario authoring**
  - Generate edge cases only after deterministic input schema discovery.
  - Add business-readable expected behavior and invariant editors.
  - Require user files or approved fixtures for file inputs.

- [ ] **P3-03 — Isolated Preview Environment**
  - Use an explicitly configured non-production Dify target.
  - Make production credentials and secret mappings structurally unavailable.
  - Persist intent/receipt before follow-up actions.
  - Label every temporary app with project, candidate, and TTL.

- [ ] **P3-04 — Cleanup and reconciliation**
  - Add idempotent cleanup, independent absence verification, orphan reaper,
    and operator reconciliation.
  - Never blind-retry an ambiguous import.
  - Keep the normal Dify `1.14.x` adapter fail-closed when no Preview target is
    configured.

- [ ] **P3-05 — Candidate runs**
  - Run one or more candidates over selected scenarios.
  - Normalize output, failed node, latency, model usage, estimated cost, and
    side effects.
  - Store only sanitized evidence.

- [ ] **P3-06 — Side-by-side comparison**
  - Compare quality, pass rate, regressions, latency, usage/cost, side effects,
    and failure clusters.
  - Explain metric limitations and missing evidence.
  - Allow one report to become the candidate baseline.

- [ ] **P3-07 — Regression gates**
  - Define project/app thresholds and required scenario suites.
  - Bind evidence to exact candidate, resource mapping, suite, policy, and
    expiry.
  - Invalidate stale evidence after any bound input changes.

- [ ] **P3-08 — Scenario Lab tests and usability**
  - Cover dataset injection, secret scan, file boundary, restricted mappings,
    side-effect approval, budget, timeout, cancellation, ambiguity, cleanup,
    metric determinism, stale evidence, accessibility, and real Preview use.

### Acceptance scenario

> Run both fallback candidates against an after-sales regression suite. Compare
> resolution quality, response latency, model usage, human escalations, and
> cost estimate; save the chosen result as the release baseline; verify the
> temporary Preview apps are gone.

### Acceptance criteria

- [ ] Candidate execution never targets production.
- [ ] Preview cannot resolve production secret mappings.
- [ ] Every Preview write has a receipt and cleanup state.
- [ ] Ambiguous import requires reconciliation, not re-import.
- [ ] Comparison evidence is exact and reproducible for fixed fixtures.
- [ ] Scenario Lab fixed goal completion is `>= 90%`.
- [ ] Preview fixture cleanup is independently verified.
- [ ] Targeted tests, real Preview acceptance, full suite, and
      `git diff --check` pass.

## 10. Phase 4 — Collaborative Review and Release Center

Status: `pending`

Dependencies: Phase 3 `completed`

Outcome: teams can discuss and approve the exact tested candidate, apply it to
a Dify draft, explicitly publish when authorized, move it between
environments, and roll back through one coherent Release Center.

### Tasks

- [ ] **P4-01 — Change Request and review**
  - Add author, reviewers, comments, assignment, request changes, approve,
    reject, supersede, expiry, and activity.
  - Bind every decision to the exact candidate and Scenario evidence.
  - Support policy-required author/reviewer separation.

- [ ] **P4-02 — Immutable Workflow Artifact**
  - Create a canonical, secrets-free Artifact from the approved candidate.
  - Include Plan/config, compatibility, capability/resource requirements,
    Scenario suite/report references, provenance, and content Hash.

- [ ] **P4-03 — Environments and mappings**
  - Add logical app identity and optional development/staging/production
    environments.
  - Map Dify app IDs, models, datasets, tools, strategies, triggers, and
    credential availability through opaque references.
  - Keep a one-environment setup simple for individual users.

- [ ] **P4-04 — Drift and release preview**
  - Re-read target state and Hash.
  - Show deployed base, target drift, proposed Artifact, mappings, quality
    evidence, risk, and release notes.
  - Block unsupported compatibility, unresolved drift, or stale evidence.

- [ ] **P4-05 — Apply Draft**
  - Bind approval to Artifact, environment, mappings, policy evidence, and
    target base Hash.
  - Reuse the safe Commit service; keep it outside model-visible Tools.
  - Store receipts and make duplicates idempotent.

- [ ] **P4-06 — Explicit Publish**
  - Keep Publish separate from Apply Draft.
  - Require a distinct high-risk user action and current draft Hash.
  - Never let the Builder, MCP, or a background repair proposal publish
    autonomously.

- [ ] **P4-07 — Release history and rollback**
  - Show Artifact, actor, evidence, notes, receipt, Dify Hash, and environment.
  - Roll back by proposing and approving an earlier Artifact against current
    state.
  - Never unconditionally overwrite later Dify changes.

- [ ] **P4-08 — Optional Git serialization**
  - Serialize deterministic, reviewable, secrets-free Artifact files.
  - Treat pulled content as untrusted and route it through a Change Request.
  - Keep push/pull explicit; do not auto-push, merge, or release.

- [ ] **P4-09 — Release Center tests and usability**
  - Cover permissions, self-approval, comments, stale decision, secret scan,
    canonical bytes, mapping mismatch, drift, duplicate, ambiguous outcome,
    Apply/Publish separation, rollback, Git conflict, accessibility, and real
    Dify-host use.

### Acceptance scenario

> A reviewer requests one change, approves the corrected and tested candidate,
> then an authorized releaser applies the exact Artifact to staging. After
> verification, Publish requires a separate explicit action. A later rollback
> is a new reviewed release and preserves external drift.

### Acceptance criteria

- [ ] Approval for candidate/release A cannot authorize B.
- [ ] Comments and decisions are project-scoped and durable.
- [ ] Artifact/Git output contains no secrets.
- [ ] Drift or stale Hash blocks Apply/Publish.
- [ ] Apply Draft and Publish are distinct approvals/actions.
- [ ] Duplicate execution does not duplicate Dify/Git writes.
- [ ] Users can finish review/release without reading raw DSL.
- [ ] Targeted tests, real Dify/Git acceptance, full suite, and
      `git diff --check` pass.

## 11. Phase 5 — Run Center, repair proposals, and safe automation

Status: `pending`

Dependencies: Phase 4 `completed`

Outcome: users understand production behavior by released version, turn
failures into safe repair proposals, and integrate read/propose/evaluate
workflows without giving external agents release authority.

### Tasks

- [ ] **P5-01 — Execution/version correlation**
  - Correlate supported Dify executions to Project, logical app, Environment,
    Artifact, and release receipt.
  - Normalize status, failed node, stable error class, latency, model
    usage/cost summary, and sanitized input/output shapes.

- [ ] **P5-02 — Run Center dashboard**
  - Show success/error trend, releases, regressions, error clusters, slow/costly
    paths, and missing evidence.
  - Filter by app, environment, Artifact, date, status, and error class.
  - Never render raw secrets or chain-of-thought.

- [ ] **P5-03 — Incident detail**
  - Link execution evidence, released Diff, Scenario coverage, known errors,
    and affected nodes.
  - Provide a stable business-readable cause and next step.

- [ ] **P5-04 — Create repair proposal**
  - Turn selected sanitized evidence into a pre-filled Change Request.
  - Let the Builder inspect and propose typed candidate fixes.
  - Require Scenario regression, review, Apply, and Publish through the normal
    product flow.
  - Never auto-modify or auto-publish production.

- [ ] **P5-05 — Alerts and scheduled checks**
  - Add adapter-based notifications for error/quality thresholds.
  - Add scheduled Scenario regression for released Artifacts.
  - Require explicit project configuration, outbox, redaction, and idempotency.

- [ ] **P5-06 — Durable product jobs**
  - Move v5 Build, Preview, Scenario, Release, notification, and cleanup work
    to lease/outbox workers.
  - Add heartbeat, cancellation, bounded retry, graceful shutdown,
    dead-letter/reconciliation, and operation-specific receipts.
  - Prove restart cannot duplicate external writes.

- [ ] **P5-07 — Safe MCP/API/CI**
  - Authenticate with OAuth or scoped tokens.
  - Expose search/inspect, Change Request creation, typed proposal, Scenario
    run/read, review read, and release preview.
  - Do not expose approval decision, Apply Draft, Publish, credential
    plaintext, raw DSL, or arbitrary Patch.

- [ ] **P5-08 — Run Center tests and usability**
  - Cover correlation, redaction, error clustering, repair linkage, alert
    deduplication, worker loss, lease expiry, outbox recovery, MCP scope
    escalation, token rotation/revocation, cross-project search, rate limits,
    accessibility, and real incident-to-repair use.

### Acceptance scenario

> Run Center groups a production variable-reference failure under the released
> Artifact, explains the affected path, and creates a repair proposal. The
> proposal passes Scenario regression and normal review/release. A scoped MCP
> client can inspect the result but cannot approve, Apply, Publish, or read
> secrets.

### Acceptance criteria

- [ ] Supported executions are correlated to the exact released Artifact.
- [ ] Designated failures become reviewable repair proposals at `>= 80%`.
- [ ] No repair path silently modifies or publishes production.
- [ ] Worker restart does not duplicate external writes.
- [ ] MCP cannot cross projects or expand permission through content.
- [ ] Every failed user action has a business-readable reason and next step.
- [ ] Targeted tests, multi-worker PostgreSQL tests, real MCP acceptance, full
      suite, and `git diff --check` pass.

## 12. v5.0.0 Release Gate

Status: `pending`

Dependencies: Phases 0–5 `completed`

The Release Gate verifies and fixes selected scope only.

### Product journeys

- [ ] Studio Home → Build Studio deep link works in the signed-in Dify host.
- [ ] New and existing Workflow/Chatflow journeys pass.
- [ ] New and existing Chatbot/Completion/Agent journeys pass.
- [ ] Multi-candidate comparison and selection pass.
- [ ] Initial Blueprint discovery/setup/apply/upgrade pass.
- [ ] Scenario authoring, candidate comparison, baseline, and regression pass.
- [ ] Review comments, request changes, approval, Apply, Publish, and rollback
      pass.
- [ ] Run incident → repair proposal → Scenario → Review → Release passes.
- [ ] Flag off restores the unchanged v4 journey.

### Usability and accessibility

- [ ] Median goal-to-valid-candidate time is `< 3 minutes` on the fixed set.
- [ ] `>= 80%` of fixed review/release tasks finish without raw technical
      views.
- [ ] Every surface has truthful empty/loading/error/permission/offline states.
- [ ] Keyboard navigation, focus, labels, contrast, and screen-reader smoke
      pass.
- [ ] Responsive drawer and full-page layouts pass.

### Safety and isolation

- [ ] Unauthorized cross-project reads/writes are `0`.
- [ ] Model, Blueprint, MCP, and worker cannot approve or publish.
- [ ] Unapproved Dify/Git/notification writes are `0`.
- [ ] Incorrect conflict overwrites are `0`.
- [ ] Secret values in UI evidence, Artifact, Git, Trace, model context,
      Scenario report, notification, activity, audit, or MCP output are `0`.
- [ ] Preview cannot resolve production secret mappings.
- [ ] Ambiguous Preview import is never blindly retried.

### Quality

- [ ] Supported candidate validity before review is `100%`.
- [ ] Unrelated workflow preservation is `>= 99%`.
- [ ] Initial Blueprint application success is `>= 95%`.
- [ ] Fixed Scenario Lab goal completion is `>= 90%`.
- [ ] Designated incident-to-repair success is `>= 80%`.
- [ ] Preview fixture cleanup is independently verified at `100%`.

### Persistence, compatibility, and operations

- [ ] Populated v4 SQLite → v5 migration passes without data loss.
- [ ] PostgreSQL migration, concurrency, outbox, lease, and recovery pass.
- [ ] At-least-once jobs do not duplicate external writes.
- [ ] Every failed external job has a receipt state and operator next step.
- [ ] Full v3/v4 suites pass.
- [ ] Real acceptance passes for every claimed Dify/DSL pair.
- [ ] Real Preview, Git, MCP, and Dify-hosted Studio journeys pass.
- [ ] Version metadata and docs consistently report `5.0.0`.
- [ ] Rollout/rollback and `git diff --check` pass.

### Release completion record

- Started:
- Completed:
- Product usability:
- Accessibility:
- Targeted tests:
- Full suite:
- SQLite migration:
- PostgreSQL:
- Real Dify:
- Preview and cleanup:
- Git:
- MCP:
- Signed-in Studio:
- Quality metrics:
- Known limitations:
- Rollback:

## 13. Explicitly deferred

- [ ] Standalone Chat2Dify workflow runtime.
- [ ] Chat2Dify-owned connector marketplace.
- [ ] Autonomous production Publish.
- [ ] Credential creation or plaintext access.
- [ ] Candidate tests with production credentials.
- [ ] Arbitrary JSON Patch or raw DSL mutation.
- [ ] Runtime multi-agent orchestration.
- [ ] Automatic sub-workflow decomposition.
- [ ] Automatic Git push/merge or environment promotion.
- [ ] Vector-database replacement for structured product data.

## 14. Decision log

| Date | Phase | Decision | Reason | Files |
| --- | --- | --- | --- | --- |
| 2026-07-30 | Planning | Select `v5.0.0`, not `v4.0.1` | The roadmap changes the primary product journey, collaboration, testing, release, operations, persistence, and deployment | v5 architecture, `AGENTS.md`, `docs/tasks.md` |
| 2026-07-30 | Planning | Position v5 as **AI Workflow Studio** rather than an infrastructure-first control plane | The requested upgrade should be visible to builders, reviewers, and operators through coherent product surfaces | v5 architecture and roadmap |
| 2026-07-30 | Planning | Compete through safe Dify-native co-building, not connector count | n8n is stronger in generic integrations; Chat2Dify's advantage is contextual Dify building with typed transactions, deterministic proof, and exact approval | v5 architecture |
| 2026-07-30 | Planning | Preserve one Builder Agent and the v4 safety core | Candidate variants and repair proposals must not weaken Patch, validation, approval, Hash, redaction, or no-replay boundaries | `AGENTS.md` |
| 2026-07-30 | Planning | Make Blueprints compile-time product patterns | They can provide reusable outcomes and guided setup while still expanding through normal Patch IR | Phase 2 |
| 2026-07-30 | Planning | Model real candidate testing as an isolated Preview product | Dify 1.14.2 ignores candidate Graph inputs and upstream import is not idempotent; receipts and cleanup must be first-class | Phase 3 |
| 2026-07-30 | Planning | Keep explicit Publish in Release Center but outside the Builder loop | Product coherence should not become autonomous high-risk production release | Phase 4 |
| 2026-07-30 | Planning | Keep approval/apply/publish unavailable to MCP | External automation may inspect, propose, and evaluate but cannot bypass human product gates | Phase 5 |
