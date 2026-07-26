# Chat2Dify v4.0.0 使用手册与实战教程

本手册面向使用 Chat2Dify Builder Agent 创建或修改 Dify 应用的用户，也可供
部署人员做首次启用检查。它描述的是 v4.0.0 已实现并通过 Release Gate 的
产品边界，不把未来规划或外部 Provider 豁免项当作当前能力。

## 1. 先认识 v4 Builder Agent

v4 不让模型直接写 Dify DSL 或调用 Dify 写接口。一次操作经过以下边界：

```text
目标
  → 读取 Dify Snapshot 和允许的上下文
  → 生成 Goal Plan
  → 调用 Typed Tools
  → Patch 版本化 Workspace
  → 确定性校验与必要修复
  → 展示业务 Diff、技术 Diff、风险和测试信息
  → 用户审批精确 Workspace 版本
  → Commit 前重新核对 Dify Hash
  → 写回 Dify 草稿
```

这带来四个直接变化：

- Agent 工作期间，Dify 草稿保持不变。
- 一个 Patch 要么完整成功并产生一个 Workspace 版本，要么完全不生效。
- Workspace 版本改变后，旧 Commit 审批自动失效。
- 发布不属于 Builder Agent 自动循环，必须走单独的显式发布操作。

### 1.1 应用类型范围

| Dify 类型 | `app_mode` | v4 新建 | v4 修改 | 上下文来源 |
| --- | --- | --- | --- | --- |
| Workflow | `workflow` | 支持 | 支持 | Dify 草稿 + 安全画布上下文 |
| Chatflow | `advanced-chat` | 支持 | 支持 | Dify 草稿 + 安全画布上下文 |
| Chatbot | `chat` | 使用 v3 | 支持 | Dify 持久化配置 |
| Completion | `completion` | 使用 v3 | 支持 | Dify 持久化配置 |
| Agent | `agent-chat` | 使用 v3 | 支持 | Dify 持久化配置 |

Workflow/Chatflow 使用 Graph `PatchDocument`，配置型应用使用
`ConfigPatchDocument`，两者不能互换。Agent 工具绑定等高风险配置变化会
要求额外的破坏性变更审批。

### 1.2 已验证兼容范围

- Dify：`1.14.x`，真实验收版本为 `1.14.2`
- Dify App DSL：`0.6.0`
- 其他或未知版本：允许有限读取和诊断，Workspace mutation 与 Commit
  默认拒绝，错误码为 `DIFY_VERSION_MUTATION_UNSUPPORTED`

## 2. 使用前准备

你需要：

1. 一个可访问的 Dify Console，以及能登录 Console 的账号。
2. Dify 源码目录。chat2dify 用它读取 Dify 和 App DSL 版本。
3. 至少一个稳定可用的 Planner/Decision Provider，用于 v4 多步决策。
4. 对准备发送给外部 Provider 的脱敏业务目标和上下文有明确授权。

需要区分两类模型：

- `PLANNER_*`、`NVIDIA_*`、`OPENROUTER_*`、`OPENAI_COMPATIBLE_*`、
  `OPENAI_*`：供 Chat2Dify Builder Agent 决策。
- `DIFY_DEFAULT_MODEL_PROVIDER` / `DIFY_DEFAULT_MODEL_NAME`：写进 Dify
  LLM 节点，供 Dify 应用实际运行。

v4.0.0 发布时，OpenAI-compatible 没有可用 key，免费 NVIDIA 服务存在
限流和超时，因此外部 Provider 的终态成功验收被明确豁免。这个豁免只影响
发布验收结论，不会让 Builder Agent 在无 Provider 的情况下完成多步决策。
生产环境应配置自己的稳定 Provider，并确认费用、限流和数据传输政策。

## 3. 安装与启用

### 3.1 独立运行

默认目录布局：

```text
github/
  dify/
  chat2dify/
```

安装：

```bash
cd chat2dify
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，至少确认：

```env
DIFY_SOURCE_DIR=../dify
DIFY_CONSOLE_API_BASE=http://127.0.0.1/console/api
DIFY_CONSOLE_WEB_BASE=http://127.0.0.1

DIFY_EMAIL=you@example.com
DIFY_PASSWORD=your-password
DIFY_LOGIN_LANGUAGE=en-US

CHAT2DIFY_AGENT_V4_ENABLED=true
CHAT2DIFY_TASK_DB=data/tasks.sqlite3
CHAT2DIFY_TASK_WORKERS=2

