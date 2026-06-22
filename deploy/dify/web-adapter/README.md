# Dify Web Adapter

This directory keeps a copy of the Dify web source changes used to embed
chat2dify inside the Dify Console.

The files mirror Dify's repository layout under `web/`:

```text
web/app/components/chat2dify/panel.tsx
web/app/components/chat2dify/workflow-trigger.tsx
web/app/components/apps/new-app-card.tsx
web/app/components/apps/__tests__/new-app-card.spec.tsx
web/app/components/workflow-app/components/workflow-header/index.tsx
web/app/components/workflow-app/components/workflow-header/__tests__/index.spec.tsx
```

What the adapter adds:

- a reusable right-side drawer that embeds `/chat2dify/` in an iframe;
- a `Chat2Dify 创建` action in the Studio create-app card;
- a `Chat2Dify` action in the Workflow canvas header;
- query parameters that pass create or modify context into chat2dify:
  `embed=1`, `intent=create|modify`, `app_mode`, `app_id`, and `app_name`.

To apply this adapter to a sibling Dify checkout:

```bash
rsync -av deploy/dify/web-adapter/web/ ../dify/web/
```

Then rebuild Dify web with the compose overlay:

```bash
cd ../dify/docker
docker compose \
  -f docker-compose.yaml \
  -f ../../chat2dify/deploy/dify/docker-compose.chat2dify.yaml \
  up -d --build web chat2dify nginx
```

The adapter is intentionally small and Dify-specific. chat2dify itself remains
an independent sidecar service.
