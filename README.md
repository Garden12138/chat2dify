# chat2dify

chat2dify 是一个独立的 FastAPI 组件，用自然语言对话创建、修改、测试运行和发布 Dify 应用。v3.0.0 开始，它可以作为 Dify Console 中的内嵌面板随 Dify docker compose 一起启动，也可以继续独立运行。

运行时仍然是独立 sidecar：chat2dify 连接本地或局域网内的 Dify Console API，把用户意图转换成可审阅的操作，再由后台任务执行。Dify web 只需要一个轻量适配层来放置抽屉入口和 iframe。核心入口是 `POST /api/assistant/plan` 和 `POST /api/assistant/execute`。

## 当前版本

`v3.0.0` 的主要变化：

- Dify 面板模式：通过 compose overlay 挂载到 Dify nginx 的 `/chat2dify/` 路径。
- Dify Console 内嵌入口：应用创建卡片可打开 Chat2Dify 创建面板，Workflow 画布顶部可打开当前实例修改面板。
- Dify web 构建覆盖：compose overlay 可构建带内嵌入口的 `web` 镜像，并和 `chat2dify`、`nginx` 一起启动。
- 独立组件部署：提供 Dockerfile、Dify compose overlay 和独立任务数据卷。
- 子路径兼容：前端静态资源和 API 请求支持 `CHAT2DIFY_PUBLIC_BASE_PATH`。
- 嵌入上下文兼容：`embed=1` 会隐藏 chat2dify 侧边栏，`intent=create|modify` 会带入创建类型或当前 app 上下文。
- 面板 manifest：`GET /api/panel/manifest` 暴露组件版本、挂载路径和 API 前缀。
- 版本统一：Python 包、FastAPI 应用和健康检查统一为 `3.0.0`。

继承自 `v2.0.0` 的能力：

- 单对话框 Web UI：创建、修改、运行、发布都从同一个输入框发起。
- 先规划再确认：助手会生成待确认操作卡片，用户点击确认后才提交后台任务。
- 自动补全上下文：创建成功后会记住当前 app，后续可以直接说“运行这个工作流”或“把回复改得更专业”。
- 中文交互优化：缺失信息、任务阶段、失败详情会优先用中文展示。
- 后台任务持久化：创建、修改、运行、发布都通过任务队列执行，浏览器刷新后仍能查状态。
- Dify 草稿安全写回：修改先生成预览，应用时带 hash 校验和风险保护。
- 多应用类型：支持 Workflow、Chatflow、Chatbot、Agent、Completion。

## 截图

chat2dify Dify 风格对话工作台，对话中完成创建、运行、修改和应用工作流：

![chat2dify Dify 风格对话工作台](docs/images/chat2dify-v2-chat-workbench.png)

通过 chat2dify 生成的 Dify 工作流，示例为“电脑城售后服务工作流”：

![Dify 电脑城售后服务工作流](docs/images/dify-v2-support-workflow-professional.png)

同一个 Dify 工作流修改提示词后的运行效果示例：

![Dify 售后工作流语气修改示例](docs/images/dify-v2-support-workflow-angry-customer.png)

## 工作方式

典型流程如下：

```text
用户自然语言
  -> /api/assistant/plan
  -> 缺信息则继续追问
  -> 生成待确认操作卡片
  -> 用户确认
  -> /api/assistant/execute
  -> 后台任务执行 create / modify / run / publish
  -> 返回 Dify app_id、草稿 hash、运行结果或错误详情
```

对话助手本身不直接让 LLM 任意调用 Dify。它会把用户意图归类为明确操作，并构造结构化 payload。每个高风险或写入操作都需要用户确认。

## 系统架构图

![chat2dify 系统架构图](docs/images/chat2dify-v2-system-architecture.svg)

图中蓝色实线表示主要请求和执行链路，灰色虚线表示配置、本地存储和版本依赖。

## 能做什么

### 创建应用

可以直接说：

```text
创建一个电脑城售后服务工作流，用户描述电脑问题后，生成专业售后回复。
```

如果缺少应用名称或需求描述，助手会继续追问。确认后会调用后台创建任务，把生成的 DSL 导入 Dify。

### 修改草稿

可以基于当前应用继续说：