PLANNER_DEFAULT_PROVIDER=openai-compatible
PLANNER_FALLBACK_PROVIDERS=
OPENAI_COMPATIBLE_API_KEY=replace-with-your-key
OPENAI_COMPATIBLE_BASE_URL=https://llm-gateway.example/v1
OPENAI_COMPATIBLE_MODEL=replace-with-your-model
OPENAI_COMPATIBLE_RESPONSE_FORMAT=true
```

上例只是通用 OpenAI-compatible 配置模板。也可以按 `.env.example` 选择
NVIDIA、OpenRouter 或 OpenAI。不要把真实 key 写入仓库、提示词、截图或
问题单。

启动：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

检查：

```bash
curl http://127.0.0.1:8000/health
```

确认返回的关键字段为：

```text
status = ok
version = 4.0.0
features.agent_v4 = true
planner.configured = true
```

独立入口是 `http://127.0.0.1:8000/`。新建 Workflow 可使用：

```text
http://127.0.0.1:8000/?intent=create&app_mode=workflow
```

修改现有应用时需要传入真实 `app_id`：

```text
http://127.0.0.1:8000/?intent=modify&app_id=<app_id>&app_mode=workflow&app_name=<name>
```

独立修改入口可以读取 Dify 草稿，但没有 Dify 内嵌画布的实时选区；需要
“只改选中节点”的场景应使用内嵌入口。

### 3.2 随 Dify docker compose 启动

先把 Dify web 适配层同步到同级 Dify 仓库：

```bash
rsync -av deploy/dify/web-adapter/web/ ../dify/web/
```

在 `dify/docker/.env` 中加入：

```env
CHAT2DIFY_PUBLIC_BASE_PATH=/chat2dify
CHAT2DIFY_AGENT_V4_ENABLED=true
CHAT2DIFY_DIFY_CONSOLE_WEB_BASE=http://localhost

CHAT2DIFY_DIFY_EMAIL=you@example.com
CHAT2DIFY_DIFY_PASSWORD=your-password
CHAT2DIFY_DIFY_LOGIN_LANGUAGE=en-US

CHAT2DIFY_PLANNER_DEFAULT_PROVIDER=openai-compatible
CHAT2DIFY_PLANNER_FALLBACK_PROVIDERS=
CHAT2DIFY_OPENAI_COMPATIBLE_API_KEY=replace-with-your-key
CHAT2DIFY_OPENAI_COMPATIBLE_BASE_URL=https://llm-gateway.example/v1
CHAT2DIFY_OPENAI_COMPATIBLE_MODEL=replace-with-your-model
CHAT2DIFY_OPENAI_COMPATIBLE_RESPONSE_FORMAT=true
```

从 `dify/docker` 启动：

```bash
docker compose \
  -f docker-compose.yaml \
  -f ../../chat2dify/deploy/dify/docker-compose.chat2dify.yaml \
  up -d --build web chat2dify nginx
```

打开：

- Dify Studio：`http://localhost/apps`
- Chat2Dify 直接入口：`http://localhost/chat2dify/`
- 健康检查：`http://localhost/chat2dify/health`

内嵌入口包括：

- Studio 创建应用区域的 `Chat2Dify 创建`
- Workflow/Chatflow 画布顶部的 `Chat2Dify`
- 现有 Chatbot、Completion、Agent 配置页的 Chat2Dify Builder 操作条

修改 Dify web 适配层后需要重建 `web`。只使用直接
`/chat2dify/` 路由时，可以不使用这些内嵌入口。

## 4. Workbench 界面

Workbench 主要分成三块：

| 区域 | 看什么 | 什么时候操作 |
| --- | --- | --- |
| 时间线 | Inspect、Patch、Validate、Repair、Review 等事件 | 判断 Agent 当前在做什么 |
| Goal Plan | 每一步目标、状态和确定性证据 | 检查是否偏离原目标 |
| 审阅栏 | 业务 Diff、校验、风险、审批、测试和技术详情 | 决定批准、拒绝、提交或撤销 |

顶部操作：

- `暂停`：在允许暂停的执行阶段保存当前状态。
- `继续`：从 `paused` 或 `interrupted` 检查点继续。
- `撤销`：Commit 前回到父 Workspace 版本；Commit 后可能生成补偿预览。
- `取消`：停止当前 Run，不等于撤销已经完成的外部写入。

