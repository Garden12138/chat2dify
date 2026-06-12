# chat2dify

Generate Dify Workflows and Chatflows via Natural Language Conversation.

## v1.1.0

Chatflow now supports the same reviewed modification and explicit publishing
flow as Workflow. Existing `advanced-chat` drafts can be loaded, revised through
Modify Preview, applied with draft hash protection, run across multiple
conversation turns, and published without introducing Workflow triggers.

## Phase 1 MVP

This repository runs as an independent FastAPI sidecar. It does not modify Dify
source code and targets the sibling latest Dify checkout:

```env
DIFY_SOURCE_DIR=../dify
DIFY_CONSOLE_API_BASE=http://127.0.0.1/console/api
DIFY_CONSOLE_WEB_BASE=http://127.0.0.1
DIFY_DEFAULT_DATASET_IDS=
```

`DIFY_SOURCE_DIR` is resolved relative to the `chat2dify` repository root. On
startup the sidecar verifies that the directory exists and that
`api/constants/dsl_version.py` can be read. The app DSL version is read from
that file at runtime instead of being hardcoded.

When running Dify through `../dify/docker/docker-compose.yaml`, the API service
listens on `5001` inside the Docker network but is not published directly to the
host. Use the nginx route instead:

```env
DIFY_CONSOLE_API_BASE=http://127.0.0.1/console/api
DIFY_CONSOLE_WEB_BASE=http://127.0.0.1
```

The second-stage flow is:

```text
user request -> raw LLM plan -> normalized WorkflowPlan IR -> Dify DSL YAML -> validation -> /console/api/apps/imports
```

The create API returns the imported Dify `app_id` and a console workflow URL in
the form `/app/{app_id}/workflow`.

Chatflow creation uses the same draft/create APIs with
`"app_mode":"advanced-chat"`. The generated graph uses
`start -> ... -> answer`, reads the current user message from
`{{#sys.query#}}`, and enables a 10-message LLM memory window.

Draft/create responses include `raw_plan`, normalized `plan`, rule-based
`explanation`, `planner` metadata, `dsl`, and structured validation issues.

## Screenshots

Web UI workbench for create, modify, and draft run:

![chat2dify Web UI workbench](docs/images/webui-workbench-run.png)

Dify draft workflow generated from the repair after-sales example:

![Dify workflow canvas](docs/images/dify-workflow-canvas.png)

The third-stage edit flow modifies an existing Dify Workflow or Chatflow draft
in place:

```text
app_id + edit request -> Dify draft graph -> WorkflowPlan IR -> revised WorkflowPlan IR -> validation -> /console/api/apps/{app_id}/workflows/draft
```

Use `POST /api/workflows/modify/draft` to preview a modification and
`POST /api/workflows/modify/apply` to write it back to the Dify draft. Both
accept `app_id`, `message`, and optional `expected_hash`; apply returns the new
Dify draft `hash`. Third-stage edits support the stabilized node set listed
below and do not publish the app. Chatflow edits preserve `advanced-chat`,
conversational `start`, `answer`, `sys.query`, and LLM memory semantics. Edits
run in safe mode by default: large node deletions, start/end/answer rewrites, or
broad edge rewiring are reported in `guard` and blocked on apply unless
`allow_destructive=true` is sent. No-op edits return `sync.result="noop"` and
are not written back to Dify.

Use `GET /api/workflows/{app_id}/draft` to inspect the current Dify draft
without calling an LLM or writing anything back. The response includes the
current draft hash, Dify app metadata when available, the decompiled Plan IR,
and validation diagnostics.

The fourth-stage validation flow runs an existing Dify workflow draft with
explicit test inputs and returns a blocking summary:

```text
app_id + inputs -> Dify draft run SSE -> terminal event -> run summary
```

