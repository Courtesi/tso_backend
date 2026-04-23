from contextlib import asynccontextmanager
import logging
from app.router import router
from app.websocket_router import router as ws_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.redis import redis_client

import firebase_admin
from firebase_admin import credentials

settings = get_settings()


# Custom filter to exclude health check logs
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/health" not in record.getMessage()


# Development: logs to console only, hide access logs
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.setLevel(logging.INFO)
uvicorn_logger.addFilter(HealthCheckFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.connect()

    yield

    await redis_client.disconnect()


docs_enabled = settings.ENV == "development" or settings.DOCS_ENABLED
app = FastAPI(
    lifespan=lifespan,
    title="TrueShotOdds API",
    description="Backend API for TrueShotOdds — real-time sports betting arbitrage and +EV bet detection across 40+ sportsbooks.",
    version="1.0.0",
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
    openapi_tags=[
        {"name": "Health", "description": "Service liveness check."},
        {
            "name": "Config",
            "description": "Public configuration: Stripe products, supported sportsbooks, and available leagues.",
        },
        {
            "name": "Lines",
            "description": "Authenticated endpoints for live odds snapshots and full line-movement history.",
        },
        {"name": "Users", "description": "Account management for authenticated users."},
        {
            "name": "Stripe",
            "description": "Stripe billing portal and subscription management.",
        },
        {"name": "Reports", "description": "Bug report submission."},
    ],
)
origins = [settings.FRONTEND_URL]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(ws_router, prefix="/api")

# Initialize Firebase Admin SDK with service account
cred = credentials.Certificate(settings.GOOGLE_APPLICATION_CREDENTIALS)
firebase_admin.initialize_app(cred)
