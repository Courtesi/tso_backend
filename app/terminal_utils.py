"""Utility functions for terminal data processing."""

import json
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import redis.asyncio as redis
from app.config import get_settings
from app.models import GameTerminalData, MarketLines, OutcomeLine, LineDataPoint

settings = get_settings()


async def fetch_games_with_lines(
    redis_client: redis.Redis,
    league: Optional[str] = None,
    game_time: Optional[str] = None
) -> List[GameTerminalData]:
    """
    Fetch all games with line movement data from Redis.

    Args:
        redis_client: Redis connection
        league: Filter by league (NBA, NFL, etc.)
        game_time: Filter by status (upcoming, live)

    Returns:
        List of games with complete line data
    """

    # Get all line keys
    pattern = "lines:*"
    line_keys = await redis_client.keys(pattern)

    if not line_keys:
        return []

    # Group keys by event_id
    events: Dict[str, Dict] = {}
    for key in line_keys:
        # Decode if bytes
        if isinstance(key, bytes):
            key = key.decode('utf-8')

        # Parse key: lines:{event_id}:{market_type}:{outcome_id}
        parts = key.split(":")
        if len(parts) != 4 or parts[0] != "lines":
            continue

        event_id = parts[1]
        market_type = parts[2]
        outcome_id = parts[3]

        if event_id not in events:
            events[event_id] = {"markets": {}}

        if market_type not in events[event_id]["markets"]:
            events[event_id]["markets"][market_type] = {}

        events[event_id]["markets"][market_type][outcome_id] = key

    # Build game objects
    games = []
    now = int(time.time())
    one_hour_ago = now - 3600

    for event_id, event_data in events.items():
        # Parse event_id to extract game info
        game_info = parse_event_id(event_id)

        # Apply league filter
        if league and game_info["league"].upper() != league.upper():
            continue

        # Apply game_time filter
        if game_time:
            game_status = get_game_status(game_info["start_time"])
            if game_status != game_time:
                continue

        # Build markets
        markets = []
        for market_type, outcomes_dict in event_data["markets"].items():
            outcome_lines = []

            for outcome_id, redis_key in outcomes_dict.items():
                # Fetch line history from sorted set
                raw_data = await redis_client.zrangebyscore(
                    redis_key,
                    one_hour_ago,
                    now,
                    withscores=True
                )

                if not raw_data:
                    continue

                history = []
                for member, score in raw_data:
                    # Decode if bytes
                    if isinstance(member, bytes):
                        member = member.decode('utf-8')

                    try:
                        data_point = json.loads(member)
                        history.append(LineDataPoint(**data_point))
                    except json.JSONDecodeError:
                        continue

                if not history:
                    continue

                # Find current best odds
                current_best = find_best_odds(history)

                outcome_line = OutcomeLine(
                    outcome_id=outcome_id,
                    outcome_name=format_outcome_name(outcome_id, market_type),
                    history=history,
                    current_best_odds=current_best["odds"],
                    current_best_sportsbook=current_best["sportsbook"]
                )
                outcome_lines.append(outcome_line)

            if outcome_lines:
                market = MarketLines(
                    market_type=market_type,
                    market_display=format_market_display(market_type),
                    outcomes=outcome_lines
                )
                markets.append(market)

        if markets:
            game = GameTerminalData(
                event_id=event_id,
                sport=game_info["sport"],
                league=game_info["league"],
                home_team=game_info["home_team"],
                away_team=game_info["away_team"],
                matchup=f"{game_info['away_team']} @ {game_info['home_team']}",
                start_time=game_info["start_time"],
                game_status=get_game_status(game_info["start_time"]),
                markets=markets
            )
            games.append(game)

    # Sort by start time
    games.sort(key=lambda g: g.start_time)

    return games