Use `POST /api/workflows/run/draft` with `app_id` and `inputs`. The sidecar
does not generate test inputs, does not publish the workflow, and does not
assume an OpenAI provider; generated workflows use the configured
`DIFY_DEFAULT_MODEL_PROVIDER` / `DIFY_DEFAULT_MODEL_NAME` values, such as
Tongyi/Qwen.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

The sidecar reads `.env` from the repository root and lets real environment
variables override file values. Fill these values before using
`/api/workflows/create`:

```env
DIFY_EMAIL=you@example.com
DIFY_PASSWORD=your-password
```

The Planner Model panel chooses the LLM used by chat2dify to generate or revise
Plan IR. This is separate from `DIFY_DEFAULT_MODEL_PROVIDER` /
`DIFY_DEFAULT_MODEL_NAME`, which configure LLM nodes inside the generated Dify
workflow. API keys stay on the server and are never returned to or stored by
the browser.

OpenAI-compatible planner configuration:

```env
PLANNER_DEFAULT_PROVIDER=openai
PLANNER_TIMEOUT_SECONDS=300
PLANNER_REQUEST_RETRIES=2
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

NVIDIA NIM DeepSeek V4 Flash planner configuration:

```env
PLANNER_DEFAULT_PROVIDER=nvidia
PLANNER_TIMEOUT_SECONDS=300
PLANNER_REQUEST_RETRIES=2
NVIDIA_API_KEY=nvapi-...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=deepseek-ai/deepseek-v4-flash
NVIDIA_THINKING=false
NVIDIA_REASONING_EFFORT=low
NVIDIA_MAX_TOKENS=8192
```

If the default planner provider has no API key, the create draft endpoint uses
a deterministic fallback plan: `start -> llm -> end` for Workflow or
`start -> llm -> answer` for Chatflow. Modify Preview requires a configured
planner provider. When an LLM provider is configured, the planner tries up to
three attempts and feeds structured validation errors back into the model for
self-repair.

Knowledge retrieval workflows require real Dify dataset IDs. Configure a
comma-separated default in `.env`, or use the Web UI Knowledge panel to search
and select datasets from the local Dify workspace. Manual dataset IDs remain
available as an advanced fallback, and Web UI selections override the default
for Create and Modify requests:

```env
DIFY_DEFAULT_DATASET_IDS=dataset_id_1,dataset_id_2
```

Tool nodes require tools that are already installed and configured in Dify. Use
the Web UI Tools panel to search and select installed builtin, API, workflow, or
MCP tools. Only selected tools are exposed to the planner; chat2dify does not
install plugins, edit credentials, or let the LLM guess provider IDs. The Web UI
also lets you configure each selected tool's runtime inputs and form settings;
those explicit bindings are sent in `tool_selections[].tool_parameters` and
`tool_selections[].tool_configurations` and take precedence over LLM-generated
values.

Agent nodes require Agent Strategy plugins that are already installed and
configured in Dify. Use the Web UI Agents panel to search and select strategies,
then configure their required parameters. If a strategy parameter requires a
Tool, bind one of the tools already selected in the Tools panel. Only selected
agent strategies are exposed to the planner; chat2dify does not install plugins,
edit credentials, or create Agent Roster records.

## Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open the local Web UI:

```text
http://127.0.0.1:8000/
```

Create, Modify Preview/Apply, Run Draft, and Publish execute as persistent
background tasks in the Web UI. Each panel shows the current phase, elapsed
time, progress, and a cooperative Cancel action. Active task IDs are restored
after a browser refresh. Task records and results use SQLite by default:

```env
CHAT2DIFY_TASK_DB=data/tasks.sqlite3
CHAT2DIFY_TASK_WORKERS=2
```

Tasks interrupted by a sidecar restart are marked `interrupted`. Completed
records are retained for seven days, capped at 200 records. Existing blocking
workflow API endpoints remain available for compatible scripts and curl calls.
Cancelled, failed, and interrupted tasks cannot resume an in-flight LLM or Dify
HTTP request. Their panel offers Retry, which starts a new task from the
beginning using the saved request.

The Planner Model panel lists only server-registered providers and disables
providers whose API key is not configured. Create and Modify Preview send the
selected provider/model in an optional request field:

```json
{
  "planner": {
    "provider": "nvidia",
    "model": "deepseek-ai/deepseek-v4-flash"
  }
}
```

Modify Apply with a reviewed preview continues to use the preview plan and does
not call the selected Planner model a second time.

`PLANNER_TIMEOUT_SECONDS` controls how long chat2dify waits for a Planner
response. NVIDIA reasoning models can take longer than 60 seconds for complex
workflow plans, so the default is 300 seconds. `PLANNER_REQUEST_RETRIES`
retries transient disconnects, timeouts, rate limits, and temporary upstream
errors without consuming an additional Plan self-repair attempt.
NVIDIA Planner requests use streaming and default to `NVIDIA_THINKING=false`
for lower latency and fewer hosted-endpoint disconnects. Set it to `true` and
raise `NVIDIA_REASONING_EFFORT` only when a deployment can sustain longer
reasoning requests.

Recommended Web UI edit flow:

```text
Load draft -> Preview -> Apply reviewed preview
```

The Web UI applies the exact Plan IR returned by Preview. It does not ask the
LLM to generate a second modification during Apply.

Health check:

```bash
curl http://127.0.0.1:8000/health
```

List Dify datasets for the Web UI selector:

```bash
curl 'http://127.0.0.1:8000/api/dify/datasets?keyword=售后&page=1&limit=50'
```

List installed Dify tools for the Web UI selector:

```bash
curl 'http://127.0.0.1:8000/api/dify/tools?keyword=search&provider_type=all'
```

List installed Dify Agent Strategies for the Web UI selector:

```bash
curl 'http://127.0.0.1:8000/api/dify/agent-strategies?keyword=react'
```

Draft a workflow without importing it:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/draft \
  -H 'Content-Type: application/json' \
  -d '{"message":"Summarize the user input","app_name":"Summary MVP","dataset_ids":["OPTIONAL_DATASET_ID"]}'
```

