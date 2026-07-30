import {
  appModeLabel,
  classifyStudioError,
  homeQuery,
  isStudioHomeEnabled,
  relativeTime,
  runPhaseLabel,
  safeBuildUrl,
} from "./core.mjs";

const config = window.CHAT2DIFY_CONFIG || {};
const basePath = normalizeBasePath(config.basePath || "");
const contextNonce = new URLSearchParams(window.location.search).get("context_nonce") || "";
const studioEnabled = isStudioHomeEnabled(config, window.location.search);
const studioState = {
  token: "",
  projectId: "",
  debounce: null,
  lastHome: null,
  requestSequence: 0,
};

if (studioEnabled) {
  window.CHAT2DIFY_STUDIO_HOME = true;
  document.addEventListener("DOMContentLoaded", () => {
    void bootStudio();
  });
}

async function bootStudio() {
  document.body.classList.add("studio-v5");
  if (new URLSearchParams(window.location.search).get("embed") === "1") {
    document.body.classList.add("studio-embedded");
  }
  document.querySelector("#legacy-app-frame")?.setAttribute("hidden", "");
  const root = document.querySelector("#studio-root");
  root.hidden = false;
  bindStudioActions();
  await connectStudio();
}

function bindStudioActions() {
  document.querySelector("#studio-refresh").addEventListener("click", () => {
    void loadHome({ announce: true });
  });
  document.querySelector("#studio-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    void loadHome({ announce: true });
  });
  document.querySelector("#studio-search-input").addEventListener("input", () => {
    window.clearTimeout(studioState.debounce);
    studioState.debounce = window.setTimeout(() => {
      void loadHome({ announce: false });
    }, 280);
  });
  document.querySelector("#studio-mode-select").addEventListener("change", () => {
    void loadHome({ announce: true });
  });
}

async function connectStudio() {
  showLoading("正在验证 Dify 会话", "我们只使用服务端验证的账号和当前 Workspace。");
  setConnection("正在连接 Dify", "loading");
  try {
    const session = await requestJson("/api/v5/studio/session", {
      method: "POST",
      body: { nonce: createNonce() },
      authenticated: false,
    });
    studioState.token = session.token;
    studioState.projectId = session.project.id;
    document.querySelector("#studio-project-badge").textContent = session.project.name;
    setConnection("Dify 已验证", "ok");
    await loadHome({ announce: false });
  } catch (error) {
    showFailure(error.presentation || classifyStudioError(500, {}));
  }
}

async function loadHome({ announce }) {
  if (!studioState.token) {
    await connectStudio();
    return;
  }
  if (announce) {
    showLoading("正在刷新 Studio Home", "正在读取当前账号可访问的真实数据。");
  }
  const query = homeQuery({
    projectId: studioState.projectId,
    search: document.querySelector("#studio-search-input").value,
    appMode: document.querySelector("#studio-mode-select").value,
  });
  const requestSequence = ++studioState.requestSequence;
  try {
    const home = await requestJson(query, { authenticated: true });
    if (requestSequence !== studioState.requestSequence) {
      return;
    }
    studioState.lastHome = home;
    renderHome(home);
    setConnection(
      home.states?.apps?.state === "partial_error" ? "部分数据不可用" : "Dify 已验证",
      home.states?.apps?.state === "partial_error" ? "warning" : "ok",
    );
  } catch (error) {
    if (requestSequence !== studioState.requestSequence) {
      return;
    }
    if (error.presentation?.action === "reconnect") {
      studioState.token = "";
    }
    showFailure(error.presentation || classifyStudioError(500, {}));
  }
}

function renderHome(home) {
  document.querySelector("#studio-state").hidden = true;
  document.querySelector("#studio-content").hidden = false;
  document.querySelector("#studio-project-badge").textContent = home.project.name;
  document.querySelector("#studio-home-summary").textContent =
    `当前为 ${roleLabel(home.membership.role)} 视图；所有内容都限定在这个项目中。`;
  renderApps(home.apps || [], home.states?.apps);
  renderWork(home.work || [], home.states?.work, home.project.id);
}