def parse_event_id(event_id: str) -> Dict:
    """
    Parse event_id to extract game components.

    Event ID is flexible, but we try to extract what we can.
    Falls back to using the event_id itself for missing info.

    Args:
        event_id: Event identifier string

    Returns:
        Dict with sport, league, home_team, away_team, start_time
    """
    # Since event_id format varies by sportsbook, we'll use a simple approach
    # The event_id typically contains team names separated by underscores
    # Example formats seen: "nba_lakers_warriors", "mlb_yankees_redsox_20260105"

    parts = event_id.lower().split("_")

    # Try to extract league from first part if it looks like a sport/league
    common_leagues = ["nba", "nfl", "mlb", "nhl", "ncaab", "ncaaf"]
    league = "Unknown"
    sport = "Unknown"

    if len(parts) > 0 and parts[0] in common_leagues:
        league = parts[0].upper()
        sport = get_sport_from_league(league)
        parts = parts[1:]  # Remove league from parts

    # Remaining parts are typically teams and maybe date/time
    # For now, use generic names
    if len(parts) >= 2:
        away_team = parts[0].title()
        home_team = parts[1].title()
    elif len(parts) == 1:
        away_team = parts[0].title()
        home_team = "TBD"
    else:
        away_team = "Unknown"
        home_team = "Unknown"

    # Default start time to now (will be overridden by actual data if available)
    start_time = datetime.now(timezone.utc).isoformat() + "Z"

    return {
        "sport": sport,
        "league": league,
        "away_team": away_team,
        "home_team": home_team,
        "start_time": start_time
    }


def get_sport_from_league(league: str) -> str:
    """Map league to sport."""
    league_sport_map = {
        "NBA": "Basketball",
        "NCAAB": "Basketball",
        "NFL": "Football",
        "NCAAF": "Football",
        "MLB": "Baseball",
        "NHL": "Hockey"
    }
    return league_sport_map.get(league.upper(), "Unknown")


def get_game_status(start_time_iso: str) -> str:
    """
    Determine game status based on start time.

    Args:
        start_time_iso: ISO format datetime string

    Returns:
        "upcoming", "live", or "completed"
    """
    try:
        start_time = datetime.fromisoformat(start_time_iso.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)

        # Simplified logic (can be enhanced with actual live game detection)
        if start_time > now:
            return "upcoming"
        elif start_time <= now < start_time + timedelta(hours=4):
            return "live"
        else:
            return "completed"
    except (ValueError, AttributeError):
        # If parsing fails, assume upcoming
        return "upcoming"


def find_best_odds(history: List[LineDataPoint]) -> Dict:
    """
    Find current best odds from history.

    Args:
        history: List of LineDataPoint objects

    Returns:
        Dict with odds and sportsbook
    """
    if not history:
        return {"odds": 0, "sportsbook": ""}

    # Get most recent data point (highest timestamp)
    latest = max(history, key=lambda x: x.timestamp)
    return {"odds": latest.odds, "sportsbook": latest.sportsbook}


def format_outcome_name(outcome_id: str, market_type: str) -> str:
    """
    Format outcome ID into display name.

    Args:
        outcome_id: Outcome identifier
        market_type: Market type (MONEY, SPREAD, TOTAL)

    Returns:
        Formatted display name
    """
    # Example transformations:
    # lakers_ml -> "Lakers ML"
    # over_220.5 -> "Over 220.5"
    # chiefs_-3.5 -> "Chiefs -3.5"

    parts = outcome_id.split("_")

    if market_type == "MONEY":
        # Moneyline: team name + ML
        if len(parts) >= 1:
            return f"{parts[0].title()} ML"
    elif market_type == "TOTAL":
        # Total: over/under + value
        if len(parts) >= 2:
            return f"{parts[0].title()} {parts[1]}"
    elif market_type == "SPREAD":
        # Spread: team + spread value
        if len(parts) >= 2:
            return f"{parts[0].title()} {parts[1]}"

    # Fallback: just capitalize
    return outcome_id.replace("_", " ").title()


def format_market_display(market_type: str) -> str:
    """
    Format market type for display.

    Args:
        market_type: Market type code

    Returns:
        Formatted display name
    """
    mapping = {
        "MONEY": "Moneyline",
        "SPREAD": "Spread",
        "TOTAL": "Total"
    }
    return mapping.get(market_type, market_type.title())
