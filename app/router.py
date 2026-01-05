import asyncio
import json
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from typing import Annotated, Optional
import logging
import redis.asyncio as redis

logger = logging.getLogger(__name__)

from app.config import get_settings
from app.dependencies import get_firebase_user_from_token, get_user_with_tier_from_query, get_user_with_tier_from_either
from app.redis import redis_client
from app import terminal_utils
import time

from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import HTTPException

from firebase_admin import auth
import stripe
import resend
from datetime import datetime, timezone

settings = get_settings()
router = APIRouter()

# Initialize 3rd party services
stripe.api_key = settings.STRIPE_SECRET_KEY
resend.api_key = settings.RESEND_API_KEY

@router.get("/health")
async def health_check():
	return {"status": "healthy"}

@router.get("/data/arbs/stream")
async def stream_arbs(request: Request, user: Annotated[dict, Depends(get_user_with_tier_from_query)]):
	tier = user.get("tier", "free")

	cache_key =  settings.PREMIUM_KEY_PREFIX if tier == "premium" else settings.FREE_KEY_PREFIX
	channel = f"{settings.REDIS_KEY_PREFIX}{cache_key}:updates"
	
	async def event_generator():
		pubsub_redis = redis.Redis(
			host=settings.REDIS_HOST,
			port=settings.REDIS_PORT,
			db=settings.REDIS_DB,
			password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
			decode_responses=True
		)

		try:
			pubsub = pubsub_redis.pubsub()
			await pubsub.subscribe(channel)

			cached_data = await redis_client.get(cache_key)
			if cached_data:
				arbs = cached_data.get("data", [])
				max_arbs = settings.TIER_MAX_ARBS.get(tier)
				if max_arbs:
					arbs = arbs[:max_arbs]
				
				response_data = {
					"tier": tier,
					"data": arbs,
					"metadata": cached_data.get("metadata"),
					"cached_at": cached_data.get("cached_at")
				}
				yield f"data: {json.dumps(response_data)}\n\n"
			else:
				yield f"data: {json.dumps({'tier': tier, 'data': [], 'message': 'No data available yet'})}\n\n"
			while True:
				# Check if client disconnected
				if await request.is_disconnected():
					break

				# Wait for message with timeout (so we can check disconnect status)
				message = await asyncio.wait_for(
					pubsub.get_message(ignore_subscribe_messages=True, timeout=1),
					timeout=2.0
				)

				if message and message["type"] == "message":
					# Parse the data
					cache_data = json.loads(message["data"])

					# Filter based on tier max arbs
					arbs = cache_data.get("data", [])
					max_arbs = settings.TIER_MAX_ARBS.get(tier)
					if max_arbs:
						arbs = arbs[:max_arbs]

					response_data = {
						"tier": tier,
						"data": arbs,
						"metadata": cache_data.get("metadata"),
						"cached_at": cache_data.get("cached_at")
					}

					# Send as SSE event
					yield f"data: {json.dumps(response_data)}\n\n"
				else:
					# No message, send heartbeat comment to keep connection alive
					yield ": heartbeat\n\n"
					await asyncio.sleep(15)
		except asyncio.CancelledError:
			# Client disconnected
			pass
		except Exception as e:
			logger.error(f"Error in SSE stream: {e}")
			yield f"data: {json.dumps({'error': 'Stream error occurred'})}\n\n"
		finally:
			# Cleanup
			await pubsub.unsubscribe(channel)
			await pubsub.close()
			await pubsub_redis.close()
		
	return StreamingResponse(
		event_generator(),
		media_type="text/event-stream",
		headers={
			"Cache-Control": "no-cache",
			"Connection": "keep-alive",
			"X-Accel-Buffering": "no",  # Disable nginx buffering
		}
	)
		
