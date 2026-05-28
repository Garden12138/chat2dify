# chat2dify

Generate Dify Workflows via Natural Language Conversation.

## Phase 1 MVP

This repository runs as an independent FastAPI sidecar. It does not modify Dify
source code and targets the sibling latest Dify checkout:

```env
DIFY_SOURCE_DIR=../dify
DIFY_CONSOLE_API_BASE=http://127.0.0.1/console/api
DIFY_CONSOLE_WEB_BASE=http://127.0.0.1
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

Draft/create responses include `raw_plan`, normalized `plan`, rule-based
`explanation`, `planner` metadata, `dsl`, and structured validation issues.

The third-stage edit flow modifies an existing Dify draft in place:

```text
app_id + edit request -> Dify draft graph -> WorkflowPlan IR -> revised WorkflowPlan IR -> validation -> /console/api/apps/{app_id}/workflows/draft
```

Use `POST /api/workflows/modify/draft` to preview a modification and
`POST /api/workflows/modify/apply` to write it back to the Dify draft. Both
accept `app_id`, `message`, and optional `expected_hash`; apply returns the new
Dify draft `hash`. Third-stage edits still support only the seven stabilized
node types and do not publish the workflow.

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

If `OPENAI_API_KEY` is not set, the draft endpoint uses a deterministic fallback
plan (`start -> llm -> end`) so the MVP can still produce a valid DSL. When it
is set, the planner tries up to three LLM attempts and feeds validation errors
back into the model for self-repair.

## Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Draft a workflow without importing it:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/draft \
  -H 'Content-Type: application/json' \
  -d '{"message":"Summarize the user input","app_name":"Summary MVP"}'
```

Create a workflow in Dify:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/create \
  -H 'Content-Type: application/json' \
  -d '{"message":"Summarize the user input","app_name":"Summary MVP"}'
```

Preview a change to an existing Dify workflow draft:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/modify/draft \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"YOUR_APP_ID","message":"Make the final answer warmer"}'
```

Apply the change to the Dify draft:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/modify/apply \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"YOUR_APP_ID","message":"Make the final answer warmer","expected_hash":"OPTIONAL_CURRENT_HASH"}'
```

Run a Dify workflow draft with explicit inputs:

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/run/draft \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"YOUR_APP_ID","inputs":{"query":"我要投诉订单配送太慢"},"timeout_seconds":120}'
```

## Supported Nodes

Phase 2 stabilizes these Plan IR node types:

```text
start, llm, code, if-else, end, http-request, template-transform
```

Current non-goals: patching an existing canvas, multi-turn incremental edits,
plugin registry sync, model capability registry, and a custom Dify frontend
panel.

## Test

```bash
python3 -m pytest
```