Create or draft a workflow that may use a selected Dify tool by passing the
tool object returned by `/api/dify/tools`:

```json
{
  "message": "先调用所选搜索工具查询信息，再由 LLM 总结并返回 answer",
  "app_name": "Tool summary workflow",
  "tool_selections": [
    {
      "provider_id": "PROVIDER_ID_FROM_DIFY",
      "provider_type": "builtin",
      "provider_name": "provider_name",
      "tool_name": "tool_name",
      "tool_label": "Tool label",
      "parameters": [],
      "output_schema": {},
      "tool_parameters": {
        "query": {"type": "mixed", "value": "{{#start.query#}}"}
      },
      "tool_configurations": {}
    }
  ]
}
```

Create or draft a workflow that may use a selected Dify Agent Strategy by
passing the strategy object returned by `/api/dify/agent-strategies`:

```json
{
  "message": "用所选智能体分析客户问题并生成处理建议，最后返回 answer",
  "app_name": "Agent support workflow",
  "agent_selections": [
    {
      "agent_strategy_provider_name": "PROVIDER_FROM_DIFY",
      "agent_strategy_name": "STRATEGY_FROM_DIFY",
      "agent_strategy_label": "Strategy label",
      "parameters": [],
      "output_schema": {},
      "agent_parameters": {
        "query": {"type": "variable", "value": ["start", "query"]}
      },
      "plugin_unique_identifier": "PLUGIN_UNIQUE_IDENTIFIER_FROM_DIFY",
      "meta": {"version": "1.0.0"}
    }
  ]
}
```

Create a workflow in Dify:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/create \
  -H 'Content-Type: application/json' \
  -d '{"message":"Summarize the user input","app_name":"Summary MVP","dataset_ids":["OPTIONAL_DATASET_ID"]}'