```text
把回复改得更专业一点，先安抚用户，再给排查步骤。
```

修改分两步：

1. 生成修改预览，不写回 Dify。
2. 用户确认“应用”后，用预览中的 Plan IR 写回 Dify 草稿。

默认安全模式会阻止大规模删除节点、重写入口/出口、广泛改线等高风险变更。确实需要破坏性重构时，API 可显式传 `allow_destructive=true`。

### 测试运行

创建或修改后，可以继续输入：

```text
运行这个工作流，输入：为什么台式机突然黑屏开不了机？
```

Workflow 会把自然语言测试文本映射到 `inputs.query`；Chatflow、Chatbot、Agent、Completion 使用各自的 draft run/chat API。

### 发布和触发器

普通创建和修改只更新 Dify 草稿，不会自动发布。发布必须显式执行，并带当前草稿 hash。Webhook、Schedule、Plugin Trigger 工作流需要发布后通过对应触发方式运行，不能用普通 Draft Run 代替。

## 支持的应用类型

| Dify 类型 | `app_mode` | 说明 |
| --- | --- | --- |
| Workflow | `workflow` | 普通工作流，使用 `start -> ... -> end` |
| Chatflow | `advanced-chat` | 对话流，使用 `start -> ... -> answer`，支持多轮上下文 |
| Chatbot | `chat` | Dify 基础聊天助手配置 |
| Agent | `agent-chat` | Dify Agent 应用配置 |
| Completion | `completion` | 文本生成应用配置 |

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

chat2dify 默认假设 Dify 源码仓库在当前仓库的同级目录：

```text
../dify
../chat2dify
```

启动时会读取 `DIFY_SOURCE_DIR` 下的 Dify DSL 版本信息，因此该目录必须存在。

## 配置

`.env` 从仓库根目录读取，真实环境变量会覆盖 `.env`。

最小可用配置：

```env
DIFY_SOURCE_DIR=../dify
DIFY_CONSOLE_API_BASE=http://127.0.0.1/console/api
DIFY_CONSOLE_WEB_BASE=http://127.0.0.1

DIFY_EMAIL=you@example.com
DIFY_PASSWORD=your-password
DIFY_LOGIN_LANGUAGE=en-US

DIFY_DEFAULT_MODEL_PROVIDER=langgenius/openai/openai
DIFY_DEFAULT_MODEL_NAME=gpt-4o-mini

PLANNER_DEFAULT_PROVIDER=nvidia
PLANNER_FALLBACK_PROVIDERS=openrouter,openai
PLANNER_TIMEOUT_SECONDS=600
PLANNER_REQUEST_RETRIES=2
NVIDIA_API_KEY=nvapi-...
NVIDIA_MODEL=deepseek-ai/deepseek-v4-flash
NVIDIA_THINKING=false
NVIDIA_REASONING_EFFORT=low
NVIDIA_MAX_TOKENS=8192

OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free

CHAT2DIFY_TASK_DB=data/tasks.sqlite3
CHAT2DIFY_TASK_WORKERS=2
```

重要配置说明：

- `DIFY_CONSOLE_API_BASE`：Dify Console API 地址。使用 Dify docker compose 时，通常走 nginx，即 `http://127.0.0.1/console/api`。
- `DIFY_CONSOLE_WEB_BASE`：返回给用户的 Dify 控制台链接前缀。
- `CHAT2DIFY_PUBLIC_BASE_PATH`：浏览器访问 chat2dify 的公开子路径。独立运行留空；挂到 Dify 面板时设为 `/chat2dify`。
- `DIFY_EMAIL` / `DIFY_PASSWORD`：用于导入应用、读取草稿、运行草稿和发布。
- `DIFY_DEFAULT_MODEL_PROVIDER` / `DIFY_DEFAULT_MODEL_NAME`：生成到 Dify LLM 节点里的运行模型，不是 chat2dify Planner。
- `PLANNER_*`、`NVIDIA_*`、`OPENROUTER_*`、`OPENAI_*`：chat2dify 用来生成或修改 Plan IR 的规划模型。
- `DIFY_DEFAULT_DATASET_IDS`：可选，生成知识检索节点时使用的默认数据集 ID，多个 ID 用逗号分隔。

