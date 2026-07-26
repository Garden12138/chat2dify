# Dify Compose Deployment

chat2dify v4.0.0 can run as an independent component beside Dify, be exposed
inside the same nginx entry point at `/chat2dify/`, and be opened from embedded
Dify Console drawer entries.

The runtime sidecar stays independent. The Dify web app only needs a lightweight
adapter for drawer triggers and the iframe panel. The compose overlay adds:

- `web`: a local build override for a Dify web image that includes the embedded
  chat2dify drawer entries.
- `chat2dify`: the FastAPI component built from this repository.
- an nginx template override that routes `/chat2dify/` to the component.
- a `chat2dify_data` volume for task state.

This repository keeps a copy of the Dify web adapter under:

```text
deploy/dify/web-adapter/
```

It mirrors Dify's `web/` source layout. To copy it into a sibling Dify checkout:

```bash
rsync -av deploy/dify/web-adapter/web/ ../dify/web/
```

## Directory Layout

The default overlay expects sibling repositories:

```text
github/
  dify/
    docker/
      docker-compose.yaml
  chat2dify/
    deploy/dify/docker-compose.chat2dify.yaml
```

## Configure

Add chat2dify-specific variables to `dify/docker/.env`:

```env
CHAT2DIFY_PUBLIC_BASE_PATH=/chat2dify
CHAT2DIFY_DIFY_CONSOLE_WEB_BASE=http://localhost

CHAT2DIFY_DIFY_EMAIL=you@example.com
CHAT2DIFY_DIFY_PASSWORD=your-password
CHAT2DIFY_DIFY_LOGIN_LANGUAGE=en-US

CHAT2DIFY_DEFAULT_MODEL_PROVIDER=langgenius/openai/openai
CHAT2DIFY_DEFAULT_MODEL_NAME=gpt-4o-mini

CHAT2DIFY_PLANNER_DEFAULT_PROVIDER=nvidia
CHAT2DIFY_PLANNER_FALLBACK_PROVIDERS=openrouter,openai-compatible,openai
CHAT2DIFY_NVIDIA_API_KEY=nvapi-...

# Optional generic OpenAI-compatible Planner endpoint.
CHAT2DIFY_OPENAI_COMPATIBLE_API_KEY=sk-...
CHAT2DIFY_OPENAI_COMPATIBLE_BASE_URL=https://llm-gateway.example/v1
CHAT2DIFY_OPENAI_COMPATIBLE_MODEL=deepseek-chat
CHAT2DIFY_OPENAI_COMPATIBLE_LABEL=OpenAI-compatible
CHAT2DIFY_OPENAI_COMPATIBLE_RESPONSE_FORMAT=true
```

`CHAT2DIFY_DIFY_CONSOLE_API_BASE` defaults to `http://api:5001/console/api`,
which uses Dify's internal compose network. Override it only if your Dify API
service has a different name or route.

## Start

From `dify/docker`:

```bash
docker compose \
  -f docker-compose.yaml \
  -f ../../chat2dify/deploy/dify/docker-compose.chat2dify.yaml \
  up -d --build web chat2dify nginx
```

Open Dify Studio:

```text
http://localhost/apps
```

Embedded entries:

- `Chat2Dify 创建` in the Studio create-app card.
- `Chat2Dify` in the Workflow/Chatflow canvas header.
- A Chat2Dify Builder bar on existing Chatbot, Completion, and Agent
  configuration pages. These modes pass app identity only and do not create a
  graph/canvas context channel.

Open the direct sidecar route:

```text
http://localhost/chat2dify/
```

The embedded drawer uses URLs like:

```text
/chat2dify/?embed=1&intent=create&app_mode=workflow
/chat2dify/?embed=1&intent=modify&app_id=<app_id>&app_mode=workflow&app_name=<name>
/chat2dify/?embed=1&intent=modify&app_id=<app_id>&app_mode=chat&app_name=<name>
```

The component still exposes its standalone API under the mounted prefix:

```text
GET  /chat2dify/health
GET  /chat2dify/api/panel/manifest
POST /chat2dify/api/assistant/plan
POST /chat2dify/api/assistant/execute
```

## Notes

- Keep `CHAT2DIFY_PUBLIC_BASE_PATH` without a trailing slash.
- If you only need the direct `/chat2dify/` route and not Dify Console embedded
  entries, the `web` rebuild is optional. For embedded entries, rebuild `web`.
- The overlay mounts the Dify repository into the container at `/dify` as
  read-only so chat2dify can read the Dify package version from
  `api/pyproject.toml` and the current DSL version. Runtime version detection
  does not require `git` in the chat2dify image.
- chat2dify task state is isolated in `chat2dify_data`; it does not use Dify's
  database.
- To run chat2dify standalone, leave `CHAT2DIFY_PUBLIC_BASE_PATH` empty and run
  `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
