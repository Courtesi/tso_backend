"""WebSocket connection manager for real-time data streaming."""

import asyncio
import json
import logging
import time
from typing import Dict, Optional
from dataclasses import dataclass, field

from fastapi import WebSocket
from firebase_admin.auth import verify_id_token
import redis.asyncio as redis

from app.config import get_settings
from app import filter_utils

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class ConnectionState:
    """Tracks state for a single WebSocket connection."""

    websocket: WebSocket
    user_id: str = ""
    tier: str = "free"
    authenticated: bool = False
    subscriptions: Dict[str, Dict] = field(default_factory=dict)  # stream_name -> filters
    last_ping: float = field(default_factory=time.time)
    redis_pubsub: Optional[redis.Redis] = None
    pubsub_task: Optional[asyncio.Task] = None


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
            # Verify Firebase token
            user = verify_id_token(token)
            user_id = user.get("uid")

            # Get tier from custom claims
            stripe_role = user.get("stripeRole")
            tier = stripe_role if stripe_role else "free"

            # Update connection state
            conn = self.active_connections[connection_id]
            conn.user_id = user_id
            conn.tier = tier
            conn.authenticated = True

            logger.info(f"WebSocket authenticated: {connection_id} (user: {user_id}, tier: {tier})")

            return {"uid": user_id, "tier": tier}

        except Exception as e:
            logger.error(f"Authentication failed for {connection_id}: {e}")
            raise ValueError(f"Authentication failed: {str(e)}")

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

        # Store subscription with filters (copy to prevent reference issues)
        conn.subscriptions[stream] = dict(filters)

        # Determine Redis channel based on stream and tier
        if stream == "arbs":
            cache_key = settings.PREMIUM_KEY_PREFIX if conn.tier == "premium" else settings.FREE_KEY_PREFIX
            channel = f"{settings.REDIS_KEY_PREFIX}{cache_key}:updates"
        elif stream == "terminal":
            # ALWAYS subscribe to "all" channel and rely on filtering
            # This prevents issues when league filter changes
            cache_key = "terminal:all"
            channel = f"{settings.REDIS_KEY_PREFIX}{cache_key}:updates"
        elif stream == "ev":
            cache_key = f"ev:{conn.tier}"
            channel = f"{settings.REDIS_KEY_PREFIX}ev:{conn.tier}:updates"
        else:
            raise ValueError(f"Unknown stream: {stream}")

        # Set up Redis pub/sub if not already set up
        if not conn.redis_pubsub:
            conn.redis_pubsub = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
                decode_responses=True
            )

            # Start pub/sub listener task
            conn.pubsub_task = asyncio.create_task(
                self._redis_listener(connection_id, channel)
            )

        logger.info(f"Subscribed {connection_id} to {stream} (channel: {channel})")

        # Send initial cached data
        await self._send_initial_data(connection_id, stream, filters, channel)

    async def update_filters(self, connection_id: str, filters: dict):
        """
        Update filters for an existing subscription without reconnecting.
        NOW SENDS RE-FILTERED DATA IMMEDIATELY.

        Args:
            connection_id: Unique connection identifier
            filters: New filter configuration dict
        """
        if connection_id not in self.active_connections:
            raise ValueError("Connection not found")

        conn = self.active_connections[connection_id]

        # Update filters for all active subscriptions
        for stream in conn.subscriptions:
            # Merge new filters with existing subscription filters
            conn.subscriptions[stream].update(filters)
            merged_filters = conn.subscriptions[stream]

            # Determine cache key and send filtered data immediately
            if stream == "arbs":
                cache_key = settings.PREMIUM_KEY_PREFIX if conn.tier == "premium" else settings.FREE_KEY_PREFIX
                cache_key = f"{settings.REDIS_KEY_PREFIX}{cache_key}"
            elif stream == "terminal":
                # ALWAYS fetch from "all" cache and apply league filter
                # This ensures consistency with Redis pub/sub subscription
                cache_key = f"{settings.REDIS_KEY_PREFIX}terminal:all"
            elif stream == "ev":
                cache_key = f"{settings.REDIS_KEY_PREFIX}ev:{conn.tier}"
            else:
                continue

            # Send re-filtered data immediately using merged filters
            logger.debug(f"Applying merged filters for {stream}: {merged_filters}")
            await self._send_filtered_data(connection_id, stream, merged_filters, cache_key)

        logger.info(f"Updated filters for {connection_id}: {filters}")

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
            del conn.subscriptions[stream]
            logger.info(f"Unsubscribed {connection_id} from {stream}")

        # If no more subscriptions, clean up Redis connection
        if not conn.subscriptions and conn.redis_pubsub:
            if conn.pubsub_task:
                conn.pubsub_task.cancel()
            await conn.redis_pubsub.close()
            conn.redis_pubsub = None

    async def disconnect(self, connection_id: str):
        """
        Clean up a disconnected WebSocket connection.

        Args:
            connection_id: Unique connection identifier
        """
        if connection_id not in self.active_connections:
            return

        conn = self.active_connections[connection_id]

        # Cancel pub/sub listener task
        if conn.pubsub_task:
            conn.pubsub_task.cancel()

        # Close Redis connection
        if conn.redis_pubsub:
            await conn.redis_pubsub.close()

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
            logger.warning(f"Failed to send message to {connection_id}: {e}")
            # Connection is dead — remove it so the Redis listener stops sending
            self.active_connections.pop(connection_id, None)

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
            # Connect to Redis to fetch cached data
            redis_client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
                decode_responses=True
            )

            cached_data_raw = await redis_client.get(cache_key)
            await redis_client.close()

            if cached_data_raw:
                cached_data = json.loads(cached_data_raw)

                # Apply filters
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
            else:
                # No cached data available
                await self.send_message(connection_id, {
                    "type": "data",
                    "stream": stream,
                    "payload": {
                        "tier": conn.tier,
                        "data": [],
                        "message": "No data available yet"
                    }
                })

        except Exception as e:
            logger.error(f"Failed to send filtered data to {connection_id}: {e}")

    async def _redis_listener(self, connection_id: str, channel: str):
        """
        Listen for Redis pub/sub messages and forward to WebSocket.

        Args:
            connection_id: Unique connection identifier
            channel: Redis channel to subscribe to
        """
        conn = self.active_connections.get(connection_id)
        if not conn or not conn.redis_pubsub:
            return

        pubsub = conn.redis_pubsub.pubsub()

        try:
            await pubsub.subscribe(channel)
            logger.info(f"Redis listener started for {connection_id} on {channel}")

            while connection_id in self.active_connections:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1),
                        timeout=2.0
                    )

                    if message and message["type"] == "message":
                        # Parse the data
                        cache_data = json.loads(message["data"])

                        # Determine stream type from channel
                        if "arbs" in channel:
                            stream = "arbs"
                        elif "ev:" in channel:
                            stream = "ev"
                        else:
                            stream = "terminal"

                        # Get current filters
                        filters = conn.subscriptions.get(stream, {})

                        # Apply filters
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

                        # Send to WebSocket
                        await self.send_message(connection_id, response_data)

                    else:
                        # No message, just continue
                        await asyncio.sleep(0.1)

                except asyncio.TimeoutError:
                    # No message within timeout, continue
                    continue
                except asyncio.CancelledError:
                    # Task was cancelled
                    break
                except Exception as e:
                    logger.error(f"Error in Redis listener for {connection_id}: {e}")
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Fatal error in Redis listener for {connection_id}: {e}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            logger.info(f"Redis listener stopped for {connection_id}")


# Global WebSocket manager instance
ws_manager = WebSocketManager()
