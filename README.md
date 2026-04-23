# Backend

FastAPI backend service for TrueShotOdds arbitrage betting platform.

## Quick Start

### Prerequisites
- Python 3.11+
- uv package manager
- Redis server
- Google Cloud service account (for Firebase)

### Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   uv sync
   ```

3. Set up secrets:
   ```bash
   cp .env.example .env
   # Edit .env with your actual credentials
   ```

4. Add your Firebase service account JSON:
   ```bash
   # Place your service-account.json in webscraper/
   cp /path/to/your/service-account.json service-account.json
   ```

### Configuration

The backend requires the following environment variables (see `.env.example`):

<!-- ENV_EXAMPLE_START -->
```env
ENV=development

# For Stripe redirects
FRONTEND_URL=... # eg: http://localhost:5173

# 3rd party secrets
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json
# Webhook secret key
STRIPE_SECRET_KEY=sk_test_...
RESEND_API_KEY=re_...
RESEND_EMAIL=support@trueshotodds.com

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_KEY_PREFIX=trueshot:
FREE_KEY_PREFIX=arbs:free
PREMIUM_KEY_PREFIX=arbs:premium
CACHE_TTL_MEDIUM=300
```
<!-- ENV_EXAMPLE_END -->

**Key configuration notes**:
- `ENV`: Set to `development` for local, `production` for deployment
- `FRONTEND_URL`: URL of your frontend application
- `REDIS_HOST`: Use `redis` for Docker, `localhost` for local development
- `GOOGLE_APPLICATION_CREDENTIALS`: Path to Firebase service account JSON

### Running the Backend

**Local development**:
```bash
uv run fastapi dev
```

The API will be available at `http://localhost:8000`

**Production (Docker)**:
```bash
docker-compose up backend
```

## API Documentation

Once running, view interactive API docs at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Development

### Setting up pre-commit hooks

This project uses pre-commit hooks to automatically lint code and keep documentation in sync:

```bash
# Install pre-commit hooks
uv run pre-commit install
```

Now:
- Ruff will automatically check your code on every commit
- README will automatically update when you modify `secrets/.env.example`

### Project Structure

```
backend/
├── app/
│   ├── main.py           # FastAPI application entry point
│   ├── config.py         # Settings and environment variables
│   ├── redis.py		  # Redis Client singleton for cache access
│   ├── terminal_utils.py # Terminal functions for line-tracking
│   └── router.py         # API route definitions
├── .env.example      # Environment variable template
├── .env              # Your actual credentials (gitignored)
├── service-account.json  # Firebase credentials (gitignored)
├── git_hooks/
│   └── embed_env_in_readme.py  # Auto-update README script
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Architecture

[Add architecture documentation here]