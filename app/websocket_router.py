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
	message_type = message.get("type")

	try:
		if message_type == "subscribe":
			stream = message.get("stream")
			filters = message.get("filters", {})

			if not stream:
				await ws_manager.send_message(connection_id, {
					"type": "error",
					"code": "MISSING_STREAM",
					"message": "Stream name is required"
				})
				return

			if stream not in ["arbs", "terminal", "ev"]:
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
			stream = message.get("stream")
			filters = message.get("filters", {})

			try:
				await ws_manager.update_filters(connection_id, filters, stream)
				await ws_manager.send_message(connection_id, {
					"type": "filters_updated",
					"stream": stream,
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
	await ws_manager.connect(websocket, connection_id)

	try:
		# --- Authentication phase ---
		try:
			auth_message = await asyncio.wait_for(
				websocket.receive_json(),
				timeout=10.0
			)
		except asyncio.TimeoutError:
			logger.warning(f"Authentication timeout for {connection_id}")
			await websocket.close(code=1008)
			return

		if auth_message.get("type") != "authenticate":
			await ws_manager.send_message(connection_id, {
				"type": "error",
				"code": "AUTH_REQUIRED",
				"message": "First message must be authentication"
			})
			await websocket.close(code=1008)
			return

		token = auth_message.get("token")
		if not token:
			await ws_manager.send_message(connection_id, {
				"type": "error",
				"code": "MISSING_TOKEN",
				"message": "Authentication token is required"
			})
			await websocket.close(code=1008)
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
			await websocket.close(code=1008)
			return

		# --- Main message loop ---
		# 90s timeout = 3× the client ping interval (30s).
		# If no message arrives within this window the client is presumed dead
		# (e.g. mobile browser backgrounded without sending a close frame).
		RECEIVE_TIMEOUT = 90

		while True:
			try:
				message = await asyncio.wait_for(
					websocket.receive_json(),
					timeout=RECEIVE_TIMEOUT
				)
				logger.info("Received message: %s", str(message)[:20])
			except asyncio.TimeoutError:
				logger.info(f"Client {connection_id} timed out (no message in {RECEIVE_TIMEOUT}s)")
				break
			except WebSocketDisconnect:
				logger.info(f"Client disconnected: {connection_id}")
				break
			except Exception as e:
				error_msg = str(e).lower()
				if "not connected" in error_msg or "close" in error_msg or "accept" in error_msg:
					logger.info(f"Client connection lost: {connection_id}")
					break

				logger.error(f"Error receiving message from {connection_id}: {e}")
				await ws_manager.send_message(connection_id, {
					"type": "error",
					"code": "MESSAGE_ERROR",
					"message": "Failed to process message"
				})
				continue

			await handle_message(connection_id, message)

	except WebSocketDisconnect:
		logger.info(f"Client disconnected during setup: {connection_id}")
	except Exception as e:
		logger.error(f"Fatal error in WebSocket connection {connection_id}: {e}")
	finally:
		await ws_manager.disconnect(connection_id)