function renderApps(apps, state) {
  const list = document.querySelector("#studio-app-list");
  const stateElement = document.querySelector("#studio-apps-state");
  stateElement.textContent = state?.message || "";
  stateElement.dataset.tone = state?.state || "ready";
  list.replaceChildren();
  if (!apps.length) {
    list.append(emptyCard(
      state?.state === "partial_error" ? "应用列表暂时不可用" : "没有匹配的应用",
      state?.message || "调整搜索或类型筛选后重试。",
      state?.recoverable ? "重试加载" : "清除筛选",
      state?.recoverable
        ? () => loadHome({ announce: true })
        : () => clearFilters(),
    ));
    return;
  }
  for (const app of apps) {
    const anchor = document.createElement("a");
    anchor.className = "studio-app-card";
    anchor.href = safeBuildUrl(app.build_url, basePath, contextNonce);
    anchor.setAttribute("aria-label", `继续构建 ${app.name}，${appModeLabel(app.mode)}`);

    const top = document.createElement("div");
    top.className = "studio-app-top";
    const icon = document.createElement("span");
    icon.className = `studio-app-icon studio-app-icon-${modeClass(app.mode)}`;
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = modeGlyph(app.mode);
    const badge = document.createElement("span");
    badge.className = "studio-mode-badge";
    badge.textContent = appModeLabel(app.mode);
    top.append(icon, badge);

    const title = document.createElement("h3");
    title.textContent = app.name;
    const description = document.createElement("p");
    description.textContent = app.description || "这个应用尚未填写描述。";
    const footer = document.createElement("div");
    footer.className = "studio-app-footer";
    const updated = document.createElement("span");
    updated.textContent = relativeTime(app.updated_at);
    const action = document.createElement("span");
    action.textContent = "继续构建 →";
    footer.append(updated, action);
    anchor.append(top, title, description, footer);
    list.append(anchor);
  }
}

function renderWork(items, state, projectId) {
  const list = document.querySelector("#studio-work-list");
  const stateElement = document.querySelector("#studio-work-state");
  stateElement.textContent = state?.message || "";
  stateElement.dataset.tone = state?.state || "ready";
  list.replaceChildren();
  if (!items.length) {
    list.append(emptyCard(
      "暂无可继续的 Builder 工作",
      state?.message || "从一个应用开始构建后，工作会显示在这里。",
      "浏览应用",
      () => document.querySelector("#studio-search-input").focus(),
    ));
    return;
  }
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "studio-work-card";
    const status = document.createElement("span");
    status.className = `studio-run-status studio-run-${phaseTone(item.phase)}`;
    status.textContent = runPhaseLabel(item.phase);
    const body = document.createElement("div");
    body.className = "studio-work-body";
    const heading = document.createElement("h3");
    heading.textContent = item.app_name;
    const goal = document.createElement("p");
    goal.textContent = item.goal;
    const meta = document.createElement("small");
    meta.textContent = `${appModeLabel(item.app_mode)} · ${relativeTime(item.updated_at)}`;
    if (item.reason) {
      const reason = document.createElement("p");
      reason.className = "studio-work-reason";
      reason.textContent = item.reason;
      body.append(heading, goal, meta, reason);
    } else {
      body.append(heading, goal, meta);
    }
    const actions = document.createElement("div");
    actions.className = "studio-work-actions";
    if (item.resumable && !item.resume_requires_message) {
      const resume = document.createElement("button");
      resume.type = "button";
      resume.textContent = "继续运行";
      resume.addEventListener("click", () => {
        void resumeWork(projectId, item, resume);
      });
      actions.append(resume);
    }
    const open = document.createElement("a");
    open.href = safeBuildUrl(item.build_url, basePath, contextNonce);
    open.textContent = item.resume_requires_message ? "补充信息" : "打开记录";
    open.setAttribute("aria-label", `${open.textContent}：${item.app_name}`);
    actions.append(open);
    card.append(status, body, actions);
    list.append(card);
  }
}

async function resumeWork(projectId, item, button) {
  button.disabled = true;
  button.textContent = "正在继续";
  try {
    await requestJson("/api/v5/studio/home/resume-v4", {
      method: "POST",
      authenticated: true,
      body: {
        project_id: projectId,
        run_id: item.run_id,
      },
    });
    window.location.assign(
      safeBuildUrl(item.build_url, basePath, contextNonce),
    );
  } catch (error) {
    button.disabled = false;
    button.textContent = "重试继续";
    announceInline(error.presentation?.message || "暂时无法继续这项工作。", "danger");
  }
}

function emptyCard(titleText, bodyText, actionText, actionHandler) {
  const card = document.createElement("article");
  card.className = "studio-empty-card";
  const mark = document.createElement("span");
  mark.className = "studio-empty-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = "◇";
  const title = document.createElement("h3");
  title.textContent = titleText;
  const body = document.createElement("p");
  body.textContent = bodyText;
  const action = document.createElement("button");
  action.type = "button";
  action.className = "studio-link-action";
  action.textContent = actionText;
  action.addEventListener("click", actionHandler);
  card.append(mark, title, body, action);
  return card;
}

