export const CANVAS_CONTEXT_PROTOCOL = "chat2dify.canvas-context.v1";
export const CANVAS_CONTEXT_VERSION = "1.0";

export const CANVAS_MESSAGE_TYPES = new Set([
  "dify.context.init",
  "dify.selection.changed",
  "dify.draft.changed",
]);

export const AGENT_EVENT_TYPES = [
  "agent.started",
  "context.loaded",
  "context.updated",
  "goal_plan.created",
  "goal_plan.updated",
  "agent.decision",
  "tool.started",
  "tool.completed",
  "workspace.version.created",
  "workspace.head.moved",
  "validation.started",
  "validation.failed",
  "validation.passed",
  "repair.started",
  "test.approval_required",
  "test.started",
  "test.progress",
  "test.completed",
  "review.ready",
  "approval.required",
  "approval.resolved",
  "commit.started",
  "commit.completed",
  "agent.paused",
  "agent.resumed",
  "agent.completed",
  "agent.failed",
];

export class CanvasContextChannel {
  constructor({ expectedOrigin, nonce, sourceWindow }) {
    if (!isOrigin(expectedOrigin)) {
      throw new Error("A valid parent origin is required.");
    }
    if (!isNonce(nonce)) {
      throw new Error("A valid per-panel context nonce is required.");
    }
    this.expectedOrigin = expectedOrigin;
    this.nonce = nonce;
    this.sourceWindow = sourceWindow;
    this.lastRevision = 0;
  }

  accept(event) {
    if (!event || event.origin !== this.expectedOrigin) {
      return null;
    }
    if (this.sourceWindow && event.source !== this.sourceWindow) {
      return null;
    }
    const message = event.data;
    if (!message || typeof message !== "object") {
      return null;
    }
    if (
      message.protocol !== CANVAS_CONTEXT_PROTOCOL
      || !CANVAS_MESSAGE_TYPES.has(message.type)
      || message.context_nonce !== this.nonce
    ) {
      return null;
    }
    const context = normalizeCanvasContext(message.payload);
    if (!context || context.revision <= this.lastRevision) {
      return null;
    }
    this.lastRevision = context.revision;
    return { type: message.type, context };
  }

  frameMessage(type) {
    if (!["chat2dify.ready", "chat2dify.context.refresh"].includes(type)) {
      throw new Error(`Unsupported frame message: ${type}`);
    }
    return {
      protocol: CANVAS_CONTEXT_PROTOCOL,
      type,
      context_nonce: this.nonce,
    };
  }
}

export class EventCursor {
  constructor(runId, initialSequence = 0) {
    this.runId = String(runId || "");
    this.sequence = Number.isInteger(initialSequence) && initialSequence > 0
      ? initialSequence
      : 0;
    this.seen = new Set();
  }

  accept(event) {
    if (
      !event
      || event.run_id !== this.runId
      || !Number.isInteger(event.seq)
      || event.seq < 1
    ) {
      return false;
    }
    const key = `${event.run_id}:${event.seq}`;
    if (this.seen.has(key)) {
      return false;
    }
    this.seen.add(key);
    this.sequence = Math.max(this.sequence, event.seq);
    return true;
  }
}

export function parseSse(text) {
  const events = [];
  for (const block of String(text || "").split(/\r?\n\r?\n/)) {
    if (!block.trim() || block.trimStart().startsWith(":")) {
      continue;
    }
    let type = "message";
    let data = "";
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) {
        type = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        data += line.slice(5).trimStart();
      }
    }
    if (!data) {
      continue;
    }
    try {
      events.push({ type, data: JSON.parse(data) });
    } catch (_error) {
      // Malformed events are ignored and polling remains available.
    }
  }
  return events;
}

export function approvalMatchesVisibleVersion(approval, workspaceVersionId) {
  return Boolean(
    approval
    && workspaceVersionId
    && approval.workspace_version_id === workspaceVersionId
    && ["pending", "approved"].includes(approval.status)
  );
}

