# Chat2Dify Builder Agent v4 operator guide

This guide covers configuration, retention, approvals, side effects, recovery,
troubleshooting, compatibility, and extension workflows for the v4 Builder
Agent. The architecture remains defined by
[`architecture/v4-agent-architecture-and-implementation-plan.md`](architecture/v4-agent-architecture-and-implementation-plan.md);
the implementation ledger is [`tasks.md`](tasks.md).
For installation and end-user walkthroughs, see
[`v4-user-guide.md`](v4-user-guide.md).

## Enablement and configuration

The v4 API is disabled by default:

```env
CHAT2DIFY_AGENT_V4_ENABLED=false
```

Set it to `true` only after the existing v3 configuration is healthy. The
Builder Agent reuses:

- `CHAT2DIFY_TASK_DB` for separately named `agent_*` SQLite tables;
- `CHAT2DIFY_TASK_WORKERS` for bounded background dispatch;
- the configured Planner provider and fallback list for decisions;
- the Dify Console connection and credentials for authoritative reads and
  approved writes;
- the Dify source checkout for its reported DSL version.

The flag changes only the v4 route and store/runtime initialization. It does
not remove or redirect the v3 assistant, direct workflow, configured-app,
Draft Run, or publish APIs.

## Application scope

| Application mode | v4 create | v4 modify | Patch domain | Fallback |
| --- | --- | --- | --- | --- |
| `workflow` | Supported | Supported | Graph `PatchDocument` | v3 remains available |
| `advanced-chat` | Supported | Supported | Graph `PatchDocument` | v3 remains available |
| `chat` | v3 only | Supported | `ConfigPatchDocument` | v3 configured-app path |
| `completion` | v3 only | Supported | `ConfigPatchDocument` | v3 configured-app path |
| `agent-chat` | v3 only | Supported | `ConfigPatchDocument` | v3 configured-app path |

Configured-app creation deliberately remains on v3 in v4.0.0. Existing
configured apps gain v4 inspect, Patch, validate, Diff, review, approval, and
Hash-checked Commit. Graph Patch and Config Patch types are not convertible or
interchangeable. The Dify configuration page exposes a Chat2Dify Builder bar
for these existing apps. It passes only the app identity and mode into the
Sidecar; unlike Workflow/Chatflow, it does not open a Canvas Context channel.

## Approval and Hash boundaries

The model can inspect, search Skills, patch only the server Workspace, validate,
and produce a review. It cannot call Commit or a Dify write Tool.

Commit approval is persisted with:

```text
run_id
workspace_version_id
base_hash
action
risk
expires_at
```

A new Workspace version invalidates prior Commit approval. Modification Commit
re-reads Dify immediately before writing:

- Workflow/Chatflow compares the Dify draft Hash.
- Chatbot/Completion/Agent compares the model-config `hash`, `updated_at`, or
  `version`. If Dify provides none, v4 uses a canonical SHA-256 fingerprint of
  the entire model config for change detection.

A mismatch returns `conflicted` without a write. High-risk changes, including
configured Agent tool bindings, require a separate destructive-change approval
before Commit approval.

Publishing is not part of the Builder Agent loop. Continue to use the explicit
v3 publish operation with its own current-draft Hash and user confirmation.

## Side effects and Draft Runs

Workflow/Chatflow Draft Runs classify local, model-cost, HTTP, Tool,
notification/human, and unknown behavior. Approval scope and test budgets are
persisted before execution. Restart never automatically replays an in-flight
Draft Run or Commit.

Configured-app v4 work is modification-only and does not expose a model-visible
Draft Run Tool. Use the existing explicit v3 Chatbot, Completion, or Agent
Draft Run endpoint after reviewing or committing the config change.

## Data retention and redaction

`agent_sessions`, `agent_runs`, `agent_events`, `agent_workspace_versions`, and
`agent_approvals` live in the same SQLite file as v3 `workflow_tasks`, but are
separate tables. SQLite WAL is enabled and writes use short transactions.

The v4.0.0 store retains Agent records until the database is archived or
removed by the operator; there is no automatic v4 pruning policy. Size
planning should include full `WorkflowPlan` or model-config snapshots per
Workspace version and append-only Events. Back up the SQLite database using a
WAL-aware SQLite backup while the service is running, or stop the service
before copying all database/WAL files.

Sensitive keys are redacted before Event persistence and public streaming.
Model context excludes raw credentials, environment-variable values,
authorization headers, cookies, raw SSE, and model chain-of-thought. Operators
should still protect the database as application data because business prompts,
sanitized traces, Plans, and config snapshots may be confidential.

## Recovery

After process restart:

1. active work becomes `interrupted`;
2. persisted Events and Workspace head remain readable;
3. `waiting_user` and `waiting_approval` remain durable pauses;
4. resume is explicit;
5. an in-flight side effect is not replayed;
6. Commit rechecks approval, Workspace version, and Dify Hash.

Exhausted decision-Provider attempts also become `interrupted` only when every
recorded failure is retryable (network, 408/425/429, or 5xx) and model-call
budget remains. Explicit Resume continues from the last accepted Tool
checkpoint. Authentication/request 4xx, decision-contract failures, and
budget exhaustion remain terminal failures.