function clearFilters() {
  document.querySelector("#studio-search-input").value = "";
  document.querySelector("#studio-mode-select").value = "";
  void loadHome({ announce: true });
}

function showLoading(title, message) {
  const state = document.querySelector("#studio-state");
  state.hidden = false;
  state.dataset.kind = "loading";
  state.replaceChildren();
  const spinner = document.createElement("div");
  spinner.className = "studio-spinner";
  spinner.setAttribute("aria-hidden", "true");
  const copy = document.createElement("div");
  const heading = document.createElement("h1");
  heading.textContent = title;
  const paragraph = document.createElement("p");
  paragraph.textContent = message;
  copy.append(heading, paragraph);
  state.append(spinner, copy);
  document.querySelector("#studio-content").hidden = true;
}

function showFailure(presentation) {
  const state = document.querySelector("#studio-state");
  state.hidden = false;
  state.dataset.kind = presentation.kind;
  state.replaceChildren();
  const mark = document.createElement("div");
  mark.className = "studio-failure-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = presentation.kind === "permission" ? "⌁" : "!";
  const copy = document.createElement("div");
  const heading = document.createElement("h1");
  heading.textContent = presentation.title;
  const paragraph = document.createElement("p");
  paragraph.textContent = presentation.message;
  const code = document.createElement("small");
  code.textContent = presentation.code;
  copy.append(heading, paragraph, code);
  if (presentation.action !== "none") {
    const action = document.createElement("button");
    action.type = "button";
    action.className = "studio-primary-action";
    action.textContent = presentation.action === "reconnect" ? "重新连接" : "重试";
    action.addEventListener("click", () => {
      if (presentation.action === "reload") {
        window.location.reload();
      } else if (presentation.action === "reconnect") {
        void connectStudio();
      } else {
        void (studioState.token ? loadHome({ announce: true }) : connectStudio());
      }
    });
    copy.append(action);
  }
  state.append(mark, copy);
  document.querySelector("#studio-content").hidden = true;
  state.focus?.();
}

function announceInline(message, tone) {
  const connection = document.querySelector("#studio-connection");
  connection.textContent = message;
  connection.dataset.tone = tone;
}

function setConnection(message, tone) {
  const connection = document.querySelector("#studio-connection");
  connection.textContent = message;
  connection.dataset.tone = tone;
}

async function requestJson(path, options = {}) {
  const headers = { Accept: "application/json" };
  if (studioState.token && options.authenticated !== false) {
    headers.Authorization = `Bearer ${studioState.token}`;
  }
  const init = {
    method: options.method || "GET",
    headers,
    credentials: "same-origin",
    referrerPolicy: "strict-origin",
  };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch (_error) {
    const error = new Error("网络连接失败。");
    error.presentation = classifyStudioError(503, {
      error: {
        code: "STUDIO_NETWORK_OFFLINE",
        message: "无法连接 Chat2Dify，请检查网络或服务状态。",
        retryable: true,
      },
    });
    throw error;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
    error.presentation = classifyStudioError(response.status, payload);
    throw error;
  }
  return payload;
}

function createNonce() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error("浏览器不支持安全随机数，无法建立 Studio 会话。");
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(24));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function apiUrl(path) {
  return `${basePath}${path.startsWith("/") ? path : `/${path}`}`;
}

function normalizeBasePath(value) {
  const normalized = String(value || "").trim();
  if (!normalized || normalized === "/") return "";
  return `${normalized.startsWith("/") ? "" : "/"}${normalized}`.replace(/\/+$/, "");
}

function modeClass(mode) {
  return String(mode || "app").replace(/[^a-z-]/g, "");
}

function modeGlyph(mode) {
  return {
    workflow: "W",
    "advanced-chat": "C",
    chat: "B",
    "agent-chat": "A",
    completion: "T",
  }[mode] || "D";
}

function phaseTone(phase) {
  if (["paused", "waiting_user", "waiting_approval", "interrupted"].includes(phase)) return "attention";
  if (["failed", "conflicted"].includes(phase)) return "danger";
  if (phase === "completed") return "complete";
  return "active";
}

function roleLabel(role) {
  return {
    owner: "项目所有者",
    admin: "管理员",
    builder: "构建者",
    reviewer: "评审者",
    viewer: "只读成员",
  }[role] || role;
}