@router.get("/data/arbs")
async def get_arbs(user: Annotated[dict, Depends(get_user_with_tier_from_either)]):
	tier = user.get("tier", "free")
	logger.info(f"User tier: {tier}, stripeRole: {user.get('stripeRole')}")

	# Choose cache key based on tier
	if tier == "premium":
		cache_key = settings.PREMIUM_KEY_PREFIX
	else:
		cache_key = settings.FREE_KEY_PREFIX

	# Get cached data from Redis
	cached_data = await redis_client.get(cache_key)

	# logger.info(f"Cache key: {cache_key}, cached data: {cached_data}")

	if cached_data is None:
		return {
			"tier": tier,
			"data": [],
			"metadata": None,
			"message": "No arbitrage data available yet. Please try again shortly."
		}

	# Filter based on tier max arbs
	arbs = cached_data.get("data", [])
	max_arbs = settings.TIER_MAX_ARBS.get(tier)
	if max_arbs:
		arbs = arbs[:max_arbs]

	return {
		"tier": tier,
		"data": arbs,
		"metadata": cached_data.get("metadata"),
		"cached_at": cached_data.get("cached_at")
	}


# ==================== TERMINAL/LINE MOVEMENT ENDPOINTS ====================

@router.get("/data/terminal/stream")
async def stream_terminal_data(
	request: Request,
	user: Annotated[dict, Depends(get_user_with_tier_from_query)],
	league: Optional[str] = None,
	game_time: Optional[str] = None
):
	"""
	Stream terminal data (line movements) via SSE.

	Query params:
	- league: Filter by league (NBA, NFL, etc.)
	- game_time: Filter by game status (upcoming, live)
	"""
	tier = user.get("tier", "free")

	async def event_generator():
		pubsub_redis = redis.Redis(
			host=settings.REDIS_HOST,
			port=settings.REDIS_PORT,
			db=settings.REDIS_DB,
			password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
			decode_responses=True
		)

		try:
			# For now, we'll poll for data every 10 seconds and send updates
			# In production, this would subscribe to a pub/sub channel
			while True:
				# Check if client disconnected
				if await request.is_disconnected():
					break

				try:
					# Fetch games with lines from Redis
					games = await terminal_utils.fetch_games_with_lines(
						redis_client.redis,
						league=league,
						game_time=game_time
					)

					# Apply tier limits
					max_games = settings.TIER_MAX_GAMES.get(tier)
					if max_games and len(games) > max_games:
						games = games[:max_games]

					# Convert Pydantic models to dicts
					games_data = [game.model_dump() for game in games]

					response_data = {
						"tier": tier,
						"data": games_data,
						"metadata": {
							"count": len(games_data),
							"league": league or "all",
							"game_time": game_time or "all"
						},
						"cached_at": datetime.now(timezone.utc).isoformat() + "Z"
					}

					# Send as SSE event
					yield f"data: {json.dumps(response_data)}\n\n"

					# Wait before next update
					await asyncio.sleep(10)

				except Exception as e:
					logger.error(f"Error fetching terminal data: {e}")
					yield f"data: {json.dumps({'error': 'Failed to fetch terminal data'})}\n\n"
					await asyncio.sleep(10)

		except asyncio.CancelledError:
			# Client disconnected
			pass
		except Exception as e:
			logger.error(f"Error in terminal SSE stream: {e}")
			yield f"data: {json.dumps({'error': 'Stream error occurred'})}\n\n"
		finally:
			# Cleanup
			await pubsub_redis.close()

	return StreamingResponse(
		event_generator(),
		media_type="text/event-stream",
		headers={
			"Cache-Control": "no-cache",
			"Connection": "keep-alive",
			"X-Accel-Buffering": "no",
		}
	)


@router.get("/data/terminal")
async def get_terminal_data(
	user: Annotated[dict, Depends(get_user_with_tier_from_either)],
	league: Optional[str] = None,
	game_time: Optional[str] = None
):
	"""
	Polling fallback for terminal data (same filters as stream).
	"""
	tier = user.get("tier", "free")

	try:
		# Fetch games with lines from Redis
		games = await terminal_utils.fetch_games_with_lines(
			redis_client.redis,
			league=league,
			game_time=game_time
		)

		# Apply tier limits
		max_games = settings.TIER_MAX_GAMES.get(tier)
		if max_games and len(games) > max_games:
			games = games[:max_games]

		# Convert Pydantic models to dicts
		games_data = [game.model_dump() for game in games]

		return {
			"tier": tier,
			"data": games_data,
			"metadata": {
				"count": len(games_data),
				"league": league or "all",
				"game_time": game_time or "all"
			},
			"cached_at": datetime.now(timezone.utc).isoformat() + "Z"
		}

	except Exception as e:
		logger.error(f"Error fetching terminal data: {e}")
		return {
			"tier": tier,
			"data": [],
			"metadata": None,
			"message": f"Error fetching terminal data: {str(e)}"
		}