```

Start the same create operation as a background task:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/workflows/create \
  -H 'Content-Type: application/json' \
  -d '{"message":"Summarize the user input","app_name":"Summary MVP"}'
```

Poll or cancel the returned `task_id`:

```bash
curl http://127.0.0.1:8000/api/tasks/TASK_ID
curl -X POST http://127.0.0.1:8000/api/tasks/TASK_ID/cancel
```

The corresponding background endpoints for the other Web UI operations are
`/api/tasks/workflows/modify/draft`,
`/api/tasks/workflows/modify/apply`, and
`/api/tasks/workflows/run/draft`. Explicit workflow publishing uses
`/api/tasks/workflows/publish`.

Create a Chatflow:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/create \
  -H 'Content-Type: application/json' \
  -d '{
    "app_mode":"advanced-chat",
    "app_name":"汽车售后多轮客服",
    "message":"创建汽车售后多轮客服。识别客户问题，礼貌追问缺失信息，记住最近对话，并通过 Answer 回复。"
  }'
```

Run the first Chatflow draft turn:

```bash
curl -X POST http://127.0.0.1:8000/api/chatflows/run/draft \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id":"YOUR_CHATFLOW_APP_ID",
    "query":"我的车刚保养完发动机抖动",
    "inputs":{},
    "timeout_seconds":120
  }'
```

For the next turn, pass the `conversation_id` and `message_id` returned by the
previous response:

```bash
curl -X POST http://127.0.0.1:8000/api/chatflows/run/draft \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id":"YOUR_CHATFLOW_APP_ID",
    "query":"我刚才说的故障是什么？",
    "inputs":{},
    "conversation_id":"PREVIOUS_CONVERSATION_ID",
    "parent_message_id":"PREVIOUS_MESSAGE_ID",
    "timeout_seconds":120
  }'
```

The background equivalent is `POST /api/tasks/chatflows/run/draft`. Use the
same `/api/workflows/modify/draft`, `/api/workflows/modify/apply`, and
`/api/workflows/{app_id}/publish` endpoints for Chatflow modification and
publishing. Chatflow modification rejects Workflow trigger selections.

Create a POST Webhook workflow by selecting Webhook in the Web UI Trigger
panel, or by passing a structured selection:

```json
{
  "message": "接收售后请求，根据 query 生成客服回复并返回 answer",
  "app_name": "Webhook 售后受理",
  "trigger_selection": {
    "type": "webhook",
    "method": "POST",
    "content_type": "application/json",
    "body": [
      {"name": "query", "type": "string", "required": true}
    ],
    "status_code": 200,
    "response_body": "{\"accepted\":true}",
    "timeout": 30
  }
}
```

Create a daily schedule workflow:

```json
{
  "message": "每天汇总售后记录并返回 answer，不引用 start.query",
  "app_name": "每日售后汇总",
  "trigger_selection": {
    "type": "schedule",
    "mode": "visual",
    "frequency": "daily",
    "visual_config": {
      "time": "09:00 AM",
      "weekdays": ["mon"],
      "on_minute": 0,
      "monthly_days": [1]
    },
    "timezone": "Asia/Shanghai"
  }
}
```

Discover installed Plugin Trigger events and their existing subscriptions:

```bash
curl 'http://127.0.0.1:8000/api/dify/trigger-providers?keyword=github'
curl 'http://127.0.0.1:8000/api/dify/trigger-subscriptions?provider_id=langgenius/github/github'
```

Create a Plugin Trigger workflow by selecting the provider event and an
existing subscription in the Web UI Trigger panel, or by passing only the
selected identifiers and constant event parameters:

```json
{
  "message": "收到新的售后工单事件后，分析事件中的 title 和 description，生成处理建议并返回 answer",
  "app_name": "插件事件售后分析",
  "trigger_selection": {
    "type": "plugin",
    "provider_id": "INSTALLED_PROVIDER_ID",
    "event_name": "INSTALLED_EVENT_NAME",
    "subscription_id": "EXISTING_SUBSCRIPTION_ID",
    "event_parameters": {
      "scope": {"type": "constant", "value": "after-sales"}
    }
  }
}
```

chat2dify resolves provider, plugin, event schema, and output schema metadata
again from Dify before planning. It does not accept guessed plugin identifiers,
create subscriptions, or handle Trigger Provider credentials.

Create and Modify only update the Dify draft. Publishing is always explicit:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/YOUR_APP_ID/publish \
  -H 'Content-Type: application/json' \
  -d '{"expected_hash":"CURRENT_DRAFT_HASH","marked_name":"v1","marked_comment":"Enable trigger"}'

curl http://127.0.0.1:8000/api/workflows/YOUR_APP_ID/triggers

curl -X POST http://127.0.0.1:8000/api/workflows/YOUR_APP_ID/triggers/TRIGGER_ID/status \
  -H 'Content-Type: application/json' \
  -d '{"enabled":false}'

curl 'http://127.0.0.1:8000/api/workflows/YOUR_APP_ID/triggers/webhook?node_id=WEBHOOK_NODE_ID'
```

