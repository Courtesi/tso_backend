from fastapi import Depends, HTTPException, status, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin.auth import verify_id_token

from typing import Annotated, Optional
import logging

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)

def get_firebase_user_from_token(token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]):
	"""Uses bearer token to identify firebase user id
	Args:
		token : the bearer token. Can be None as we set auto_error to False
	Returns:
		dict: the firebase user on success
	Raises:
		HTTPException 401 if user does not exist or token is invalid
	"""
	try:
		if not token:
			# raise and catch to return 401, only needed because fastapi returns 403
			# by default instead of 401 so we set auto_error to False
			raise ValueError("No token")
		user = verify_id_token(token.credentials)
		return user
	# lots of possible exceptions, see firebase_admin.auth,
	# but most of the time it is a credentials issue
	except Exception:
		# we also set the header
		# see https://fastapi.tiangolo.com/tutorial/security/simple-oauth2/
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Not logged in or Invalid credentials",
			headers={"WWW-Authenticate": "Bearer"},
		)


def get_user_with_tier(user: Annotated[dict, Depends(get_firebase_user_from_token)]) -> dict:
    """
    Dependency that extracts user info and tier from Firebase token.

    Args:
        user: Verified Firebase user dict from token

    Returns:
        dict: The user dict with added 'tier' key
    """
    # Get tier from stripeRole custom claim
    stripe_role = user.get("stripeRole")
    tier = stripe_role if stripe_role else "free"

    # Add tier to user dict for use in endpoint
    user["tier"] = tier

    # logger.debug(f"User {user.get('uid')} authenticated with tier: {tier}")

    return user


def get_firebase_user_from_query_token(token: Annotated[Optional[str], Query()] = None):
	"""Uses query parameter token to identify firebase user (for SSE endpoints)
	Args:
		token : the token from query parameter. Can be None
	Returns:
		dict: the firebase user on success
	Raises:
		HTTPException 401 if user does not exist or token is invalid
	"""
	try:
		if not token:
			raise ValueError("No token")
		user = verify_id_token(token)
		return user
	except Exception:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Not logged in or Invalid credentials",
			headers={"WWW-Authenticate": "Bearer"},
		)


def get_user_with_tier_from_query(user: Annotated[dict, Depends(get_firebase_user_from_query_token)]) -> dict:
    """
    Dependency that extracts user info and tier from Firebase token (query param version).

    Args:
        user: Verified Firebase user dict from token

    Returns:
        dict: The user dict with added 'tier' key
    """
    # Get tier from stripeRole custom claim
    stripe_role = user.get("stripeRole")
    tier = stripe_role if stripe_role else "free"

    # Add tier to user dict for use in endpoint
    user["tier"] = tier

    return user


def get_firebase_user_from_either(
    bearer_token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    query_token: Annotated[Optional[str], Query(alias="token")] = None
):
    """Uses EITHER bearer token OR query parameter to identify firebase user
    Args:
        bearer_token: Bearer token from Authorization header (can be None)
        query_token: Token from query parameter (can be None)
    Returns:
        dict: the firebase user on success
    Raises:
        HTTPException 401 if user does not exist or token is invalid
    """
    try:
        # Try bearer token first
        if bearer_token:
            user = verify_id_token(bearer_token.credentials)
            return user
        # Fall back to query token
        elif query_token:
            user = verify_id_token(query_token)
            return user
        else:
            raise ValueError("No token provided")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not logged in or Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_user_with_tier_from_either(user: Annotated[dict, Depends(get_firebase_user_from_either)]) -> dict:
    """
    Dependency that extracts user info and tier from Firebase token (accepts both header and query param).

    Args:
        user: Verified Firebase user dict from token

    Returns:
        dict: The user dict with added 'tier' key
    """
    # Get tier from stripeRole custom claim
    stripe_role = user.get("stripeRole")
    tier = stripe_role if stripe_role else "free"

    # Add tier to user dict for use in endpoint
    user["tier"] = tier

    return user