@router.get("/data/terminal/lines/{event_id}/{market_type}/{outcome_id}")
async def get_line_history(
	event_id: str,
	market_type: str,
	outcome_id: str,
	user: Annotated[dict, Depends(get_user_with_tier_from_either)],
	start_time: Optional[int] = None,
	end_time: Optional[int] = None
):
	"""
	Get historical line data for a specific outcome.

	Path params:
	- event_id: Game identifier
	- market_type: MONEY, SPREAD, or TOTAL
	- outcome_id: Specific outcome identifier

	Query params:
	- start_time: Unix timestamp (default: 1 hour ago)
	- end_time: Unix timestamp (default: now)
	"""
	# tier = user.get("tier", "free")

	# Construct Redis key
	key = f"lines:{event_id}:{market_type}:{outcome_id}"

	# Default time range: last hour
	now = int(time.time())
	start = start_time if start_time else now - 3600
	end = end_time if end_time else now

	try:
		# Get data from sorted set
		raw_data = await redis_client.redis.zrangebyscore(
			key,
			start,
			end,
			withscores=True
		)

		# Parse JSON members
		history = []
		for member, score in raw_data:
			# Decode if bytes
			if isinstance(member, bytes):
				member = member.decode('utf-8')

			try:
				data_point = json.loads(member)
				history.append(data_point)
			except json.JSONDecodeError:
				continue

		return {
			"event_id": event_id,
			"market_type": market_type,
			"outcome_id": outcome_id,
			"history": history,
			"count": len(history)
		}

	except Exception as e:
		logger.error(f"Error fetching line history: {e}")
		raise HTTPException(
			status_code=500,
			detail=f"Failed to fetch line history: {str(e)}"
		)


# ==================== USER MANAGEMENT ====================

@router.post("/delete-account")
async def delete_account(user: Annotated[dict, Depends(get_firebase_user_from_token)]):
	"""
	Deletes a user account and cancels any active Stripe subscriptions.
	The user must be authenticated (token verified by get_firebase_user_from_token).
	"""
	uid = user.get("uid")

	try:
		# Step 1: Find Stripe customer by Firebase UID
		customer_id = None
		for customer in stripe.Customer.list(limit=100).auto_paging_iter():
			if customer.metadata.get('firebaseUID') == uid:
				customer_id = customer.id
				break

		# Step 2: Cancel all active subscriptions if customer exists
		if customer_id:
			subscriptions = stripe.Subscription.list(
				customer=customer_id,
				status='all'
			)

			for subscription in subscriptions.auto_paging_iter():
				if subscription.status in ['active', 'trialing']:
					stripe.Subscription.cancel(subscription.id)

		# Step 3: Delete Firebase Auth user
		auth.delete_user(uid)

		return JSONResponse(
			content={"message": "Account deleted successfully"},
			status_code=200
		)

	except stripe.error.StripeError as e:
		raise HTTPException(
			status_code=400,
			detail=f"Stripe error while canceling subscription: {str(e)}"
		)
	except auth.UserNotFoundError:
		raise HTTPException(
			status_code=404,
			detail="User not found"
		)
	except Exception as e:
		raise HTTPException(
			status_code=500,
			detail=f"Internal server error: {str(e)}"
		)


# ==================== STRIPE CUSTOMER PORTAL ====================