export function commitBlockReason(run, canvasContext) {
  if (!run?.head_version_id) {
    return "没有可提交的工作区版本。";
  }
  if (!canvasContext) {
    return "";
  }
  if (canvasContext.dirty_state) {
    return "Dify 画布仍有未同步变更。";
  }
  if (run.base_hash && !canvasContext.canvas_draft_hash) {
    return "Dify 画布尚未提供可验证的草稿 Hash。";
  }
  if (
    canvasContext.canvas_draft_hash
    && run.base_hash
    && canvasContext.canvas_draft_hash !== run.base_hash
  ) {
    return "Dify 画布 Hash 与本次 Run 的基准 Hash 不一致。";
  }
  return "";
}

export function runControlState(run) {
  const phase = run?.phase || "";
  const terminal = ["completed", "conflicted", "cancelled", "failed"].includes(phase);
  return {
    canPause: ["queued", "observing", "planning", "acting", "validating", "testing"].includes(phase),
    canResume: ["paused", "interrupted"].includes(phase),
    resumesFromComposer: phase === "waiting_user",
    canUndo: Boolean(run?.head_version_id),
    canCancel: Boolean(run) && !terminal && phase !== "committing",
  };
}

export function undoPresentation(result) {
  if (
    !result
    || !["pre_commit", "post_commit"].includes(result.kind)
    || !result.run?.id
  ) {
    throw new Error("Undo returned an invalid Run result.");
  }
  return {
    run: result.run,
    message: result.kind === "pre_commit"
      ? "已恢复父工作区版本；Dify 未发生写入。"
      : "已生成新的补偿预览；需重新审阅和批准。",
  };
}

export function reviewDiffRows(review) {
  return (review?.technical_diff || [])
    .filter(change => change && typeof change === "object")
    .map(change => ({
      type: String(change.type || "changed"),
      message: String(change.message || change.type || "changed"),
    }));
}

export function testPresentation(review) {
  const sideEffects = review?.test_result?.side_effects
    || review?.side_effects
    || review?.validation?.side_effects;
  const execution = review?.test_result?.execution || review?.test_result;
  const risk = sideEffects?.highest_risk || "unknown";
  const counts = Object.entries(sideEffects?.counts || {})
    .map(([kind, count]) => `${kind}:${count}`)
    .join(" · ");
  const status = execution?.status || "not_run";
  const failedNode = execution?.failed_node_id
    ? ` · 失败节点 ${execution.failed_node_id}`
    : "";
  return {
    scope: `副作用：${risk}${counts ? ` · ${counts}` : ""}`,
    inputs: review?.test_result?.input_preview || {},
    result: status === "not_run"
      ? "等待执行观察"
      : `执行结果：${status}${failedNode}${execution?.error_code ? ` · ${execution.error_code}` : ""}`,
  };
}

export function timelinePresentation(event) {
  const type = event?.type || "agent.event";
  const tone = type.endsWith(".failed") || type === "validation.failed"
    ? "danger"
    : type.endsWith(".passed") || type.endsWith(".completed")
      ? "success"
      : type.includes("approval") || type === "agent.paused"
        ? "warning"
        : "neutral";
  return {
    type,
    phase: event?.phase || "",
    message: businessEventMessage(event),
    tone,
  };
}

