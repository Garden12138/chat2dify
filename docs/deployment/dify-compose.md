# Dify Compose Deployment

chat2dify v3.0.0 can run as an independent component beside Dify and be exposed
inside the same nginx entry point at `/chat2dify/`.

This deployment keeps the Dify source tree unchanged. The compose overlay adds:

- `chat2dify`: the FastAPI component built from this repository.
- an nginx template override that routes `/chat2dify/` to the component.
- a `chat2dify_data` volume for task state.

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
CHAT2DIFY_PLANNER_FALLBACK_PROVIDERS=openrouter,openai
CHAT2DIFY_NVIDIA_API_KEY=nvapi-...
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
  up -d --build chat2dify nginx
```

Open:

```text
http://localhost/chat2dify/
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
- The overlay mounts the Dify repository into the container at `/dify` as
  read-only so chat2dify can read the current DSL version.
- chat2dify task state is isolated in `chat2dify_data`; it does not use Dify's
  database.
- To run chat2dify standalone, leave `CHAT2DIFY_PUBLIC_BASE_PATH` empty and run
  `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
