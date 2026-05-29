const state = {
  lastResponse: {},
};

const els = {
  healthStatus: document.querySelector("#health-status"),
  resultJson: document.querySelector("#result-json"),
  summaryGrid: document.querySelector("#summary-grid"),
  changesList: document.querySelector("#changes-list"),
  createForm: document.querySelector("#create-form"),
  createStatus: document.querySelector("#create-status"),
  modifyStatus: document.querySelector("#modify-status"),
  runStatus: document.querySelector("#run-status"),
  modifyAppId: document.querySelector("#modify-app-id"),
  modifyExpectedHash: document.querySelector("#modify-expected-hash"),
  runAppId: document.querySelector("#run-app-id"),
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  refreshHealth();
  renderResult({});
});

function bindEvents() {
  els.createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await handleCreate();
  });

  document.querySelector("#preview-modify").addEventListener("click", async () => {
    await handleModify("/api/workflows/modify/draft", "preview");
  });

  document.querySelector("#apply-modify").addEventListener("click", async () => {
    await handleModify("/api/workflows/modify/apply", "apply");
  });

  document.querySelector("#run-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await handleRun();
  });

  document.querySelector("#clear-result").addEventListener("click", () => {
    renderResult({});
    setPanelStatus(els.createStatus, "");
    setPanelStatus(els.modifyStatus, "");
    setPanelStatus(els.runStatus, "");
  });
}

async function refreshHealth() {
  try {
    const data = await requestJson("/health");
    const version = data?.dify?.app_dsl_version || "unknown";
    els.healthStatus.textContent = `Healthy · DSL ${version}`;
    els.healthStatus.className = "status-pill status-ok";
  } catch (error) {
    els.healthStatus.textContent = "Offline";
    els.healthStatus.className = "status-pill status-error";
  }
}

async function handleCreate() {
  await withBusy(els.createForm, els.createStatus, "Creating", async () => {
    const payload = {
      message: valueOf("#create-message"),
      app_name: optionalValue("#create-app-name"),
    };
    const data = await requestJson("/api/workflows/create", {
      method: "POST",
      body: payload,
    });
    syncAppId(data.app_id);
    setPanelStatus(els.createStatus, data.status || "Created", "ok");
    renderResult(data);
  });
}

async function handleModify(path, mode) {
  const form = document.querySelector("#modify-form");
  if (!form.reportValidity()) {
    return;
  }
  await withBusy(document.querySelector("#modify-form"), els.modifyStatus, mode === "apply" ? "Applying" : "Previewing", async () => {
    const payload = {
      app_id: valueOf("#modify-app-id"),
      message: valueOf("#modify-message"),
      expected_hash: optionalValue("#modify-expected-hash"),
      allow_destructive: document.querySelector("#modify-allow-destructive").checked,
    };
    const data = await requestJson(path, {
      method: "POST",
      body: payload,
    });
    if (data.new_hash) {
      document.querySelector("#modify-expected-hash").value = data.new_hash;
    } else if (data.base_hash && !payload.expected_hash) {
      document.querySelector("#modify-expected-hash").value = data.base_hash;
    }
    syncAppId(data.app_id || payload.app_id);
    const guard = data.guard?.risk ? `Guard ${data.guard.risk}` : "Ready";
    setPanelStatus(els.modifyStatus, mode === "apply" ? "Applied" : guard, guardClass(data.guard));
    renderResult(data);
  });
}

async function handleRun() {
  await withBusy(document.querySelector("#run-form"), els.runStatus, "Running", async () => {
    const payload = {
      app_id: valueOf("#run-app-id"),
      inputs: parseJsonField("#run-inputs", "Inputs JSON"),
      timeout_seconds: Number(valueOf("#run-timeout") || 120),
    };
    const data = await requestJson("/api/workflows/run/draft", {
      method: "POST",
      body: payload,
    });
    setPanelStatus(els.runStatus, data.status || "Done", data.ok ? "ok" : "error");
    renderResult(data);
  });
}

