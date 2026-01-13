"""WebSocket router for real-time data streaming."""

import asyncio
import logging
import time
import uuid
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter()


async def handle_message(connection_id: str, message: Dict):
    """
    Handle incoming WebSocket messages.

    Args:
        connection_id: Unique connection identifier
        message: Message dict from client
    """
    message_type = message.get("type")

    try:
        if message_type == "authenticate":
            # Authenticate the connection
            token = message.get("token")
            if not token:
                await ws_manager.send_message(connection_id, {
                    "type": "error",
                    "code": "MISSING_TOKEN",
                    "message": "Authentication token is required"
                })
                return

            try:
                user = await ws_manager.authenticate(connection_id, token)
                await ws_manager.send_message(connection_id, {
                    "type": "auth_success",
                    "user": user
                })
            except ValueError as e:
                await ws_manager.send_message(connection_id, {
                    "type": "auth_error",
                    "message": str(e)
                })

        elif message_type == "subscribe":
            # Subscribe to a stream
            stream = message.get("stream")
            filters = message.get("filters", {})

            if not stream:
                await ws_manager.send_message(connection_id, {
                    "type": "error",
                    "code": "MISSING_STREAM",
                    "message": "Stream name is required"
                })
                return

            if stream not in ["arbs", "terminal"]:
                await ws_manager.send_message(connection_id, {
                    "type": "error",
                    "code": "INVALID_STREAM",
                    "message": f"Unknown stream: {stream}"
                })
                return

            try:
                await ws_manager.subscribe(connection_id, stream, filters)
                await ws_manager.send_message(connection_id, {
                    "type": "subscribed",
                    "stream": stream,
                    "filters": filters
                })
            except ValueError as e:
                await ws_manager.send_message(connection_id, {
                    "type": "error",
                    "code": "SUBSCRIPTION_FAILED",
                    "message": str(e)
                })

        elif message_type == "update_filters":
            # Update filters without reconnecting
            filters = message.get("filters", {})

            try:
                await ws_manager.update_filters(connection_id, filters)
                await ws_manager.send_message(connection_id, {
                    "type": "filters_updated",
                    "filters": filters
                })
            except ValueError as e:
                await ws_manager.send_message(connection_id, {
                    "type": "error",
                    "code": "FILTER_UPDATE_FAILED",
                    "message": str(e)
                })

        elif message_type == "unsubscribe":
            # Unsubscribe from a stream
            stream = message.get("stream")

            if not stream:
                await ws_manager.send_message(connection_id, {
                    "type": "error",
                    "code": "MISSING_STREAM",
                    "message": "Stream name is required"
                })
                return

            await ws_manager.unsubscribe(connection_id, stream)
            await ws_manager.send_message(connection_id, {
                "type": "unsubscribed",
                "stream": stream
            })

        elif message_type == "ping":
            # Respond to ping with pong
            await ws_manager.send_message(connection_id, {
                "type": "pong",
                "timestamp": int(time.time())
            })

        else:
            # Unknown message type
            await ws_manager.send_message(connection_id, {
                "type": "error",
                "code": "UNKNOWN_MESSAGE_TYPE",
                "message": f"Unknown message type: {message_type}"
            })

    except Exception as e:
        logger.error(f"Error handling message from {connection_id}: {e}")
        await ws_manager.send_message(connection_id, {
            "type": "error",
            "code": "SERVER_ERROR",
            "message": "An internal server error occurred"
        })


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for real-time data streaming.

    This endpoint handles:
    - Connection establishment
    - Authentication
    - Stream subscription (arbs, terminal)
    - Dynamic filter updates
    - Heartbeat (ping/pong)
    """
    connection_id = str(uuid.uuid4())

    try:
        # Accept the connection
        await ws_manager.connect(websocket, connection_id)

        # Wait for authentication within 10 seconds
        try:
            auth_message = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=10.0
            )

            if auth_message.get("type") != "authenticate":
                await ws_manager.send_message(connection_id, {
                    "type": "error",
                    "code": "AUTH_REQUIRED",
                    "message": "First message must be authentication"
                })
                await websocket.close(code=1008)  # Policy violation
                return

            # Handle authentication
            await handle_message(connection_id, auth_message)

            # Check if authentication succeeded
            conn = ws_manager.active_connections.get(connection_id)
            if not conn or not conn.authenticated:
                await websocket.close(code=1008)  # Policy violation
                return

        except asyncio.TimeoutError:
            logger.warning(f"Authentication timeout for {connection_id}")
            await websocket.close(code=1008)  # Policy violation
            return

        # Message loop
        while True:
            try:
                message = await websocket.receive_json()
                await handle_message(connection_id, message)

            except WebSocketDisconnect:
                logger.info(f"Client disconnected: {connection_id}")
                break

            except Exception as e:
                logger.error(f"Error receiving message from {connection_id}: {e}")
                await ws_manager.send_message(connection_id, {
                    "type": "error",
                    "code": "MESSAGE_ERROR",
                    "message": "Failed to process message"
                })

    except WebSocketDisconnect:
        logger.info(f"Client disconnected during setup: {connection_id}")

    except Exception as e:
        logger.error(f"Fatal error in WebSocket endpoint for {connection_id}: {e}")

    finally:
        # Clean up connection
        await ws_manager.disconnect(connection_id)
