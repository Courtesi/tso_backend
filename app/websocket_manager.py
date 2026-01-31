"""WebSocket connection manager for real-time data streaming."""

import asyncio
import json
import logging
import time
from typing import Dict
from dataclasses import dataclass, field

from fastapi import WebSocket
from firebase_admin.auth import verify_id_token
import redis.asyncio as redis

from app.config import get_settings
from app import filter_utils
from app.redis import redis_client as shared_redis

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class SubscriptionState:
	"""Tracks state for a single stream subscription."""

	channel: str
	filters: Dict
	redis_conn: redis.Redis
	listener_task: asyncio.Task


@dataclass
class ConnectionState:
	"""Tracks state for a single WebSocket connection."""

	websocket: WebSocket
	user_id: str = ""
	tier: str = "free"
	authenticated: bool = False
	subscriptions: Dict[str, SubscriptionState] = field(default_factory=dict)  # stream_name -> SubscriptionState
	last_ping: float = field(default_factory=time.time)


class WebSocketManager:
	"""Manages all WebSocket connections."""

	def __init__(self):
		self.active_connections: Dict[str, ConnectionState] = {}
		self.ping_interval = getattr(settings, 'WEBSOCKET_PING_INTERVAL', 30)
		self.pong_timeout = getattr(settings, 'WEBSOCKET_PONG_TIMEOUT', 10)

	async def connect(self, websocket: WebSocket, connection_id: str):
		"""
		Accept a new WebSocket connection.

		Args:
			websocket: FastAPI WebSocket instance
			connection_id: Unique connection identifier
		"""
		await websocket.accept()
		self.active_connections[connection_id] = ConnectionState(websocket=websocket)
		logger.info(f"WebSocket connected: {connection_id}")

	async def authenticate(self, connection_id: str, token: str) -> Dict:
		"""
		Authenticate a connection using Firebase ID token.

		Args:
			connection_id: Unique connection identifier
			token: Firebase ID token

		Returns:
			User info dict with uid and tier

		Raises:
			ValueError: If authentication fails
		"""
		if connection_id not in self.active_connections:
			raise ValueError("Connection not found")

		try:
			user = verify_id_token(token)
		except Exception as e:
			logger.error(f"Authentication failed for {connection_id}: {e}")
			raise ValueError(f"Authentication failed: {str(e)}")

		user_id = user.get("uid")

		stripe_role = user.get("stripeRole")
		tier = stripe_role if stripe_role else "free"

		conn = self.active_connections[connection_id]
		conn.user_id = user_id
		conn.tier = tier
		conn.authenticated = True

		logger.info(f"WebSocket authenticated: {connection_id} (user: {user_id}, tier: {tier})")
		return {"uid": user_id, "tier": tier}
	

	async def subscribe(self, connection_id: str, stream: str, filters: dict):
		"""
		Subscribe a connection to a data stream.

		Args:
			connection_id: Unique connection identifier
			stream: Stream name ("arbs" or "terminal")
			filters: Filter configuration dict
		"""
		if connection_id not in self.active_connections:
			raise ValueError("Connection not found")

		conn = self.active_connections[connection_id]

		if not conn.authenticated:
			raise ValueError("Connection not authenticated")

		if stream == "arbs":
			cache_key = settings.PREMIUM_KEY_PREFIX if conn.tier == "premium" else settings.FREE_KEY_PREFIX
			channel = f"{settings.REDIS_KEY_PREFIX}{cache_key}:updates"
		elif stream == "terminal":
			# ALWAYS subscribe to "all" channel and rely on filtering to prevent filter change issues
			cache_key = "terminal:all"
			channel = f"{settings.REDIS_KEY_PREFIX}{cache_key}:updates"
		elif stream == "ev":
			cache_key = f"ev:{conn.tier}"
			channel = f"{settings.REDIS_KEY_PREFIX}ev:{conn.tier}:updates"
		else:
			raise ValueError(f"Unknown stream: {stream}")

		# Tear down existing subscription for this stream if re-subscribing
		if stream in conn.subscriptions:
			old = conn.subscriptions[stream]
			old.listener_task.cancel()
			try:
				await old.listener_task
			except asyncio.CancelledError:
				pass
			await old.redis_conn.close()
			del conn.subscriptions[stream]

		# Create a dedicated Redis connection and listener for this stream
		redis_conn = redis.Redis(
			host=settings.REDIS_HOST,
			port=settings.REDIS_PORT,
			db=settings.REDIS_DB,
			password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
			decode_responses=True
		)

		listener_task = asyncio.create_task(
			self._redis_listener(connection_id, stream, channel, redis_conn)
		)

		conn.subscriptions[stream] = SubscriptionState(
			channel=channel,
			filters=dict(filters),
			redis_conn=redis_conn,
			listener_task=listener_task,
		)

		logger.info(f"Subscribed {str(connection_id)[:5]} to {stream} (channel: {channel})")
		await self._send_initial_data(connection_id, stream, filters, channel)

	async def update_filters(self, connection_id: str, filters: dict, stream: str = None):
		"""
		Update filters for a specific subscription without reconnecting.
		Sends re-filtered data immediately.

		Args:
			connection_id: Unique connection identifier
			filters: New filter configuration dict
			stream: Target stream name (if None, updates all subscriptions for backwards compat)
		"""
		if connection_id not in self.active_connections:
			raise ValueError("Connection not found")

		conn = self.active_connections[connection_id]

		# Determine which streams to update
		if stream:
			# Explicit stream requested — only update if it's actually subscribed
			streams_to_update = [stream] if stream in conn.subscriptions else []
		else:
			# No stream specified — update all (backwards compat)
			streams_to_update = list(conn.subscriptions.keys())

		for s in streams_to_update:
			# Merge new filters with existing subscription filters
			conn.subscriptions[s].filters.update(filters)
			merged_filters = conn.subscriptions[s].filters

			if s == "arbs":
				cache_key = settings.PREMIUM_KEY_PREFIX if conn.tier == "premium" else settings.FREE_KEY_PREFIX
				cache_key = f"{settings.REDIS_KEY_PREFIX}{cache_key}"
			elif s == "terminal":
				# ALWAYS fetch from "all" cache and apply league filter (ensures consistency with pubsub)
				cache_key = f"{settings.REDIS_KEY_PREFIX}terminal:all"
			elif s == "ev":
				cache_key = f"{settings.REDIS_KEY_PREFIX}ev:{conn.tier}"
			else:
				continue

			logger.debug(f"Applying merged filters for {s}: {merged_filters}")
			await self._send_filtered_data(connection_id, s, merged_filters, cache_key)

		logger.info(f"Updated filters for {str(connection_id)[:5]}: {filters}")

	async def unsubscribe(self, connection_id: str, stream: str):
		"""
		Unsubscribe a connection from a stream.

		Args:
			connection_id: Unique connection identifier
			stream: Stream name to unsubscribe from
		"""
		if connection_id not in self.active_connections:
			return

		conn = self.active_connections[connection_id]

		if stream in conn.subscriptions:
			sub = conn.subscriptions.pop(stream)
			sub.listener_task.cancel()
			try:
				await sub.listener_task
			except asyncio.CancelledError:
				pass
			await sub.redis_conn.close()
			logger.info(f"Unsubscribed {str(connection_id)[:5]} from {stream}")

	async def disconnect(self, connection_id: str):
		"""
		Clean up a disconnected WebSocket connection.

		Args:
			connection_id: Unique connection identifier
		"""
		if connection_id not in self.active_connections:
			return

		conn = self.active_connections[connection_id]

		# Tear down all per-stream subscriptions
		for sub in conn.subscriptions.values():
			sub.listener_task.cancel()
			try:
				await sub.listener_task
			except asyncio.CancelledError:
				pass
			await sub.redis_conn.close()
		conn.subscriptions.clear()

		# Remove from active connections
		del self.active_connections[connection_id]

		logger.info(f"WebSocket disconnected: {connection_id}")

	async def send_message(self, connection_id: str, message: dict):
		"""
		Send a message to a specific connection.

		Args:
			connection_id: Unique connection identifier
			message: Message dict to send
		"""
		if connection_id not in self.active_connections:
			return

		conn = self.active_connections[connection_id]

		try:
			await conn.websocket.send_json(message)
		except Exception as e:
			error_msg = str(e).lower()
			if "close" in error_msg or "not connected" in error_msg:
				logger.warning(f"Connection {connection_id} is dead, cleaning up: {e}")
				await self.disconnect(connection_id)
			else:
				logger.error(f"Failed to send message to {connection_id} (type={message.get('type')}): {e}")

	async def _send_initial_data(self, connection_id: str, stream: str, filters: dict, channel: str):
		"""
		Send initial cached data when a client subscribes.

		Args:
			connection_id: Unique connection identifier
			stream: Stream name
			filters: Filter configuration
			channel: Redis channel name
		"""
		# Extract cache key from channel
		cache_key = channel.replace(":updates", "")

		# Use the shared helper method
		await self._send_filtered_data(connection_id, stream, filters, cache_key)

	async def _send_filtered_data(
		self,
		connection_id: str,
		stream: str,
		filters: dict,
		cache_key: str
	):
		"""
		Fetch cached data, apply filters, and send to client.

		Used by both subscribe() and update_filters() to send data.

		Args:
			connection_id: Unique connection identifier
			stream: Stream name ("arbs" or "terminal")
			filters: Filter configuration dict
			cache_key: Redis cache key to fetch data from
		"""
		conn = self.active_connections.get(connection_id)
		if not conn:
			return

		try:
			# redis_client = redis.Redis(
			# 	host=settings.REDIS_HOST,
			# 	port=settings.REDIS_PORT,
			# 	db=settings.REDIS_DB,
			# 	password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
			# 	decode_responses=True
			# )
			# cached_data_raw = await redis_client.get(cache_key)
			# await redis_client.close()

			cached_data_raw = await shared_redis.redis.get(cache_key)
			if not cached_data_raw:
				await self.send_message(connection_id, {
					"type": "data",
					"stream": stream,
					"payload": {
						"tier": conn.tier,
						"data": [],
						"message": "No data available yet"
					}
				})
				return

			cached_data = json.loads(cached_data_raw)

			if stream == "arbs":
				filtered_data = filter_utils.apply_arb_filters(
					cached_data.get("data", []),
					filters,
					conn.tier
				)
			elif stream == "terminal":
				filtered_data = filter_utils.apply_terminal_filters(
					cached_data.get("data", []),
					filters,
					conn.tier
				)
			elif stream == "ev":
				filtered_data = filter_utils.apply_ev_filters(
					cached_data.get("data", []),
					filters,
					conn.tier
				)
			else:
				filtered_data = cached_data.get("data", [])

			response_data = {
				"type": "data",
				"stream": stream,
				"payload": {
					"tier": conn.tier,
					"data": filtered_data,
					"metadata": cached_data.get("metadata", {}),
					"cached_at": cached_data.get("cached_at")
				}
			}

			await self.send_message(connection_id, response_data)

		except Exception as e:
			logger.error(f"Failed to send filtered data to {connection_id}: {e}")

	async def _redis_listener(self, connection_id: str, stream: str, channel: str, redis_conn: redis.Redis):
		"""
		Listen for Redis pub/sub messages and forward to WebSocket.

		Args:
			connection_id: Unique connection identifier
			stream: Stream name this listener is for
			channel: Redis channel to subscribe to
			redis_conn: Dedicated Redis connection for this listener
		"""
		conn = self.active_connections.get(connection_id)
		if not conn:
			return

		pubsub = redis_conn.pubsub()

		try:
			await pubsub.subscribe(channel)

			while connection_id in self.active_connections:
				try:
					message = await asyncio.wait_for(
						pubsub.get_message(ignore_subscribe_messages=True, timeout=1),
						timeout=2.0
					)

					if message and message.get("type", "") == "message":
						cache_data = json.loads(message["data"])

						filters = conn.subscriptions[stream].filters if stream in conn.subscriptions else {}

						if stream == "arbs":
							filtered_data = filter_utils.apply_arb_filters(
								cache_data.get("data", []),
								filters,
								conn.tier
							)
						elif stream == "terminal":
							filtered_data = filter_utils.apply_terminal_filters(
								cache_data.get("data", []),
								filters,
								conn.tier
							)
						elif stream == "ev":
							filtered_data = filter_utils.apply_ev_filters(
								cache_data.get("data", []),
								filters,
								conn.tier
							)
						else:
							filtered_data = cache_data.get("data", [])

						response_data = {
							"type": "data",
							"stream": stream,
							"payload": {
								"tier": conn.tier,
								"data": filtered_data,
								"metadata": cache_data.get("metadata", {}),
								"cached_at": cache_data.get("cached_at")
							}
						}

						await self.send_message(connection_id, response_data)

					else:
						await asyncio.sleep(0.1)

				except asyncio.TimeoutError:
					continue
				except asyncio.CancelledError:
					break
				except Exception as e:
					logger.error(f"Error in Redis listener for {connection_id}: {e}")
					await asyncio.sleep(1)

		except Exception as e:
			logger.error(f"Fatal error in Redis listener for {connection_id}: {e}")
		finally:
			await pubsub.unsubscribe(channel)
			await pubsub.close()


ws_manager = WebSocketManager()
