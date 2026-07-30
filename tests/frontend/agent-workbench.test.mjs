import assert from "node:assert/strict";
import test from "node:test";

import {
  CANVAS_CONTEXT_PROTOCOL,
  CanvasContextChannel,
  EventCursor,
  appModeLabel,
  approvalMatchesVisibleVersion,
  commitBlockReason,
  isAgentWorkbenchSupported,
  isContextNonce,
  parseSse,
  reviewDiffRows,
  resolveAgentAppMode,
  requiresCanvasContext,
  runControlState,
  supportsCanvasContext,
  testPresentation,
  timelinePresentation,
  undoPresentation,
} from "../../app/static/agent-workbench-core.mjs";

test("create deep links default to workflow when Dify omits app_mode", () => {
  assert.equal(resolveAgentAppMode("create", ""), "workflow");
  assert.equal(resolveAgentAppMode("create", null), "workflow");
  assert.equal(resolveAgentAppMode("modify", ""), "");
  assert.equal(resolveAgentAppMode("create", "advanced-chat"), "advanced-chat");
  assert.equal(isContextNonce("context-nonce-1234567890"), true);
  assert.equal(isContextNonce("bad nonce"), false);
});

test("Studio Home uses persisted Dify drafts without pretending to have canvas context", () => {
  assert.equal(requiresCanvasContext({
    appMode: "workflow",
    intent: "modify",
    embedded: true,
    studioEntry: "home",
  }), false);
  assert.equal(requiresCanvasContext({
    appMode: "workflow",
    intent: "modify",
    embedded: true,
    studioEntry: "canvas",
  }), true);
  assert.equal(requiresCanvasContext({
    appMode: "workflow",
    intent: "create",
    embedded: true,
    studioEntry: "canvas",
  }), false);
});

const nonce = "safe_context_nonce_123456789";
const parentWindow = {};

const contextMessage = (overrides = {}) => ({
  protocol: CANVAS_CONTEXT_PROTOCOL,
  type: "dify.context.init",
  context_nonce: nonce,
  payload: {
    protocol_version: "1.0",
    revision: 1,
    selected_node_ids: ["llm-1"],
    selected_edge_ids: ["edge-1"],
    viewport: { x: 10, y: 20, zoom: 1.2 },
    current_panel: "canvas",
    dirty_state: false,
    canvas_draft_hash: "hash-v0",
    ...overrides,
  },
});

test("Workbench supports graph creation and existing graph or config applications", () => {
  assert.equal(isAgentWorkbenchSupported({
    featureEnabled: true,
    intent: "create",
    appMode: "workflow",
  }), true);
  assert.equal(isAgentWorkbenchSupported({
    featureEnabled: true,
    intent: "modify",
    appMode: "advanced-chat",
    appId: "app-1",
  }), true);
  for (const appMode of ["chat", "completion", "agent-chat"]) {
    assert.equal(isAgentWorkbenchSupported({
      featureEnabled: true,
      intent: "modify",
      appMode,
      appId: "app-1",
    }), true);
    assert.equal(isAgentWorkbenchSupported({
      featureEnabled: true,
      intent: "create",
      appMode,
    }), false);
    assert.equal(supportsCanvasContext(appMode), false);
  }
  assert.equal(isAgentWorkbenchSupported({
    featureEnabled: false,
    intent: "modify",
    appMode: "chat",
    appId: "app-1",
  }), false);
  assert.equal(isAgentWorkbenchSupported({
    featureEnabled: true,
    intent: "modify",
    appMode: "chat",
  }), false);
  assert.equal(supportsCanvasContext("workflow"), true);
  assert.equal(appModeLabel("completion"), "文本生成应用");
});

test("canvas channel accepts valid selection and rejects origin, source, and nonce changes", () => {
  const channel = new CanvasContextChannel({
    expectedOrigin: "https://dify.example",
    nonce,
    sourceWindow: parentWindow,
  });
  const accepted = channel.accept({
    origin: "https://dify.example",
    source: parentWindow,
    data: contextMessage(),
  });
  assert.deepEqual(accepted.context.selected_node_ids, ["llm-1"]);
  assert.equal(channel.accept({
    origin: "https://evil.example",
    source: parentWindow,
    data: contextMessage({ revision: 2 }),
  }), null);
  assert.equal(channel.accept({
    origin: "https://dify.example",
    source: {},
    data: contextMessage({ revision: 2 }),
  }), null);
  const wrongNonce = contextMessage({ revision: 2 });
  wrongNonce.context_nonce = "wrong_nonce_123456789";
  assert.equal(channel.accept({
    origin: "https://dify.example",
    source: parentWindow,
    data: wrongNonce,
  }), null);
});

test("canvas channel rejects stale, malformed, and raw-graph context", () => {
  const channel = new CanvasContextChannel({
    expectedOrigin: "https://dify.example",
    nonce,
    sourceWindow: parentWindow,
  });
  const event = {
    origin: "https://dify.example",
    source: parentWindow,
    data: contextMessage(),
  };
  assert.ok(channel.accept(event));
  assert.equal(channel.accept(event), null);
  assert.equal(channel.accept({
    ...event,
    data: contextMessage({ revision: 2, raw_graph: { nodes: [] } }),
  }), null);
  assert.equal(channel.accept({
    ...event,
    data: contextMessage({ revision: 3, viewport: { x: 0, y: 0, zoom: 0 } }),
  }), null);
});

