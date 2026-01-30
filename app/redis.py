import redis.asyncio as redis
import json
from typing import Any, Optional
from functools import wraps
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class RedisClient:

	def __init__(self):
		self.redis: Optional[redis.Redis] = None

	async def connect(self):
		"""Connect to Redis server"""
		try:
			self.redis = redis.Redis(
				host=settings.REDIS_HOST,
				port=settings.REDIS_PORT,
				db=settings.REDIS_DB,
				password=settings.REDIS_PASSWORD,
				decode_responses=False,  # We'll handle encoding ourselves
				socket_connect_timeout=5,
				socket_timeout=5,
			)
			# Test connection
			await self.redis.ping()
			logger.info(f"Connected to Redis at {settings.REDIS_HOST}:{settings.REDIS_PORT}")
		except Exception as e:
			logger.error(f"Failed to connect to Redis: {e}")
			raise

	async def disconnect(self):
		"""Disconnect from Redis"""
		if self.redis:
			await self.redis.close()
			logger.info("Disconnected from Redis")

	def _make_key(self, key: str) -> str:
		"""Add prefix to distinguish from Spring Boot keys"""
		return f"{settings.REDIS_KEY_PREFIX}{key}"

	async def get(self, key: str) -> Optional[Any]:
		"""Get value from cache"""
		if not self.redis:
			return None

		try:
			full_key = self._make_key(key)
			data = await self.redis.get(full_key)
			if data:
				return json.loads(data)
			return None
		except Exception as e:
			logger.error(f"Redis GET error for key {key}: {e}")
			return None

	async def set(self, key: str, value: Any, ttl: int) -> bool:
		"""Set value in cache with TTL"""
		if not self.redis:
			return False

		try:
			full_key = self._make_key(key)
			data = json.dumps(value)
			await self.redis.setex(full_key, ttl, data)
			return True
		except Exception as e:
			logger.error(f"Redis SET error for key {key}: {e}")
			return False

	async def delete(self, key: str) -> bool:
		"""Delete key from cache"""
		if not self.redis:
			return False

		try:
			full_key = self._make_key(key)
			result = await self.redis.delete(full_key)
			return result > 0
		except Exception as e:
			logger.error(f"Redis DELETE error for key {key}: {e}")
			return False

	async def exists(self, key: str) -> bool:
		"""Check if key exists in cache"""
		if not self.redis:
			return False

		try:
			full_key = self._make_key(key)
			result = await self.redis.exists(full_key)
			return result > 0
		except Exception as e:
			logger.error(f"Redis EXISTS error for key {key}: {e}")
			return False


# Global Redis client instance
redis_client = RedisClient()

def cache_response(ttl: int = settings.CACHE_TTL_MEDIUM, key_generator: Optional[callable] = None):
	"""
	Decorator to cache FastAPI endpoint responses.

	Args:
		ttl: Time to live in seconds
		key_generator: Function to generate cache key from request args
	"""
	def decorator(func):
		@wraps(func)
		async def wrapper(*args, **kwargs):
			# Generate cache key
			if key_generator:
				cache_key = key_generator(*args, **kwargs)
			else:
				# Default key generation: function_name:args:kwargs
				args_str = "_".join(str(arg) for arg in args[1:])  # Skip 'self' if present
				kwargs_str = "_".join(f"{k}:{v}" for k, v in sorted(kwargs.items()))
				cache_key = f"{func.__name__}:{args_str}:{kwargs_str}"

			# Try to get from cache first
			cached_result = await redis_client.get(cache_key)
			if cached_result is not None:
				logger.debug(f"Cache HIT for key: {cache_key}")
				return cached_result

			# Cache miss - execute function
			logger.debug(f"Cache MISS for key: {cache_key}")
			result = await func(*args, **kwargs)

			# Store in cache
			await redis_client.set(cache_key, result, ttl)
			return result

		return wrapper
	return decorator