from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from typing import Annotated, Optional
import json
import logging
import time

logger = logging.getLogger(__name__)

from app.config import get_settings, SPORTSBOOKS, TIER_FEATURES
from app.dependencies import get_firebase_user_from_token, get_user_with_tier
from app.filter_utils import apply_terminal_tier_filters
from app.redis import redis_client as shared_redis

from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException

from firebase_admin import auth
import stripe
import resend
from datetime import datetime, timezone, timedelta

settings = get_settings()
router = APIRouter()

# Initialize 3rd party services
stripe.api_key = settings.STRIPE_SECRET_KEY
resend.api_key = settings.RESEND_API_KEY


@router.get("/health")
async def health_check():
    return {"status": "healthy"}


# ==================== PUBLIC CONFIG ENDPOINTS ====================


@router.get("/config/sportsbooks")
async def get_sportsbooks():
    """
    Returns sportsbook configuration data (icons and display names).
    Public endpoint - no authentication required.
    """
    return {"sportsbooks": SPORTSBOOKS}


@router.get("/config/tiers")
async def get_tiers():
    """
    Returns tier features configuration for the subscription page.
    Also includes tier limits (allowed leagues, max games, etc).
    Public endpoint - no authentication required.
    """
    # Combine display features with tier limits
    tier_data = {}
    for tier_name, features in TIER_FEATURES.items():
        tier_data[tier_name] = {
            **features,
            "allowed_leagues": settings.TIER_ALLOWED_LEAGUES.get(tier_name),
            "max_games": settings.TIER_MAX_GAMES.get(tier_name),
            "max_arbs": settings.TIER_MAX_ARBS.get(tier_name),
        }

    return {
        "tiers": tier_data,
        "all_leagues": settings.ALL_LEAGUES,
    }


# ==================== TERMINAL / LINE HISTORY ====================