如果没有配置任何 Planner key，创建草稿会退化为简单确定性模板；修改预览仍需要至少一个可用 Planner。

## 运行

独立运行：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000/
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## Dify 面板部署

v3.0.0 提供 Dify docker compose overlay。默认假设 Dify 和 chat2dify 是同级目录：

```text
../dify
../chat2dify
```

集成后有两个入口层级：

- Dify Console 内嵌入口：在 Studio 创建应用卡片中点击 `Chat2Dify 创建`，或进入 Workflow 画布后点击顶部 `Chat2Dify`。
- 子路径直接入口：继续保留 `http://localhost/chat2dify/`，便于调试和独立使用。

Dify web 中的内嵌入口只是抽屉和 iframe 适配层；真正的创建、修改、运行和发布仍由 `chat2dify` sidecar 执行。

在 `dify/docker/.env` 中补充：

```env
CHAT2DIFY_PUBLIC_BASE_PATH=/chat2dify
CHAT2DIFY_DIFY_EMAIL=you@example.com
CHAT2DIFY_DIFY_PASSWORD=your-password
CHAT2DIFY_NVIDIA_API_KEY=nvapi-...
```

从 `dify/docker` 启动：

```bash
docker compose \
  -f docker-compose.yaml \
  -f ../../chat2dify/deploy/dify/docker-compose.chat2dify.yaml \
  up -d --build web chat2dify nginx
```

打开 Dify：

```text
http://localhost/apps
```

打开 chat2dify 直接入口：

```text
http://localhost/chat2dify/
```

内嵌面板使用的 URL 形态：

```text
/chat2dify/?embed=1&intent=create&app_mode=workflow
/chat2dify/?embed=1&intent=modify&app_id=<app_id>&app_mode=workflow&app_name=<name>
```

`embed=1` 会隐藏 chat2dify 自身侧边栏；`intent=create` 会把 `app_mode` 作为创建类型上下文；`intent=modify` 会把当前 app 记入对话上下文，后续可以直接说“把回复改得更专业”。

详细说明见 [Dify Compose Deployment](docs/deployment/dify-compose.md)。

## Web UI 使用建议

推荐把信息直接写完整：

```text
创建一个电脑城售后服务工作流。名称叫电脑城售后服务工作流。
用户输入售后问题后，先安抚，再给排查步骤，最后建议联系门店或维修服务。
```

创建后继续在同一对话里操作：

```text
运行这个工作流，输入：电脑突然黑屏开不了机了
```

```text
修改这个工作流，回复要更简洁，先问电源和显示器，再问是否听到风扇声
```

```text
应用刚才的修改
```

## API 概览

Web UI 使用这些助手 API：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/panel/manifest` | 返回 Dify 面板组件元数据 |
| `POST` | `/api/assistant/plan` | 把自然语言转成缺信息提示或待确认操作 |
| `POST` | `/api/assistant/execute` | 提交用户已确认的操作，返回 `task_id` |
| `GET` | `/api/tasks/{task_id}` | 查询后台任务 |
| `POST` | `/api/tasks/{task_id}/cancel` | 请求取消后台任务 |

保留的直接工作流 API：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/workflows/draft` | 只生成 DSL 和 Plan IR，不导入 Dify |
| `POST` | `/api/workflows/create` | 创建并导入 Dify 应用 |
| `GET` | `/api/workflows/{app_id}/draft` | 读取当前 Dify 草稿并反编译为 Plan IR |
| `POST` | `/api/workflows/modify/draft` | 生成修改预览 |
| `POST` | `/api/workflows/modify/apply` | 应用已审阅的修改 |
| `POST` | `/api/workflows/run/draft` | 运行 Workflow 草稿 |
| `POST` | `/api/chatflows/run/draft` | 运行 Chatflow 草稿 |
| `POST` | `/api/chatbots/run/draft` | 运行 Chatbot 草稿 |
| `POST` | `/api/completions/run/draft` | 运行 Completion 草稿 |
| `POST` | `/api/agents/run/draft` | 运行 Agent 草稿 |
| `POST` | `/api/workflows/{app_id}/publish` | 发布草稿 |

后台任务 API 与主要写入/运行操作一一对应，例如：