@router.post("/create-portal-session")
async def create_portal_session(
	user: Annotated[dict, Depends(get_firebase_user_from_token)],
	return_url: str = None
):
	"""
	Creates a Stripe Customer Portal session for the authenticated user
	Returns the portal URL for redirect
	"""
	try:
		# Get user's UID from Firebase token
		uid = user.get("uid")

		# In Stripe, the customer ID is stored in Firestore at customers/{uid}
		# We need to get it from there, but for now we'll use the UID as customer ID
		# The Firebase Stripe extension creates customers with metadata['firebaseUID'] = uid

		# Search for the customer by Firebase UID in metadata
		# customers = stripe.Customer.list(limit=1).data
		customer_id = None

		# Try to find customer with matching Firebase UID
		for customer in stripe.Customer.list(limit=100).auto_paging_iter():
			if customer.metadata.get('firebaseUID') == uid:
				customer_id = customer.id
				break

		if not customer_id:
			raise HTTPException(
				status_code=404,
				detail="No Stripe customer found for this user. Please complete a purchase first."
			)

		# Create the portal session
		session = stripe.billing_portal.Session.create(
			customer=customer_id,
			return_url=return_url or settings.FRONTEND_URL,
		)

		return {"url": session.url}

	except stripe.error.StripeError as e:
		raise HTTPException(
			status_code=400,
			detail=f"Stripe error: {str(e)}"
		)
	except Exception as e:
		raise HTTPException(
			status_code=500,
			detail=f"Internal server error: {str(e)}"
		)


# ==================== BUG REPORTING ====================

@router.post("/create-bug-report")
async def submit_bug_report(
	title: Annotated[str, Form()],
	description: Annotated[str, Form()],
	category: Annotated[str, Form()],
	url: Annotated[str, Form()],
	userAgent: Annotated[str, Form()],
	screenshot: Annotated[Optional[UploadFile], File()] = None
):
	"""
	Public endpoint for submitting bug reports
	Sends email notification to support team
	"""
	try:
		# Prepare email content
		timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

		# Build HTML email body
		html_content = f"""
		<html>
			<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
				<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px 10px 0 0;">
					<h1 style="color: white; margin: 0;">🐛 New Bug Report</h1>
				</div>

				<div style="background: #f9fafb; padding: 20px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 10px 10px;">
					<div style="background: white; padding: 15px; border-radius: 8px; margin-bottom: 15px;">
						<p style="margin: 0; color: #6b7280; font-size: 12px;">CATEGORY</p>
						<p style="margin: 5px 0 0 0; color: #1f2937; font-size: 16px; font-weight: 600;">{category}</p>
					</div>

					<div style="background: white; padding: 15px; border-radius: 8px; margin-bottom: 15px;">
						<p style="margin: 0; color: #6b7280; font-size: 12px;">TITLE</p>
						<p style="margin: 5px 0 0 0; color: #1f2937; font-size: 16px; font-weight: 600;">{title}</p>
					</div>

					<div style="background: white; padding: 15px; border-radius: 8px; margin-bottom: 15px;">
						<p style="margin: 0; color: #6b7280; font-size: 12px;">DESCRIPTION</p>
						<p style="margin: 5px 0 0 0; color: #1f2937; white-space: pre-wrap;">{description}</p>
					</div>

					<div style="background: white; padding: 15px; border-radius: 8px; margin-bottom: 15px;">
						<p style="margin: 0; color: #6b7280; font-size: 12px;">CONTEXT</p>
						<p style="margin: 5px 0 0 0; color: #1f2937;"><strong>Page URL:</strong> {url}</p>
						<p style="margin: 5px 0 0 0; color: #1f2937;"><strong>Browser:</strong> {userAgent}</p>
						<p style="margin: 5px 0 0 0; color: #6b7280; font-size: 12px;"><strong>Reported at:</strong> {timestamp}</p>
					</div>
				</div>
			</body>
		</html>
		"""

		# Prepare email params
		email_params = {
			"from": f"Trueshot <{str(settings.RESEND_EMAIL)}>",
			"to": [str(settings.RESEND_EMAIL)],
			"subject": f"[Bug Report] {category}: {title}",
			"html": html_content,
		}

		# Handle screenshot attachment if provided
		if screenshot and screenshot.filename:
			# Read file content
			file_content = await screenshot.read()

			# Add attachment to email
			email_params["attachments"] = [{
				"filename": screenshot.filename,
				"content": list(file_content),
			}]

		# Send email using Resend
		response = resend.Emails.send(email_params)

		return JSONResponse(
			content={
				"message": "Bug report submitted successfully",
				"id": response.get("id")
			},
			status_code=200
		)

	except Exception as e:
		logger.debug(f"Failed to submit bug report: {str(e)}")
		raise HTTPException(
			status_code=500,
			detail=f"Failed to submit bug report: {str(e)}"
		)