Regular Draft Run is for workflows with a `start` entry. Trigger workflows
must be published and invoked through the returned Webhook URL or schedule.

Preview a change to an existing Dify workflow draft:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/modify/draft \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"YOUR_APP_ID","message":"Make the final answer warmer","dataset_ids":["OPTIONAL_DATASET_ID"]}'
```

Inspect the current Dify workflow draft without modifying it:

```bash
curl http://127.0.0.1:8000/api/workflows/YOUR_APP_ID/draft
```

Apply the change to the Dify draft:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/modify/apply \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"YOUR_APP_ID","message":"Make the final answer warmer","expected_hash":"OPTIONAL_CURRENT_HASH"}'
```

Apply a reviewed preview plan without re-running the edit planner. The `plan`
value should be the exact `plan` object returned by `modify/draft`:

```json
{
  "app_id": "YOUR_APP_ID",
  "message": "Make the final answer warmer",
  "expected_hash": "PREVIEW_BASE_HASH",
  "dataset_ids": ["OPTIONAL_DATASET_ID"],
  "plan": {
    "...": "copy the full preview plan object here"
  }
}
```

Allow a confirmed destructive rewrite:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/modify/apply \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"YOUR_APP_ID","message":"Rebuild this draft into a simpler workflow","allow_destructive":true}'
```

Run a Dify workflow draft with explicit inputs:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/run/draft \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"YOUR_APP_ID","inputs":{"query":"我要投诉订单配送太慢"},"timeout_seconds":120}'
```

## Supported Nodes

The current Plan IR supports these node types:

```text
start, llm, code, if-else, end, http-request, template-transform,
question-classifier, parameter-extractor, variable-aggregator,
document-extractor, assigner, list-operator, knowledge-retrieval,
human-input, iteration, iteration-start, loop, loop-start, loop-end,
tool, agent, datasource, datasource-empty, knowledge-index,
trigger-webhook, trigger-plugin, trigger-schedule, answer
```

`answer` is only valid in `advanced-chat` mode. Workflow mode continues to use
`end`; Chatflow requires at least one `answer`, rejects `end` and workflow
triggers, and reads the current message from `sys.query`.