```text
POST /api/tasks/workflows/create
POST /api/tasks/workflows/modify/draft
POST /api/tasks/workflows/modify/apply
POST /api/tasks/workflows/run/draft
POST /api/tasks/chatflows/run/draft
POST /api/tasks/workflows/publish
```

资源选择 API：

```text
GET /api/planner/providers
GET /api/dify/datasets
GET /api/dify/models
GET /api/dify/tools
GET /api/dify/agent-strategies
GET /api/dify/trigger-providers
GET /api/dify/trigger-subscriptions
```

这些接口只读取 Dify 已安装、已配置的资源。chat2dify 不安装插件，不写凭据，也不允许 Planner 猜测 provider ID。

## Plan IR 和节点支持

Plan IR 是 chat2dify 的中间表示。Planner 只负责生成结构化计划；chat2dify 会执行规范化、修复、校验、编译、反编译和 Dify DSL 预检。

当前支持的主要节点：

```text
start, llm, code, if-else, end, answer, http-request,
template-transform, question-classifier, parameter-extractor,
variable-aggregator, document-extractor, list-operator,
knowledge-retrieval, human-input, iteration, loop, assigner,
tool, agent, trigger-webhook, trigger-plugin, trigger-schedule
```

范围说明：

- Workflow 使用 `end`，Chatflow 使用 `answer`。
- Chatflow 会读取 `sys.query` 和 `sys.files`，并保留 Dify 对话记忆语义。
- `knowledge-retrieval` 只能使用请求选择或环境变量配置的数据集 ID。
- `tool` 只能使用 Dify 已安装并由用户选择的工具。
- `agent` 只能使用 Dify 已安装并由用户选择的 Agent Strategy。
- Trigger 工作流必须显式选择 Webhook、Schedule 或 Plugin Trigger 元数据。
- 复杂旧草稿可以尽量读取和保留；超出支持范围的节点会作为外部依赖或原始结构处理，无法保证可生成新的同类节点。

## 任务和错误处理

后台任务存储在 SQLite：

```env
CHAT2DIFY_TASK_DB=data/tasks.sqlite3
CHAT2DIFY_TASK_WORKERS=2
```

任务状态包括 `queued`、`running`、`succeeded`、`failed`、`cancelled`、`interrupted`。服务重启时未完成任务会标记为 `interrupted`。完成记录默认保留 7 天，最多保留 200 条。

Planner 请求会按 provider 做网络重试和 fallback。NVIDIA 默认使用流式请求；OpenRouter 和 OpenAI-compatible provider 可作为 fallback。若最终失败，后台任务会保留结构化错误详情，Web UI 会尽量显示中文原因，例如 API Key 超限、请求过频、超时或认证失败。

## 开发

运行测试：

```bash
pytest
```

检查前端脚本语法：

```bash
node --check app/static/app.js
```

项目结构：

```text
app/
  main.py              FastAPI 路由、任务入口和 Dify 操作编排
  assistant.py         对话助手的确定性意图解析和待确认操作生成
  agent/               Planner、编辑器、规范化、差异和保护逻辑
  compiler/            Plan IR 到 Dify DSL 的编译
  dify/                Dify Console API client、graph 适配和预检
  static/              v3 Dify 面板 Web UI
deploy/dify/           Dify docker compose overlay 和 nginx 面板路由
docker/                chat2dify 容器镜像
tests/                 pytest 覆盖 create / modify / run / assistant / client
docs/images/           README 截图
../dify/web/app/components/chat2dify/
                       Dify Console 抽屉面板和入口按钮适配层
```

## 注意事项

- chat2dify 是独立组件，不是 Dify 插件；运行时通过 sidecar 服务访问 Dify Console API。
- Dify 内嵌模式需要 Dify web 的轻量 UI 适配层，用来放置创建入口、Workflow 入口和 iframe 面板。
- Dify 面板模式同时支持 Console 抽屉入口和 nginx 子路径入口；默认子路径是 `/chat2dify/`。
- 创建和修改默认只操作 Dify 草稿；发布需要显式确认。
- 浏览器端只保存会话上下文和待确认操作，API key 保留在服务端。
- 运行 Dify docker compose 时，优先使用 nginx 暴露的 `/console/api` 地址，而不是容器内部的 `5001`。
- README 中的截图来自 Dify 风格 Web UI 和 Dify 生成结果。
