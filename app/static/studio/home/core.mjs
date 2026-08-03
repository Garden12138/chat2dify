export const STUDIO_NAVIGATION = Object.freeze([
  { id: "home", label: "Studio Home", available: true },
  { id: "build", label: "Build Studio", available: true },
  { id: "blueprints", label: "Blueprints", available: true },
  { id: "scenarios", label: "Scenarios", available: false },
  { id: "releases", label: "Reviews & Releases", available: false },
  { id: "runs", label: "Runs", available: false },
]);

export function isStudioHomeEnabled(config, search = "") {
  const params = new URLSearchParams(search);
  return Boolean(config?.studioV5Enabled)
    && !["build", "blueprints"].includes(params.get("studio"));
}

export function homeQuery({ projectId = "", search = "", appMode = "" } = {}) {
  const params = new URLSearchParams();
  if (projectId) {
    params.set("project_id", projectId);
  }
  if (search.trim()) {
    params.set("search", search.trim());
  }
  if (appMode) {
    params.set("app_mode", appMode);
  }
  const suffix = params.toString();
  return `/api/v5/studio/home${suffix ? `?${suffix}` : ""}`;
}

export function classifyStudioError(status, payload = {}) {
  const error = payload?.error || {};
  const code = String(error.code || "STUDIO_REQUEST_FAILED");
  if (status === 401) {
    return {
      kind: "permission",
      code,
      title: "需要重新连接 Dify",
      message: error.message || "登录会话已失效，请重新连接。",
      action: "reconnect",
    };
  }
  if (status === 403) {
    return {
      kind: "permission",
      code,
      title: "无法访问这个项目",
      message: error.message || "当前账号没有该项目的访问权限。",
      action: "none",
    };
  }
  if (status === 404 && code === "AI_STUDIO_V5_DISABLED") {
    return {
      kind: "disabled",
      code,
      title: "Studio v5 当前未启用",
      message: "刷新后会回到原有 Chat2Dify 工作台。",
      action: "reload",
    };
  }
  if (status === 503 || error.retryable) {
    return {
      kind: "offline",
      code,
      title: "暂时无法连接 Studio",
      message: error.message || "服务暂时不可用，请稍后重试。",
      action: "retry",
    };
  }
  return {
    kind: "error",
    code,
    title: "Studio 加载失败",
    message: error.message || "发生了未预期的错误。",
    action: "retry",
  };
}

export function appModeLabel(mode) {
  return {
    workflow: "Workflow",
    "advanced-chat": "Chatflow",
    chat: "Chatbot",
    "agent-chat": "Agent",
    completion: "文本生成",
  }[mode] || mode || "Dify 应用";
}

export function runPhaseLabel(phase) {
  return {
    queued: "等待开始",
    observing: "读取应用",
    planning: "生成计划",
    acting: "构建变更",
    validating: "确定性校验",
    testing: "草稿测试",
    paused: "已暂停",
    waiting_user: "等待补充信息",
    waiting_approval: "等待审批",
    committing: "正在提交",
    completed: "已完成",
    conflicted: "存在冲突",
    cancelled: "已取消",
    failed: "失败",
    interrupted: "可恢复中断",
  }[phase] || phase || "未知状态";
}

export function relativeTime(value, now = Date.now()) {
  if (!value) {
    return "更新时间未知";
  }
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) {
    return "更新时间未知";
  }
  const seconds = Math.max(0, Math.round((now - timestamp) / 1000));
  if (seconds < 60) return "刚刚更新";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(timestamp));
}

export function safeBuildUrl(value, basePath = "", contextNonce = "") {
  const raw = String(value || "");
  const normalizedBase = String(basePath || "").replace(/\/+$/, "");
  const fallback = `${normalizedBase}/`;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) {
    return fallback;
  }
  try {
    const url = new URL(raw, "https://studio.invalid");
    if (
      url.origin !== "https://studio.invalid"
      || (
        normalizedBase
        && url.pathname !== `${normalizedBase}/`
        && !url.pathname.startsWith(`${normalizedBase}/`)
      )
    ) {
      return fallback;
    }
    if (isContextNonce(contextNonce)) {
      url.searchParams.set("context_nonce", contextNonce);
    }
    return `${url.pathname}${url.search}`;
  } catch (_error) {
    return fallback;
  }
}

export function isContextNonce(value) {
  return typeof value === "string"
    && value.length >= 16
    && value.length <= 256
    && /^[A-Za-z0-9_-]+$/.test(value);
}