test("SSE replay and reconnect cursor deduplicate by Run and sequence", () => {
  const parsed = parseSse([
    "id: 1",
    "event: agent.started",
    'data: {"run_id":"run-1","seq":1,"type":"agent.started","phase":"observing","message":"Started"}',
    "",
    "id: 2",
    "event: validation.passed",
    'data: {"run_id":"run-1","seq":2,"type":"validation.passed","phase":"validating","message":"Passed"}',
    "",
  ].join("\n"));
  assert.equal(parsed.length, 2);
  const cursor = new EventCursor("run-1", 1);
  assert.equal(cursor.accept(parsed[0].data), true);
  assert.equal(cursor.accept(parsed[0].data), false);
  assert.equal(cursor.accept(parsed[1].data), true);
  assert.equal(cursor.sequence, 2);
  assert.equal(cursor.accept(parsed[0].data), false);
  assert.equal(cursor.accept({ ...parsed[1].data, run_id: "run-2", seq: 3 }), false);
});

test("timeline presentation keeps business outcome and failure tone stable", () => {
  assert.deepEqual(
    timelinePresentation({
      type: "validation.failed",
      phase: "validating",
      message: "变量引用无效",
    }),
    {
      type: "validation.failed",
      phase: "validating",
      message: "变量引用无效",
      tone: "danger",
    },
  );
});

test("visible approval and canvas safety bind Commit to the exact version and Hash", () => {
  const approval = {
    workspace_version_id: "v2",
    status: "approved",
  };
  assert.equal(approvalMatchesVisibleVersion(approval, "v2"), true);
  assert.equal(approvalMatchesVisibleVersion(approval, "v3"), false);
  assert.equal(commitBlockReason(
    { head_version_id: "v2", base_hash: "hash-v0" },
    { dirty_state: true, canvas_draft_hash: "hash-v0" },
  ), "Dify 画布仍有未同步变更。");
  assert.equal(commitBlockReason(
    { head_version_id: "v2", base_hash: "hash-v0" },
    { dirty_state: false },
  ), "Dify 画布尚未提供可验证的草稿 Hash。");
  assert.equal(commitBlockReason(
    { head_version_id: "v2", base_hash: "hash-v0" },
    { dirty_state: false, canvas_draft_hash: "hash-v1" },
  ), "Dify 画布 Hash 与本次 Run 的基准 Hash 不一致。");
});

test("Diff rows keep added, updated, and removed node and edge outcomes visible", () => {
  assert.deepEqual(
    reviewDiffRows({
      technical_diff: [
        { type: "node_added", message: "新增 LLM 节点。" },
        { type: "prompt_changed", message: "更新 LLM Prompt。" },
        { type: "node_removed", message: "删除旧节点。" },
        { type: "edge_added", message: "新增连线。" },
        { type: "edge_removed", message: "删除连线。" },
      ],
    }).map(change => change.type),
    [
      "node_added",
      "prompt_changed",
      "node_removed",
      "edge_added",
      "edge_removed",
    ],
  );
});

test("Resume affordances and Undo results preserve explicit Run boundaries", () => {
  assert.equal(runControlState({ phase: "paused", head_version_id: "v2" }).canResume, true);
  assert.equal(runControlState({ phase: "interrupted", head_version_id: "v2" }).canResume, true);
  assert.equal(
    runControlState({ phase: "waiting_user", head_version_id: "v2" }).resumesFromComposer,
    true,
  );
  const preCommit = undoPresentation({
    kind: "pre_commit",
    run: { id: "run-1", head_version_id: "v1" },
  });
  const postCommit = undoPresentation({
    kind: "post_commit",
    run: { id: "run-2", head_version_id: "compensating-v1" },
  });
  assert.equal(preCommit.run.id, "run-1");
  assert.match(preCommit.message, /Dify 未发生写入/);
  assert.equal(postCommit.run.id, "run-2");
  assert.match(postCommit.message, /重新审阅和批准/);
});

test("Draft Test presentation exposes sanitized scope, remaining execution result, and stop control", () => {
  assert.equal(
    runControlState({ phase: "testing", head_version_id: "v2" }).canPause,
    true,
  );
  const presentation = testPresentation({
    side_effects: {
      highest_risk: "external",
      counts: { http: 1, model_cost: 1 },
    },
    test_result: {
      input_preview: {
        inputs: { query: "test", api_key: "[REDACTED]" },
      },
      execution: {
        status: "failed",
        failed_node_id: "http-1",
        error_code: "EXECUTION_HTTP_FAILED",
      },
    },
  });
  assert.match(presentation.scope, /external/);
  assert.equal(presentation.inputs.inputs.api_key, "[REDACTED]");
  assert.match(presentation.result, /http-1/);
  assert.match(presentation.result, /EXECUTION_HTTP_FAILED/);
});
