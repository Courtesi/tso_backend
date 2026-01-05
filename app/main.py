from contextlib import asynccontextmanager
import logging
from app.router import router
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
	level=logging.INFO,
	format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.setLevel(logging.INFO)
uvicorn_logger.addFilter(HealthCheckFilter())

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.connect()

    yield
    
    await redis_client.disconnect()
    
app = FastAPI(lifespan=lifespan)
origins = [settings.FRONTEND_URL]

app.add_middleware(
	CORSMiddleware,
	allow_origins=origins,
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# Initialize Firebase Admin SDK with service account
cred = credentials.Certificate(settings.GOOGLE_APPLICATION_CREDENTIALS)
firebase_admin.initialize_app(cred)