from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from typing import Annotated, Optional
import logging

logger = logging.getLogger(__name__)

from app.config import get_settings, SPORTSBOOKS, TIER_FEATURES
from app.dependencies import get_firebase_user_from_token

from fastapi.responses import JSONResponse
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