async function withBusy(container, statusElement, label, action) {
  const buttons = Array.from(container.querySelectorAll("button"));
  try {
    buttons.forEach((button) => {
      button.disabled = true;
    });
    setPanelStatus(statusElement, label, "muted");
    await action();
  } catch (error) {
    setPanelStatus(statusElement, "Error", "error");
    renderResult(error.payload || { error: error.message });
  } finally {
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
}

async function requestJson(path, options = {}) {
  const init = {
    method: options.method || "GET",
    headers: {},
  };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(compactPayload(options.body));
  }
  const response = await fetch(path, init);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(extractErrorMessage(data, response.status));
    error.payload = typeof data === "string" ? { error: data, status: response.status } : data;
    throw error;
  }
  return data;
}

function renderResult(data) {
  state.lastResponse = data || {};
  els.resultJson.textContent = JSON.stringify(state.lastResponse, null, 2);
  renderSummary(state.lastResponse);
  renderChanges(state.lastResponse);
}

function renderSummary(data) {
  const items = [
    ["App ID", data.app_id],
    ["Workflow", linkValue(data.workflow_url)],
    ["Status", data.status ?? data.guard?.risk],
    ["Run OK", typeof data.ok === "boolean" ? String(data.ok) : undefined],
    ["Base hash", data.base_hash],
    ["New hash", data.new_hash],
    ["Run ID", data.workflow_run_id],
    ["Task ID", data.task_id],
    ["Tokens", data.total_tokens],
    ["Steps", data.total_steps],
    ["Error", data.error || data.detail?.message || data.detail],
  ].filter((item) => item[1] !== undefined && item[1] !== null && item[1] !== "");

  els.summaryGrid.replaceChildren(
    ...items.map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "summary-item";
      const labelEl = document.createElement("span");
      labelEl.className = "summary-label";
      labelEl.textContent = label;
      const valueEl = document.createElement("span");
      valueEl.className = "summary-value";
      if (value instanceof Node) {
        valueEl.append(value);
      } else {
        valueEl.textContent = String(value);
      }
      item.append(labelEl, valueEl);
      return item;
    })
  );
}

function renderChanges(data) {
  const messages = [];
  if (Array.isArray(data.changes)) {
    messages.push(...data.changes.map((change) => change.message || `${change.type}: ${change.target}`));
  }
  if (Array.isArray(data.guard?.issues)) {
    messages.push(...data.guard.issues.map((issue) => `${issue.severity}: ${issue.message}`));
  }
  if (data.outputs) {
    messages.push(`Outputs: ${JSON.stringify(data.outputs)}`);
  }

  els.changesList.replaceChildren(
    ...messages.slice(0, 12).map((message) => {
      const row = document.createElement("div");
      row.className = "change-row";
      row.textContent = message;
      return row;
    })
  );
}

function linkValue(url) {
  if (!url) {
    return undefined;
  }
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = url;
  return link;
}

function syncAppId(appId) {
  if (!appId) {
    return;
  }
  els.modifyAppId.value = appId;
  els.runAppId.value = appId;
}

function valueOf(selector) {
  return document.querySelector(selector).value.trim();
}

function optionalValue(selector) {
  const value = valueOf(selector);
  return value || undefined;
}

function parseJsonField(selector, label) {
  const raw = valueOf(selector);
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`${label} is not valid JSON.`);
  }
}

function compactPayload(payload) {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined && value !== ""));
}

function setPanelStatus(element, text, tone = "muted") {
  element.textContent = text;
  if (!text) {
    element.className = "panel-status";
    return;
  }
  const className = tone === "ok" ? "status-ok" : tone === "error" ? "status-error" : tone === "warning" ? "status-warning" : "status-muted";
  element.className = `panel-status ${className}`;
}

function guardClass(guard) {
  if (!guard) {
    return "muted";
  }
  if (guard.risk === "high") {
    return "error";
  }
  if (guard.risk === "medium") {
    return "warning";
  }
  return "ok";
}

function extractErrorMessage(data, status) {
  if (typeof data === "string") {
    return data || `Request failed with ${status}`;
  }
  if (typeof data?.detail === "string") {
    return data.detail;
  }
  if (data?.detail?.message) {
    return data.detail.message;
  }
  return `Request failed with ${status}`;
}