export function normalizeCanvasContext(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  if (
    Object.prototype.hasOwnProperty.call(value, "graph")
    || Object.prototype.hasOwnProperty.call(value, "raw_graph")
  ) {
    return null;
  }
  if (
    value.protocol_version !== CANVAS_CONTEXT_VERSION
    || !Number.isInteger(value.revision)
    || value.revision < 1
    || !isIdList(value.selected_node_ids)
    || !isIdList(value.selected_edge_ids)
    || typeof value.dirty_state !== "boolean"
  ) {
    return null;
  }
  const viewport = value.viewport;
  if (
    !viewport
    || !isFiniteNumber(viewport.x)
    || !isFiniteNumber(viewport.y)
    || !isFiniteNumber(viewport.zoom)
    || viewport.zoom <= 0
    || viewport.zoom > 100
  ) {
    return null;
  }
  if (
    value.current_panel !== undefined
    && (typeof value.current_panel !== "string" || value.current_panel.length > 128)
  ) {
    return null;
  }
  if (
    value.canvas_draft_hash !== undefined
    && (
      typeof value.canvas_draft_hash !== "string"
      || value.canvas_draft_hash.length > 512
    )
  ) {
    return null;
  }
  return {
    protocol_version: CANVAS_CONTEXT_VERSION,
    revision: value.revision,
    selected_node_ids: [...new Set(value.selected_node_ids)],
    selected_edge_ids: [...new Set(value.selected_edge_ids)],
    viewport: {
      x: value.viewport.x,
      y: value.viewport.y,
      zoom: value.viewport.zoom,
    },
    current_panel: value.current_panel || undefined,
    dirty_state: value.dirty_state,
    canvas_draft_hash: value.canvas_draft_hash || undefined,
  };
}

function isIdList(value) {
  return Array.isArray(value)
    && value.length <= 100
    && value.every(item => (
      typeof item === "string"
      && item.trim().length > 0
      && item.length <= 256
    ));
}

function isOrigin(value) {
  try {
    return new URL(value).origin === value;
  } catch (_error) {
    return false;
  }
}

function isNonce(value) {
  return typeof value === "string"
    && value.length >= 16
    && value.length <= 256
    && /^[A-Za-z0-9_-]+$/.test(value);
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function businessEventMessage(event) {
  const message = String(event?.message || "");
  if (/[\u4e00-\u9fff]/.test(message)) {
    return message;
  }
  if (
    event?.type === "agent.decision"
    && event?.data?.type === "ask_user"
    && event.data.question
  ) {
    return `需要补充信息：${event.data.question}`;
  }
  if (event?.type === "agent.started" && event?.data?.goal) {
    return `开始处理目标：${event.data.goal}`;
  }
  const toolName = event?.data?.tool_name || event?.data?.toolName;
  const labels = {
    "agent.started": "Builder Agent 已开始工作。",
    "context.loaded": "已读取权威工作流与能力上下文。",
    "context.updated": "已更新画布选择上下文。",
    "goal_plan.created": "已生成 Goal Plan。",
    "goal_plan.updated": "Goal Plan 已更新。",
    "agent.decision": "Agent 已选择下一步操作。",
    "tool.started": toolName ? `开始执行 ${toolName}。` : "开始执行工具。",
    "tool.completed": toolName ? `${toolName} 执行完成。` : "工具执行完成。",
    "workspace.version.created": "已创建新的工作区版本。",
    "workspace.head.moved": "工作区已撤销到父版本。",
    "validation.started": "正在执行确定性校验。",
    "validation.failed": "确定性校验未通过。",
    "validation.passed": "确定性校验通过。",
    "repair.started": "已根据结构化执行错误开始受限修复。",
    "test.approval_required": "Draft Test 正在等待副作用审批。",
    "test.started": "已开始批准的 Draft Test。",
    "test.progress": "Draft Test 返回了脱敏进度。",
    "test.completed": "Draft Test 已生成结构化执行观察。",
    "review.ready": "业务 Diff 与风险审阅已就绪。",
    "approval.required": "此操作需要用户审批。",
    "approval.resolved": "审批状态已更新。",
    "commit.started": "正在通过安全链路写回 Dify。",
    "commit.completed": "Dify 写回已结束。",
    "agent.paused": "Agent 已暂停，等待用户操作。",
    "agent.resumed": "Agent 已从持久化检查点继续。",
    "agent.completed": "Agent Run 已完成。",
    "agent.failed": "Agent Run 失败。",
  };
  return labels[event?.type] || message || event?.type || "Agent 事件";
}