如果 Agent 提问，输入框会切换为补充信息模式，回答后继续同一个 Run。
浏览器断开 SSE 时会用游标重连，并保留轮询作为后备。

## 5. 教程一：创建 Workflow

### 5.1 打开新建入口

在 Dify Studio 的创建应用区域点击 `Chat2Dify 创建`，选择或进入
Workflow 新建模式。标题应显示“新建工作流”，状态显示
“Workbench 已就绪”。

### 5.2 输入目标

使用包含名称、输入、处理步骤和输出的目标：

```text
创建一个名为“电脑城售后助手”的 Workflow。
输入字段 query 是用户的电脑故障描述。
先判断故障属于电源、显示、系统还是其他，再生成三步排查建议；
最后输出判断、排查步骤和需要联系人工维修的条件。
不要添加 HTTP、工具或通知节点。
```

目标越明确，Agent 越少追问。约束“不要添加外部副作用节点”也会缩小测试
风险。

### 5.3 审阅

等待 Run 到达 Review，依次检查：

1. Goal Plan 是否覆盖分类、建议和最终输出。
2. “确定性校验”是否通过。
3. 业务 Diff 是否只包含预期的新节点、连线和变量。
4. 风险是否与节点类型一致。
5. 技术详情中是否出现意外的外部资源、工具或 HTTP 配置。

如果内容不对，拒绝审批或先撤销 Workspace，再提交更具体的新目标。不要
为了“先跑起来”而批准不理解的 Diff。

### 5.4 批准并提交

低风险新建通常会出现 Commit 审批：

1. 点击 Commit 审批卡片的“批准”。
2. 确认审批显示的版本等于当前可见 Workspace 版本。
3. 点击“提交到 Dify”。
4. 等待 `commit.completed`。
5. 根据返回的应用信息进入 Dify 检查草稿。

创建导入如果因网络中断而没有返回 app/import ID，会失败关闭，不能自动
重试。Dify 1.14.2 不按 `Idempotency-Key` 去重，直接重试可能创建重复应用；
此时先在 Dify 中人工核对，再决定是否新建 Session。

## 6. 教程二：定向修改 Workflow/Chatflow

### 6.1 准备画布

1. 在 Dify 打开目标 Workflow 或 Chatflow。
2. 保存或同步当前画布，避免 dirty state。
3. 选中要修改的节点。
4. 点击画布顶部的 `Chat2Dify`。
5. 等待界面显示已接收到画布上下文和选区。

浏览器只传递经过 origin、nonce 和协议校验的选择信息、viewport、dirty
state 和草稿 Hash；原始浏览器 Graph 不是 Commit 来源。

### 6.2 输入窄范围目标

```text
只修改当前选中的 LLM 节点。
保留节点 ID、其他节点、连线、变量、布局和功能配置。
把系统提示词改成中文售后专家：
先复述问题，再给最多三步排查，最后说明何时联系人工。
输出不要包含 Markdown 表格。
```

避免只写“优化一下”。明确“只改什么”和“必须保留什么”，有助于 Diff
保持可审阅。

### 6.3 处理 Draft Test

Agent 可能提出 Draft Test 审批卡片，其中包括：

- 副作用分类
- 输入预览
- 请求运行次数
- 可编辑的 JSON 输入

只批准你理解的输入和次数。模型、HTTP、Tool、通知或未知节点可能产生
费用或外部副作用。

对于 Dify 1.14.2：

- 未修改 Workspace 时可以运行持久化 baseline。
- Workspace 一旦发生 Graph Patch，就不能真实执行未提交候选 Graph。
- 此时出现 `DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED` 是预期的安全阻断。
- 审阅并 Commit 后，应通过 Dify 原生预览或 v3 Draft Run 新开一次显式
  运行，验证实际草稿。

### 6.4 Commit 冲突

点击“提交到 Dify”前，系统会检查：

- 当前 Workspace 版本是否仍是审批绑定的版本
- 确定性校验是否通过
- Dify 画布是否还有未同步变更
- 画布 Hash 是否与 Run 的 base Hash 一致
- Dify 持久化草稿是否在审批后被其他人修改

任何不一致都应重新读取 Snapshot 并生成新 Review，不能绕过。常见提示：

```text
Dify 画布仍有未同步变更。
Dify 画布 Hash 与本次 Run 的基准 Hash 不一致。
DIFY_DRAFT_HASH_CONFLICT
```

## 7. 教程三：修改 Chatbot、Completion 或 Agent

