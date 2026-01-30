# Backend — CLAUDE.md

## Stack

- **Framework:** FastAPI (async), Python 3.11+
- **Package manager:** uv
- **Auth:** Firebase Admin SDK (ID token verification)
- **Payments:** Stripe
- **Cache/pubsub:** Redis
- **Email:** Resend

## Running locally

```bash
uv run fastapi dev        # dev server on :8000
uv run fastapi run        # production mode (used in Docker)
```

## Project layout

```
app/
  main.py                 # Entry point, lifespan, CORS, router registration
  config.py               # pydantic-settings, env vars
  router.py               # REST API routes
  websocket_router.py     # /api/ws endpoint, auth + message loop
  websocket_manager.py    # Connection state, Redis pubsub listener, send_message
  redis.py                # Redis client singleton
  models.py               # Pydantic models
  filter_utils.py         # Arb/terminal/EV data filtering
  terminal_utils.py       # Terminal stream utilities
  ev_utils.py             # Expected value utilities
  dependencies/
    authentication.py     # FastAPI dependency for auth
```

## Key patterns

- WebSocket connections go through `/api/ws`. First message must be `authenticate` within 10s or the socket is closed.
- Streams: `arbs`, `terminal`, `ev`. Clients subscribe after auth; data is pushed via Redis pubsub.
- Tier system (`free` / `premium`) derived from Firebase custom claim `stripeRole`. Affects which Redis keys and filters are used.
- `WebSocketManager` is a global singleton (`ws_manager`). Each connection gets a `ConnectionState` with its own Redis pubsub client and background listener task.
- REST routes and WS routes are both mounted under `/api`.

## Linting

```bash
uv run ruff check .
uv run ruff format .
```

## Docker

Dockerfile uses a multi-stage build. Production container runs as non-root `appuser`. Health check hits `/api/health`.

## Environment

See `.env.example` for required variables. `service-account.json` (Firebase) is mounted at `/app/service-account.json` in Docker.
