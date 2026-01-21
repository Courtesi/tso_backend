"""Utility functions for filtering arbitrage and terminal data."""

import logging
from typing import List, Set

from app.config import get_settings
from app import terminal_utils

logger = logging.getLogger(__name__)
settings = get_settings()


def apply_arb_filters(arbs: List[dict], filters: dict, tier: str) -> List[dict]:
    """
    Apply filters to arbitrage data.

    Args:
        arbs: List of arbitrage opportunities
        filters: Filter configuration dict with keys:
            - sportsbooks: List of sportsbook names to include (optional)
        tier: User tier ("free" or "premium")

    Returns:
        Filtered list of arbitrage opportunities
    """
    filtered = arbs

    # Apply tier limits first
    max_arbs = settings.TIER_MAX_ARBS.get(tier)
    if max_arbs:
        filtered = filtered[:max_arbs]

    # Apply minimum profit filter
    min_profit = filters.get("min_profit")
    if min_profit is not None:
        try:
            min_profit_val = float(min_profit)
            if min_profit_val > 0:
                filtered = [
                    arb for arb in filtered
                    if arb.get("profit_percentage", 0) >= min_profit_val
                ]
        except (ValueError, TypeError):
            pass

    # Apply maximum profit filter
    max_profit = filters.get("max_profit")
    logger.debug(f"max_profit filter value: {max_profit} (type: {type(max_profit).__name__})")
    if max_profit is not None:
        try:
            max_profit_val = float(max_profit)
            before_count = len(filtered)
            filtered = [
                arb for arb in filtered
                if arb.get("profit_percentage", 0) <= max_profit_val
            ]
            logger.debug(f"max_profit filter applied: {before_count} -> {len(filtered)} arbs (max: {max_profit_val}%)")
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to apply max_profit filter: {e}")

    # Apply league filter
    league = filters.get("league")
    if league and league.lower() != "all":
        filtered = [
            arb for arb in filtered
            if arb.get("league", "").upper() == league.upper()
        ]

    # Apply market type filter
    market_type = filters.get("market_type")
    if market_type:
        if isinstance(market_type, str):
            market_types = [market_type.lower()]
        elif isinstance(market_type, list) and len(market_type) > 0:
            market_types = [m.lower() for m in market_type]
        else:
            market_types = None

        if market_types:
            filtered = [
                arb for arb in filtered
                if arb.get("market", "").lower() in market_types
            ]

    # Apply sportsbook filter
    sportsbooks_filter = filters.get("sportsbooks")
    if sportsbooks_filter and isinstance(sportsbooks_filter, list) and len(sportsbooks_filter) > 0:
        sportsbooks_set = set(sb.lower() for sb in sportsbooks_filter)
        filtered = [
            arb for arb in filtered
            if arb.get("bet1", {}).get("sportsbook", "").lower() in sportsbooks_set
            or arb.get("bet2", {}).get("sportsbook", "").lower() in sportsbooks_set
        ]

    return filtered


def apply_terminal_filters(games: List[dict], filters: dict, tier: str) -> List[dict]:
    """
    Apply filters to terminal/charts data.

    Args:
        games: List of game data
        filters: Filter configuration dict with keys:
            - league: League filter ("NBA", "NFL", etc. or None for all)
            - game_time: Game status filter ("upcoming", "live", or None)
            - sportsbooks: List of sportsbook names to include (optional)
        tier: User tier ("free" or "premium")

    Returns:
        Filtered list of games
    """
    filtered = games

    # Apply tier-based league restrictions first (before user's league filter)
    allowed_leagues = settings.TIER_ALLOWED_LEAGUES.get(tier)
    if allowed_leagues:
        allowed_set = set(league.upper() for league in allowed_leagues)
        filtered = [g for g in filtered if g.get("league", "").upper() in allowed_set]

    # User's league filter (further narrows down if specified)
    league = filters.get("league")
    if league and league != "all":
        filtered = [g for g in filtered if g.get("league", "").upper() == league.upper()]

    # Game time filter
    game_time = filters.get("game_time")
    if game_time:
        filtered = terminal_utils.filter_terminal_data(filtered, game_time=game_time)

    # Apply tier limits
    max_games = settings.TIER_MAX_GAMES.get(tier)
    if max_games:
        filtered = filtered[:max_games]

    # Sportsbook filter (filter outcomes within each game)
    sportsbooks_filter = filters.get("sportsbooks")
    if sportsbooks_filter and isinstance(sportsbooks_filter, list) and len(sportsbooks_filter) > 0:
        sportsbooks_set = set(sb.lower() for sb in sportsbooks_filter)
        filtered = apply_sportsbook_filter_to_games(filtered, sportsbooks_set)

    return filtered


def apply_sportsbook_filter_to_games(games: List[dict], sportsbooks: Set[str]) -> List[dict]:
    """
    Filter line history by selected sportsbooks.

    This function filters the line movement history within each game's markets
    to only include data from the specified sportsbooks. It also recalculates
    the best odds based on the filtered data.

    Args:
        games: List of game dictionaries with market and outcome data
        sportsbooks: Set of sportsbook names (lowercased) to include

    Returns:
        Filtered list of games (only games with data from selected sportsbooks)
    """
    filtered_games = []

    for game in games:
        filtered_game = game.copy()
        filtered_markets = []

        markets = game.get("markets", [])
        for market in markets:
            filtered_outcomes = []

            outcomes = market.get("outcomes", [])
            for outcome in outcomes:
                # Filter history to only include selected sportsbooks
                history = outcome.get("history", [])
                filtered_history = [
                    point for point in history
                    if point.get("sportsbook", "").lower() in sportsbooks
                ]

                if filtered_history:  # Only include outcome if it has data
                    filtered_outcome = outcome.copy()
                    filtered_outcome["history"] = filtered_history

                    # Recalculate best odds from filtered history
                    latest = max(filtered_history, key=lambda x: x.get("timestamp", 0))
                    filtered_outcome["current_best_odds"] = latest.get("odds")
                    filtered_outcome["current_best_sportsbook"] = latest.get("sportsbook")

                    filtered_outcomes.append(filtered_outcome)

            if filtered_outcomes:  # Only include market if it has outcomes
                filtered_market = market.copy()
                filtered_market["outcomes"] = filtered_outcomes
                filtered_markets.append(filtered_market)

        if filtered_markets:  # Only include game if it has markets
            filtered_game["markets"] = filtered_markets
            filtered_games.append(filtered_game)

    return filtered_games