1. 打开已经存在的配置型应用。
2. 在配置页点击 Chat2Dify Builder 操作条。
3. 输入精确的配置修改目标。
4. 检查 Config Diff、字段前置条件、校验和风险。
5. Agent 工具绑定等高风险变化先批准“破坏性变更审批”。
6. 再批准 Commit，并点击“提交到 Dify”。

Chatbot 示例：

```text
只修改系统提示词和开场白。
系统提示词改成中文产品顾问，不确定时明确说不知道；
开场白改为“你好，我可以帮你比较产品功能和适用场景。”
不要修改模型、参数或敏感词配置。
```

配置型应用没有 Canvas Context。系统会在 Commit 前重新读取
`model_config`，比较 Dify 提供的 `hash`、`updated_at`、`version`，或比较
整个配置的规范化 SHA-256 指纹。若发生
`DIFY_MODEL_CONFIG_HASH_CONFLICT`，重新开 Run 审阅当前配置。

v4.0.0 不支持：

- 新建 Chatbot、Completion 或 Agent；请使用 v3。
- 在 Config Patch Agent 循环中执行配置型应用 Draft Run；审阅或 Commit
  后请使用保留的 v3 Chatbot、Completion 或 Agent Draft Run。
- 配置型应用 Commit 后的自动补偿 Undo；请创建新的 Config Patch，或使用
  v3 保留的配置预览路径。
- 在 Builder Agent 循环中自动发布。

## 8. 审批规则

| 审批 | 何时出现 | 批准前检查 |
| --- | --- | --- |
| `draft_run` | 测试可能有费用或外部副作用 | 副作用、输入、文件、允许次数 |
| `destructive_change` | 删除、广泛重连、敏感配置或 Agent 工具绑定等高风险变化 | 影响范围和恢复办法 |
| `commit` | 校验通过且有可提交 Workspace | 业务 Diff、技术 Diff、风险、版本和 Hash |

审批绑定：

```text
run_id
workspace_version_id
base_hash
action
risk
expires_at
```

因此不要把“批准过一次”理解为对后续任何版本的授权。新 Patch、新 Repair
或 Undo 都可能产生新版本，并使旧审批失效。

## 9. 暂停、恢复、取消与撤销

### 9.1 暂停和恢复

- `waiting_user`：在输入框补充信息，继续同一个 Run。
- `paused`：点击“继续”从检查点恢复。
- `interrupted`：重启或可重试 Provider 尝试耗尽后，点击“继续”恢复。

只有当所有 Provider 失败都是网络、408/425/429 或 5xx 等可重试错误，且
模型调用预算仍有剩余时，Provider 尝试耗尽才进入 `interrupted`。认证或
请求 4xx、决策协议错误、预算耗尽仍是终态失败。

恢复不会自动重放已经开始的 Commit、Draft Test 或其他副作用操作；遇到
外部结果不明确时先人工核对。

### 9.2 撤销

- Commit 前：移动 Workspace head 到父版本，并使旧审批失效。
- Workflow/Chatflow Commit 后：创建新的补偿 Run/预览，重新审阅和批准
  后才能写回。
- 配置型应用 Commit 后：v4.0.0 没有自动补偿 Undo。

关闭 Feature Flag 也不会撤销已经完成的 Dify 写入。

## 10. 常见错误与处理

| 错误码或提示 | 含义 | 处理 |
| --- | --- | --- |
| `AGENT_V4_DISABLED` | v4 Feature Flag 未开启 | 设置为 `true` 并重启 sidecar |
| `DIFY_VERSION_MUTATION_UNSUPPORTED` | Dify/DSL 不在写入兼容矩阵 | 保持 v3 路径可用，并升级到已验证的 Dify/DSL 组合 |
| `WORKSPACE_VERSION_MISMATCH` | Patch 指向旧 head | 读取当前 head，重新生成 Patch |
| `CONFIG_PATCH_PRECONDITION_FAILED` | 配置字段自检查后已变化 | 重新 Inspect 和审阅 |
| `APPROVAL_WORKSPACE_VERSION_MISMATCH` | 审批属于旧 Workspace 版本 | 为当前可见版本重新申请审批 |
| `DIFY_DRAFT_HASH_CONFLICT` | Workflow/Chatflow 草稿已变化 | 重新创建 Snapshot/Run |
| `DIFY_MODEL_CONFIG_HASH_CONFLICT` | 配置型应用已变化 | 重新创建配置修改 Run |
| `COMMIT_REQUIRES_VALIDATED_HEAD` | 当前 head 未通过校验 | Repair 后重新校验，不能跳过 |
| `DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED` | Dify 不能执行候选 Graph | 批准 Commit 后新开显式运行 |
| `DRAFT_TEST_FILE_REQUIRED` | 测试缺少真实文件 | 提供用户文件或已批准 fixture |
| 画布有未同步变更 | dirty state 阻止 Commit | 回到 Dify 保存/同步，再重新建立上下文 |
| Provider 429/5xx/超时 | 临时限流或服务异常 | 等待后从 `interrupted` 继续，或配置稳定 fallback |
| Provider 401/403 | key、权限或请求契约有误 | 修复配置；这类错误不会无限重试 |

