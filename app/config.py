from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
	APP_NAME: str = "Trueshot FastAPI"
	ENV: str
	FRONTEND_URL: str

	GOOGLE_APPLICATION_CREDENTIALS: str
	STRIPE_SECRET_KEY: str
	RESEND_API_KEY: str
	RESEND_EMAIL: str

	REDIS_HOST: str
	REDIS_PORT: int
	REDIS_DB: int
	REDIS_PASSWORD: str = ""
	REDIS_KEY_PREFIX: str
	CACHE_TTL_MEDIUM: int
	FREE_KEY_PREFIX: str
	PREMIUM_KEY_PREFIX: str

	# Uvicorn logging settings (optional)
	UVICORN_LOG_LEVEL: str = "info"
	UVICORN_ACCESS_LOG: bool = True

	TIER_RATE_LIMITS: dict = {
		"free": 60,
		"premium": 5
	}

	TIER_MAX_ARBS: dict = {
		"free": 5,
		"premium": None
	}

	class Config:
		env_file = "secrets/.env"
		# extra = "ignore"  # Ignore extra fields in .env that aren't defined here
	
@lru_cache
def get_settings() -> Settings:
	"""Retrieves the fastapi settings"""
	return Settings()
