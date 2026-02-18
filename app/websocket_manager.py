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
    last_data: dict = field(default_factory=dict)


@dataclass
class ConnectionState:
    """Tracks state for a single WebSocket connection."""

    websocket: WebSocket
    user_id: str = ""
    tier: str = "free"
    authenticated: bool = False
    subscriptions: Dict[str, SubscriptionState] = field(
        default_factory=dict
    )  # stream_name -> SubscriptionState
    last_ping: float = field(default_factory=time.time)


class WebSocketManager:
    """Manages all WebSocket connections."""

    def __init__(self):
        self.active_connections: Dict[str, ConnectionState] = {}
        self.ping_interval = getattr(settings, "WEBSOCKET_PING_INTERVAL", 30)
        self.pong_timeout = getattr(settings, "WEBSOCKET_PONG_TIMEOUT", 10)

    async def connect(self, websocket: WebSocket, connection_id: str):
        await websocket.accept()
        self.active_connections[connection_id] = ConnectionState(websocket=websocket)
        logger.info(f"WebSocket connected: {connection_id[:5]}")

    async def disconnect(self, connection_id: str):
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

        # logger.info(f"WebSocket disconnected: {connection_id[:5]}")

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
            logger.error(f"Authentication failed for {connection_id[:5]}: {e}")
            raise ValueError(f"Authentication failed: {str(e)}")

        user_id = user.get("uid")

        stripe_role = user.get("stripeRole")
        tier = stripe_role if stripe_role else "free"

        conn = self.active_connections[connection_id]
        conn.user_id = user_id
        conn.tier = tier
        conn.authenticated = True

        logger.info(
            f"WebSocket authenticated: {str(connection_id)[:5]} (user: {user_id}, tier: {tier})"
        )
        return {"uid": user_id, "tier": tier}

    async def subscribe(self, connection_id: str, stream: str, filters: dict):
        """
        Subscribe a connection to a data stream.

        Args:
                connection_id: Unique connection identifier
                stream: Stream name ("arbs" or "terminal")
                filters: Filter configuration dict
        """
        subscribe_st = time.time()

        if connection_id not in self.active_connections:
            raise ValueError("Connection not found")

        conn = self.active_connections[connection_id]

        if not conn.authenticated:
            raise ValueError("Connection not authenticated")

        if stream == "arbs":
            cache_key = (
                settings.PREMIUM_KEY_PREFIX
                if conn.tier == "premium"
                else settings.FREE_KEY_PREFIX
            )
            channel = f"{settings.REDIS_KEY_PREFIX}{cache_key}:updates"
        elif stream == "terminal":
            league = filters.get("league", "NBA")
            # Validate tier access to this league
            allowed_leagues = settings.TIER_ALLOWED_LEAGUES.get(conn.tier)
            if allowed_leagues and league.upper() not in (allowed_league.upper() for allowed_league in allowed_leagues):
                raise ValueError(f"League {league} not available for {conn.tier} tier")
            channel = f"lines:{league}"
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
            decode_responses=True,
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

        # Terminal initial data is loaded by the frontend via REST /api/terminal/lines
        if stream != "terminal":
            sid_st = time.time()
            await self._send_initial_data(connection_id, stream, filters, channel)
            sid_et = round(time.time() - sid_st, 2)
        else:
            sid_et = 0

        subscribe_et = round(time.time() - subscribe_st, 2)
        logger.info(
            f"Subscribed {str(connection_id)[:5]} to {stream} (channel: {channel}); {subscribe_et=}, {sid_et=}"
        )

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

    async def update_filters(
        self, connection_id: str, filters: dict, stream: str = None
    ):
        """
        Update filters for a specific subscription without reconnecting.
        Sends re-filtered data immediately.

        Args:
                connection_id: Unique connection identifier
                filters: New filter configuration dict
                stream: Target stream name (if None, updates all subscriptions for backwards compat)
        """
        sfd_time = 0
        uf_st = time.time()

        if connection_id not in self.active_connections:
            raise ValueError("Connection not found")

        conn = self.active_connections[connection_id]

        # Determine which streams to update
        if stream:
            # Explicit stream requested — only update if it's actually subscribed
            streams_to_update = [stream] if stream in conn.subscriptions else []
        else:
            raise ValueError("No stream specified")

        for s in streams_to_update:
            # Merge new filters with existing subscription filters
            conn.subscriptions[s].filters.update(filters)
            merged_filters = conn.subscriptions[s].filters

            # Use in-memory cached data if the listener has received at least one update
            cached = conn.subscriptions[s].last_data
            if cached:
                sfd_st = time.time()
                await self._send_from_cache(connection_id, s, merged_filters, cached)
                sfd_time += time.time() - sfd_st
                continue

            if s == "arbs":
                cache_key = (
                    settings.PREMIUM_KEY_PREFIX
                    if conn.tier == "premium"
                    else settings.FREE_KEY_PREFIX
                )
                cache_key = f"{settings.REDIS_KEY_PREFIX}{cache_key}"
            elif s == "terminal":
                cache_key = f"{settings.REDIS_KEY_PREFIX}terminal:all"
            elif s == "ev":
                cache_key = f"{settings.REDIS_KEY_PREFIX}ev:{conn.tier}"
            else:
                continue

            sfd_st = time.time()
            await self._send_filtered_data(connection_id, s, merged_filters, cache_key)
            sfd_time += time.time() - sfd_st

        uf_et = round(time.time() - uf_st, 2)
        logger.info(
            f"Updated filters ({uf_et=}, {round(sfd_time, 2)=}) for {str(connection_id)[:5]}: stream={stream}, filters={filters}"
        )

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
                logger.warning(f"Connection {connection_id[:5]} is dead, cleaning up: {e}")
                await self.disconnect(connection_id)
            else:
                logger.error(
                    f"Failed to send message to {connection_id[:5]} (type={message.get('type')}): {e}"
                )

    async def _send_initial_data(
        self, connection_id: str, stream: str, filters: dict, channel: str
    ):
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
        self, connection_id: str, stream: str, filters: dict, cache_key: str
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
            cached_data_raw = await shared_redis.redis.get(cache_key)
            if not cached_data_raw:
                await self.send_message(
                    connection_id,
                    {
                        "type": "data",
                        "stream": stream,
                        "payload": {
                            "tier": conn.tier,
                            "data": [],
                            "message": "No data available yet",
                        },
                    },
                )
                return

            cached_data = json.loads(cached_data_raw)

            if stream in conn.subscriptions:
                conn.subscriptions[stream].last_data = cached_data

            if stream == "arbs":
                filtered_data = filter_utils.apply_arb_filters(
                    cached_data.get("data", []), filters, conn.tier
                )
            elif stream == "terminal":
                filtered_data = filter_utils.apply_terminal_tier_filters(
                    cached_data.get("data", []), conn.tier
                )
            elif stream == "ev":
                filtered_data = filter_utils.apply_ev_filters(
                    cached_data.get("data", []), filters, conn.tier
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
                    "cached_at": cached_data.get("cached_at"),
                },
            }

            await self.send_message(connection_id, response_data)

        except Exception as e:
            logger.error(f"Failed to send filtered data to {connection_id[:5]}: {e}")

    async def _send_from_cache(
        self, connection_id: str, stream: str, filters: dict, cached_data: dict
    ):
        """Filter and send already-parsed data without a Redis round-trip."""
        conn = self.active_connections.get(connection_id)
        if not conn:
            return

        try:
            if stream == "arbs":
                filtered_data = filter_utils.apply_arb_filters(
                    cached_data.get("data", []), filters, conn.tier
                )
            elif stream == "terminal":
                filtered_data = filter_utils.apply_terminal_tier_filters(
                    cached_data.get("data", []), conn.tier
                )
            elif stream == "ev":
                filtered_data = filter_utils.apply_ev_filters(
                    cached_data.get("data", []), filters, conn.tier
                )
            else:
                filtered_data = cached_data.get("data", [])

            await self.send_message(
                connection_id,
                {
                    "type": "data",
                    "stream": stream,
                    "payload": {
                        "tier": conn.tier,
                        "data": filtered_data,
                        "metadata": cached_data.get("metadata", {}),
                        "cached_at": cached_data.get("cached_at"),
                    },
                },
            )

        except Exception as e:
            logger.error(f"Failed to send cached data to {connection_id[:5]}: {e}")

    async def _redis_listener(
        self, connection_id: str, stream: str, channel: str, redis_conn: redis.Redis
    ):
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
                        timeout=2.0,
                    )

                    if not message:
                        await asyncio.sleep(0.1)
                        continue

                    if message.get("type") != "message":
                        await asyncio.sleep(0.1)
                        continue

                    raw_data = json.loads(message["data"])

                    if stream == "terminal":
                        # lines:{league} — forward LineUpdate[] directly
                        await self.send_message(
                            connection_id,
                            {
                                "type": "data",
                                "stream": stream,
                                "payload": {
                                    "data": raw_data,
                                },
                            },
                        )
                    else:
                        # arbs / ev streams — apply filters
                        cache_data = raw_data

                        if stream in conn.subscriptions:
                            conn.subscriptions[stream].last_data = cache_data

                        filters = (
                            conn.subscriptions[stream].filters
                            if stream in conn.subscriptions
                            else {}
                        )

                        if stream == "arbs":
                            filtered_data = filter_utils.apply_arb_filters(
                                cache_data.get("data", []), filters, conn.tier
                            )
                        elif stream == "ev":
                            filtered_data = filter_utils.apply_ev_filters(
                                cache_data.get("data", []), filters, conn.tier
                            )
                        else:
                            filtered_data = cache_data.get("data", [])

                        await self.send_message(
                            connection_id,
                            {
                                "type": "data",
                                "stream": stream,
                                "payload": {
                                    "tier": conn.tier,
                                    "data": filtered_data,
                                    "metadata": cache_data.get("metadata", {}),
                                    "cached_at": cache_data.get("cached_at"),
                                },
                            },
                        )

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in Redis listener for {connection_id[:5]}: {e}")
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Fatal error in Redis listener for {connection_id[:5]}: {e}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()


ws_manager = WebSocketManager()