排查时优先查看 Run 的 `error.code`、当前 Diff、最近事件和 Workspace
版本。不要把原始 Authorization、Cookie、环境变量值或未脱敏 Dify 响应
复制到问题单或外部模型。

## 11. 安全使用清单

提交前确认：

- [ ] Diff 只包含目标范围内的节点、字段、连线和变量。
- [ ] 确定性校验已通过。
- [ ] Dify 画布没有未同步改动，Hash 与 Run 基准一致。
- [ ] 审批显示的 Workspace 版本就是当前可见版本。
- [ ] Draft Test 的输入、文件、次数和副作用都可接受。
- [ ] 数据集、工具、Agent Strategy 和 Trigger 都来自 Dify 已安装资源，
      不是模型猜出的 provider ID。
- [ ] 没有在 Prompt、Trace、截图或工单中暴露凭据和敏感执行数据。
- [ ] 需要发布时，已转到独立显式发布流程。

工作流 Prompt、代码、插件元数据、数据集内容、HTTP 响应和执行错误都应视为
不可信数据，而不是给 Builder Agent 的系统指令。

## 12. 回滚 v4

1. 停止接收新的 v4 工作。
2. 让无副作用操作完成，或显式暂停/取消；不要自动重试正在进行的 Commit
   或 Draft Test。
3. 设置：

   ```env
   CHAT2DIFY_AGENT_V4_ENABLED=false
   ```

4. 重启 sidecar。
5. 验证 v4 API 返回 `AGENT_V4_DISABLED`，继续使用 v3。

`agent_*` 表可以保留用于审计和以后重新启用；不需要做 schema downgrade。
如果已批准写入 Dify，关闭 Feature Flag 不会回滚该草稿。

## 13. API 快速参考

UI 已封装正常使用所需调用。集成方可使用：

| 方法 | 路径 |
| --- | --- |
| `POST` | `/api/v4/agent/sessions` |
| `GET` | `/api/v4/agent/sessions/{session_id}` |
| `POST` | `/api/v4/agent/sessions/{session_id}/messages` |
| `GET` | `/api/v4/agent/runs/{run_id}` |
| `GET` | `/api/v4/agent/runs/{run_id}/diff` |
| `GET` | `/api/v4/agent/runs/{run_id}/events` |
| `POST` | `/api/v4/agent/runs/{run_id}/context` |
| `GET` | `/api/v4/agent/runs/{run_id}/approvals` |
| `POST` | `/api/v4/agent/runs/{run_id}/approvals/{approval_id}` |
| `POST` | `/api/v4/agent/runs/{run_id}/commit` |
| `POST` | `/api/v4/agent/runs/{run_id}/pause` |
| `POST` | `/api/v4/agent/runs/{run_id}/resume` |
| `POST` | `/api/v4/agent/runs/{run_id}/undo` |
| `POST` | `/api/v4/agent/runs/{run_id}/cancel` |

SSE 支持 `Last-Event-ID` 和 `after_seq`，事件序号在每个 Run 内严格递增。
任何写入集成都必须提交服务端持久化的 `workspace_version_id` 和
`approval_id`，不能上传任意替代 Plan。

## 14. 延伸文档

- [README](../README.md)：项目入口、配置、部署和 API 总览
- [Builder Agent 运维指南](agent-v4-operations.md)：保留、备份、恢复、评测和扩展
- [v3 → v4 迁移与回滚](migration-v4.md)
- [Dify 兼容矩阵](compatibility/dify-v4.md)
- [v4 架构与实现方案](architecture/v4-agent-architecture-and-implementation-plan.md)
- [v4 开发任务与 Release Gate 证据](tasks.md)