`question-classifier` is used for semantic routing such as complaint /
consultation / appointment branches. `parameter-extractor` is used to extract
structured fields such as `order_id`, `car_model`, `store`, and `issue` from
the user input. `document-extractor` can extract text from uploaded files,
`list-operator` can filter/sort/limit arrays, and `variable-aggregator` can
merge fallback variables into one output. `assigner` is supported for existing
draft compatibility but is not generated by default for new workflow requests.
`knowledge-retrieval` retrieves context from dataset IDs selected in the Web UI
or configured in `DIFY_DEFAULT_DATASET_IDS`; `answer` remains out of scope for
workflow mode. `human-input` is available for explicit manual review or approval
steps; draft runs that reach it return `status=paused`, and the human action is
still completed in the Dify UI in this stage. `iteration` is used for explicit
batch/list traversal, while `loop` is used for explicit retry/repeat/until or
max-N-times flows. `iteration-start`, `loop-start`, and `loop-end` are internal
Dify graph children generated inside their parent container, not ordinary
top-level business nodes.
`tool` can be generated when the request includes explicit `tool_selections`
from the Web UI/API and the user asks to call a tool. String tool inputs such as
`url`, `query`, and `text` are represented as Dify mixed text values like
`{{#start.query#}}`; boolean, number, and select settings use Dify ToolInput
constant/variable structures. Existing `_raw_data` tool nodes are still
preserved as passthrough for draft compatibility. `agent` can be generated when
the request includes explicit `agent_selections` from the Web UI/API and the
user explicitly asks for an Agent/智能体/autonomous multi-step flow. Agent
parameters use the same Dify `{type,value}` input structure, and Agent
tool-selector parameters must bind tools selected in the Web UI Tools panel.
Existing `_raw_data` agent nodes are still preserved as passthrough for old
draft compatibility. `trigger-webhook`, `trigger-plugin`, and
`trigger-schedule` are structured entry nodes that can be configured in the Web
UI, generated only from an explicit `trigger_selection`, published explicitly,
and enabled or disabled after publication. Plugin Trigger creation only uses
Trigger Providers and subscriptions already configured in Dify; event
parameters are constant bindings and downstream references are restricted to
the selected event's output schema. Legacy `_raw_data` Plugin Trigger nodes
remain passthrough-compatible. `datasource`, `datasource-empty`, and
`knowledge-index` remain passthrough-only external dependency nodes.

Example file workflow request:

```text
创建维修单附件总结工作流。用户上传维修单文件，先提取文件文本，再总结车辆问题、维修建议和需要补充的信息，最后返回 answer。
```

Example list workflow request:

```text
创建售后记录筛选工作流。输入 items 是包含 records 数组的 JSON 对象，筛选投诉类记录并取第一条，然后生成客服回复，最后返回 answer。
```

Example knowledge workflow request:

```text
创建修车售后知识库问答工作流。输入 query 是客户售后问题，先从门店售后政策知识库检索相关资料，再用模型结合资料生成客服回复，最后返回 answer。
```

Example human review workflow request:

```text
创建售后人工审核工作流。输入 query 是客户售后诉求，先生成客服回复草稿，再交给经理人工审核；经理可以选择通过或驳回，通过时返回草稿，驳回时返回人工审核意见。
```

Example batch iteration workflow request:

```text
创建批量售后记录处理工作流。输入 items 是包含 records 数组的 JSON 对象，遍历每条售后记录，逐条生成处理建议，最后返回建议列表 answer。
```

Example retry loop workflow request:

```text
创建最多 3 次维修状态检查工作流。输入 query 是客户提供的维修单号和问题，循环检查处理状态，满足可回复条件或达到 3 次后生成最终回复，最后返回 answer。
```

Example selected tool workflow request:

```text
创建工具查询总结工作流。先调用我在 Web UI 勾选的搜索工具查询客户问题相关信息，再用模型总结查询结果并生成客服回复，最后返回 answer。
```

Example selected agent workflow request:

```text
创建智能体售后分析工作流。使用我在 Web UI 勾选的 Agent Strategy 对客户问题进行多步分析，必要时调用已绑定工具，最后生成处理建议并返回 answer。
```

Plugin installation, credential editing, automatic creation of data-source
nodes, Chatflow conversation-variable management, Agent Roster management, and
model capability registry sync remain out of scope for now.

## Test

```bash
python3 -m pytest
```