Before Commit, Undo moves only the graph Workspace head to its parent and
invalidates approval. After Workflow/Chatflow Commit, Undo creates a new
compensating preview with a new approval. Configured-app compensating Undo is
not in the selected v4.0.0 scope; create a new inspected Config Patch or use the
preserved v3 configured-app preview path.

## Troubleshooting

| Stable code | Meaning | Operator action |
| --- | --- | --- |
| `AGENT_V4_DISABLED` | v4 flag is off | Enable the flag and restart only if rollout is intended |
| `DIFY_VERSION_MUTATION_UNSUPPORTED` | Matrix allows diagnostics but not writes | Keep v3 active; upgrade to a tested Dify/DSL pair |
| `WORKSPACE_VERSION_MISMATCH` | Patch targets a stale head | Inspect the current head and regenerate the Patch |
| `CONFIG_PATCH_PRECONDITION_FAILED` | A field changed since inspection | Re-inspect the field and review a new Patch |
| `APPROVAL_WORKSPACE_VERSION_MISMATCH` | Approval targets an old version | Request approval for the visible head |
| `DIFY_DRAFT_HASH_CONFLICT` | Workflow/Chatflow changed in Dify | Start a fresh Snapshot/Run |
| `DIFY_MODEL_CONFIG_HASH_CONFLICT` | Config app changed in Dify | Start a fresh configured-app Run |
| `COMMIT_REQUIRES_VALIDATED_HEAD` | Stored head is not valid | Repair and validate; never bypass the check |
| `DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED` | Dify cannot run candidate Graphs | Commit with approval, then start a new explicit test Run |
| `DRAFT_TEST_FILE_REQUIRED` | No real file/fixture exists | Provide an explicit user file or approved fixture |

When a Run fails, inspect the terminal `error.code`, its current Diff, recent
structured observations, and the persisted Event timeline. Do not copy raw
credentials or unredacted Dify responses into a recovery message.

## Compatibility

The machine-enforced matrix and fixture expectations are documented in
[`compatibility/dify-v4.md`](compatibility/dify-v4.md). Unknown combinations are
diagnostic-only. Compatibility is pinned to the Run Snapshot, and every pinned
Capability carries the same matrix decision to prevent mid-Run drift.

## Evaluation

The default suite is offline and reproducible:

```bash
python -m app.evals.runner \
  --output app/evals/reports/phase4-release.json
```

It loads versioned JSON cases from `app/evals/cases/`, uses deterministic
scenarios to drive the real `AgentRuntime`, Tool Registry, versioned Workspace,
Patch, validation, review, approval, and Draft Run services, and emits a sorted
machine-readable report. The grader reads the resulting Workspace, Run,
Approval, and Event evidence; it does not copy `expected_result` fields into
scores. The deterministic decision and execution adapters make no Provider,
Dify, or Commit call.

Localhost-only Dify acceptance is opt-in because it creates and deletes
isolated temporary apps:

```bash
CHAT2DIFY_LIVE_DIFY_ACCEPTANCE=1 \
python -m pytest -q \
  tests/test_agent_phase1a_live.py \
  tests/test_agent_phase2_live.py \
  tests/test_agent_release_live.py
```

The additional real-Provider case requires both the localhost Dify flag and
`CHAT2DIFY_LIVE_PROVIDER_ACCEPTANCE=1`. It sends a bounded, sanitized workflow
goal/context to the configured Provider, allows at most eight model calls,
stops at review, and performs no Dify Commit or publish. Enable it only after
the operator explicitly approves that external data transfer.

## Extending the Builder Agent

### Add a Node Definition

1. Add a typed definition to `app/agent/catalog.py`.
2. Declare supported app modes, config/output schema, side-effect class,
   examples, and version range.
3. Reuse output metadata from `app/node_outputs.py` where possible.
4. Add normalizer, compiler, validator, Graph overlay, and negative tests.
5. Add compatibility fixtures before enabling mutation for a new Dify shape.

### Add a Tool

1. Define strict Pydantic input and output models.
2. Register the Tool explicitly in `ToolRegistry`; never dynamically import an
   executor by model-provided name.
3. Choose `none`, `workspace`, `draft_run`, or `dify_write` side effect and the
   correct approval policy.
4. Validate input before policy/execution and output before trace persistence.
5. Return stable public error codes and sanitized observations.
6. Keep Dify writes outside model-visible Tools.

### Add a Skill

1. Add a `SkillDefinition` to `app/agent/skills.py`.
2. Declare applicable app modes, mode-specific required Tools, validation
   rules, common stable errors, examples, and security notes.
3. Ensure required Tools are already visible under server policy.
4. Add `skill.search` tests proving the Skill cannot expose or authorize a
   hidden Tool.
5. Add at least one fixed evaluation case.

Skills are server-owned guidance. They never register Tools, change Tool
side-effect metadata, approve actions, or add permissions.

### Add an evaluation case

1. Add a versioned JSON file under `app/evals/cases/`.
2. Fix the goal, Snapshot/version, allowed resources/capabilities, invariants,
   required/forbidden changes, budgets, side-effect policy, and expected
   validation.
3. Include a readable Event trace and a structured terminal reason for an
   expected failure.
4. Run the suite twice and confirm byte-stable report content.
5. Add new grader logic only when it is deterministic and does not weaken
   existing release thresholds.
