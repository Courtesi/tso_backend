from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
import os
import uvicorn
import logging
from app.router import router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.redis import redis_client

from dotenv import load_dotenv
import pathlib

import firebase_admin
from firebase_admin import credentials, auth

settings = get_settings()

if settings.ENV == "production":
	os.makedirs("logs", exist_ok=True)

	# Production: logs to files
	logging.basicConfig(
		level=logging.INFO,
		format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
		handlers=[
			logging.StreamHandler(),  # Still show critical errors in console
			RotatingFileHandler('logs/app.log', maxBytes=10*1024*1024, backupCount=5)
		]
	)

	# Access logs to separate file
	access_handler = RotatingFileHandler('logs/access.log', maxBytes=10*1024*1024, backupCount=5)
	logging.getLogger("uvicorn.access").addHandler(access_handler)
	logging.getLogger("uvicorn.access").setLevel(logging.INFO)
else:
	# Development: logs to console only, hide access logs
	logging.basicConfig(
		level=logging.INFO,
		format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
	)
	logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

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