@router.get("/terminal/lines")
async def get_terminal_lines(user: Annotated[dict, Depends(get_user_with_tier)]):
    """
    Returns line history from Redis sorted sets, transformed into
    the GameTerminalData format the frontend expects.
    Called once on page load to bootstrap chart state.
    """
    r = shared_redis.redis
    if not r:
        raise HTTPException(status_code=503, detail="Redis not available")

    now = int(time.time())
    window_start = now - settings.LINES_TTL

    # 1. Load all event metadata
    event_keys = [k async for k in r.scan_iter(match="event:*")]
    if not event_keys:
        return {"data": [], "metadata": {"count": 0}}

    event_values = await r.mget(event_keys)
    events = {}
    for raw in event_values:
        if not raw:
            continue
        meta = json.loads(raw)
        events[meta["event_id"]] = meta

    # 2. Load all line keys and group by event
    line_keys = [k async for k in r.scan_iter(match="lines:*")]

    # Group: event_id -> market_type -> outcome_id -> key
    grouped = {}
    for key in line_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        parts = key_str.split(":")
        if len(parts) != 4:
            continue
        _, event_id, market_type, outcome_name = parts
        grouped.setdefault(event_id, {}).setdefault(market_type, {})[outcome_name] = key

    # 3. Build games
    games = []
    for event_id, markets in grouped.items():
        meta = events.get(event_id)
        if not meta:
            continue

        home = meta.get("home_team", "Unknown")
        away = meta.get("away_team", "Unknown")

        # Map lowercased team names to their proper display form
        team_display = {home.lower(): home, away.lower(): away}

        market_list = []
        for market_type, outcomes in markets.items():
            outcome_list = []
            for outcome_name, redis_key in outcomes.items():
                raw_members = await r.zrangebyscore(redis_key, window_start, now)
                if not raw_members:
                    continue

                history = []
                history_by_sportsbook = {}
                for member in raw_members:
                    point = json.loads(member)
                    history.append(point)
                    sb = point.get("sportsbook")
                    if sb:
                        history_by_sportsbook.setdefault(sb, []).append(point)

                latest = max(history, key=lambda x: x["timestamp"])

                # Recover proper capitalization from event metadata
                display_name = team_display.get(outcome_name)
                if not display_name:
                    # Spread/total: try matching the team prefix (e.g. "celtics -3.5")
                    for lower_team, proper_team in team_display.items():
                        if outcome_name.startswith(lower_team):
                            display_name = proper_team + outcome_name[len(lower_team) :]
                            break
                    else:
                        display_name = outcome_name.title()

                outcome_list.append(
                    {
                        "outcome_id": redis_key,
                        "outcome_name": display_name,
                        "history": history,
                        "current_best_odds": latest["odds"],
                        "current_best_sportsbook": latest["sportsbook"],
                        "history_by_sportsbook": history_by_sportsbook,
                    }
                )

            if not outcome_list:
                continue

            display = {"MONEY": "Moneyline", "SPREAD": "Spread", "TOTAL": "Total"}
            market_list.append(
                {
                    "market_type": market_type,
                    "market_display": display.get(market_type, market_type.title()),
                    "outcomes": outcome_list,
                }
            )

        if not market_list:
            continue
        start_time = meta.get("start_time", "")

        game_status = "upcoming"
        try:
            st = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            utc_now = datetime.now(timezone.utc)
            if st <= utc_now < st + timedelta(hours=4):
                game_status = "live"
            elif st + timedelta(hours=4) <= utc_now:
                game_status = "completed"
        except (ValueError, AttributeError):
            pass

        games.append(
            {
                "event_id": event_id,
                "sport": meta.get("sport", "Unknown"),
                "league": meta.get("league", "Unknown"),
                "home_team": home,
                "away_team": away,
                "matchup": f"{away} @ {home}",
                "start_time": start_time,
                "game_status": game_status,
                "markets": market_list,
            }
        )

    games.sort(key=lambda g: g.get("start_time", ""))

    tier = user.get("tier", "free")
    games = apply_terminal_tier_filters(games, tier)

    return {
        "tier": tier,
        "data": games,
        "metadata": {"count": len(games)},
        "cached_at": datetime.now(timezone.utc).isoformat() + "Z",
    }


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
            if customer.metadata.get("firebaseUID") == uid:
                customer_id = customer.id
                break

        # Step 2: Cancel all active subscriptions if customer exists
        if customer_id:
            subscriptions = stripe.Subscription.list(customer=customer_id, status="all")

            for subscription in subscriptions.auto_paging_iter():
                if subscription.status in ["active", "trialing"]:
                    stripe.Subscription.cancel(subscription.id)

        # Step 3: Delete Firebase Auth user
        auth.delete_user(uid)

        return JSONResponse(
            content={"message": "Account deleted successfully"}, status_code=200
        )

    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Stripe error while canceling subscription: {str(e)}",
        )
    except auth.UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# ==================== STRIPE CUSTOMER PORTAL ====================


@router.post("/create-portal-session")
async def create_portal_session(
    user: Annotated[dict, Depends(get_firebase_user_from_token)],
    return_url: Optional[str] = Body(None, alias="returnUrl", embed=True),
):
    """
    Creates a Stripe Customer Portal session for the authenticated user
    Returns the portal URL for redirect
    """
    try:
        # Get user's UID from Firebase token
        uid = user.get("uid")

        # Search for the customer by Firebase UID using Stripe Search API
        result = stripe.Customer.search(query=f'metadata["firebaseUID"]:"{uid}"')

        if not result.data:
            raise HTTPException(
                status_code=404,
                detail="No Stripe customer found for this user. Please complete a purchase first.",
            )

        customer_id = result.data[0].id

        # Create the portal session
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url or settings.FRONTEND_URL,
        )

        return {"url": session.url}

    except stripe.StripeError as e:
        logger.error(f"Stripe error creating portal session: {e}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating portal session: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# ==================== BUG REPORTING ====================


@router.post("/create-bug-report")
async def submit_bug_report(
    title: Annotated[str, Form()],
    description: Annotated[str, Form()],
    category: Annotated[str, Form()],
    url: Annotated[str, Form()],
    userAgent: Annotated[str, Form()],
    screenshot: Annotated[Optional[UploadFile], File()] = None,
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
            email_params["attachments"] = [
                {
                    "filename": screenshot.filename,
                    "content": list(file_content),
                }
            ]

        # Send email using Resend
        response = resend.Emails.send(email_params)

        return JSONResponse(
            content={
                "message": "Bug report submitted successfully",
                "id": response.get("id"),
            },
            status_code=200,
        )

    except Exception as e:
        logger.debug(f"Failed to submit bug report: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to submit bug report: {str(e)}"
        )
