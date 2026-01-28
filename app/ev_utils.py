"""Utility functions for Expected Value (EV) bet calculations."""

import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Configuration constants
MIN_LIQUIDITY_DOLLARS = 10000  # Minimum liquidity to trust prediction market odds
MIN_VOLUME = 100  # Minimum volume (contracts/USDC)
MIN_EV_PERCENTAGE = 1.0  # Minimum EV% to display a bet

# Prediction market platforms (used as "true odds" sources)
PREDICTION_MARKETS = {"kalshi", "polymarket"}


def american_to_probability(odds: int) -> float:
    """
    Convert American odds to implied probability.

    Args:
        odds: American odds (e.g., -110, +150)

    Returns:
        Implied probability as a decimal (0-1)
    """
    if odds >= 100:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def probability_to_american(prob: float) -> int:
    """
    Convert probability to American odds.

    Args:
        prob: Probability as a decimal (0-1)

    Returns:
        American odds (negative for favorites, positive for underdogs)
    """
    if prob <= 0 or prob >= 1:
        return 0

    if prob >= 0.5:
        # Favorite (negative odds)
        return int(-100 * prob / (1 - prob))
    else:
        # Underdog (positive odds)
        return int(100 * (1 - prob) / prob)


def calculate_ev(sportsbook_odds: int, true_probability: float) -> float:
    """
    Calculate Expected Value percentage.

    EV = (True Probability * Potential Profit) - (1 - True Probability) * Stake
    For a $1 stake: EV = (True Probability * (Payout - 1)) - (1 - True Probability)

    Args:
        sportsbook_odds: American odds from the sportsbook
        true_probability: True probability from prediction market (0-1)

    Returns:
        Expected Value as a percentage
    """
    if true_probability <= 0 or true_probability >= 1:
        return 0.0

    # Calculate payout multiplier from sportsbook odds
    if sportsbook_odds >= 100:
        payout_multiplier = (sportsbook_odds / 100) + 1
    else:
        payout_multiplier = (100 / abs(sportsbook_odds)) + 1

    # EV = (prob * payout) - 1
    # Expressed as percentage
    ev = ((true_probability * payout_multiplier) - 1) * 100

    return round(ev, 2)


def calculate_edge(sportsbook_odds: int, true_probability: float) -> float:
    """
    Calculate the edge (difference between true probability and implied probability).

    Args:
        sportsbook_odds: American odds from the sportsbook
        true_probability: True probability from prediction market (0-1)

    Returns:
        Edge as a percentage
    """
    implied_prob = american_to_probability(sportsbook_odds)
    edge = (true_probability - implied_prob) * 100
    return round(edge, 2)


def calculate_confidence_score(liquidity: float | None, volume: float | None) -> float:
    """
    Calculate confidence score based on liquidity and volume.

    Args:
        liquidity: Total liquidity in dollars
        volume: Trading volume in contracts/USDC

    Returns:
        Confidence score from 0 to 1
    """
    if liquidity is None and volume is None:
        return 0.0

    score = 0.0

    # Liquidity component (0-0.5)
    if liquidity is not None:
        if liquidity >= 100000:
            score += 0.5
        elif liquidity >= 50000:
            score += 0.4
        elif liquidity >= 25000:
            score += 0.3
        elif liquidity >= MIN_LIQUIDITY_DOLLARS:
            score += 0.2
        else:
            score += 0.1

    # Volume component (0-0.5)
    if volume is not None:
        if volume >= 10000:
            score += 0.5
        elif volume >= 5000:
            score += 0.4
        elif volume >= 1000:
            score += 0.3
        elif volume >= MIN_VOLUME:
            score += 0.2
        else:
            score += 0.1

    return min(score, 1.0)


def confidence_to_label(score: float) -> str:
    """
    Convert confidence score to human-readable label.

    Args:
        score: Confidence score (0-1)

    Returns:
        "HIGH", "MEDIUM", or "LOW"
    """
    if score >= 0.7:
        return "HIGH"
    elif score >= 0.4:
        return "MEDIUM"
    else:
        return "LOW"


def calculate_kelly_fraction(odds: int, true_prob: float) -> float:
    """
    Calculate the Kelly Criterion fraction for optimal bet sizing.

    Kelly % = (bp - q) / b
    where:
        b = decimal odds - 1 (net profit per $1 bet)
        p = true probability of winning
        q = 1 - p (probability of losing)

    Args:
        odds: American odds from the sportsbook
        true_prob: True probability from prediction market (0-1)

    Returns:
        Kelly fraction as a decimal (0-1), capped at 0.25 for safety
    """
    if true_prob <= 0 or true_prob >= 1:
        return 0.0

    # Convert American odds to decimal odds
    if odds >= 100:
        decimal_odds = (odds / 100) + 1
    else:
        decimal_odds = (100 / abs(odds)) + 1

    b = decimal_odds - 1  # Net profit per $1 bet
    p = true_prob
    q = 1 - p

    # Kelly formula
    kelly = (b * p - q) / b

    # Only return positive values, cap at 25% for safety
    if kelly <= 0:
        return 0.0

    return min(round(kelly, 4), 0.25)


def find_ev_bets(merged_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Find +EV betting opportunities by comparing sportsbook odds to prediction market odds.

    Args:
        merged_events: List of merged event data from ArbFinder

    Returns:
        List of EV bet opportunities
    """
    ev_bets = []
    bet_id = 0

    for event in merged_events:
        event_id = event.get("event_id", "")
        league = event.get("league", "")
        matchup = event.get("matchup", "")
        game_time = event.get("start_time", "")

        markets = event.get("markets", [])

        for market in markets:
            market_type = market.get("market_type", "")
            market_display = market.get("market_display", "")

            outcomes = market.get("outcomes", [])

            for outcome in outcomes:
                outcome_name = outcome.get("outcome_name", "")
                sportsbooks_data = outcome.get("sportsbooks", {})

                # Find prediction market data (true odds source)
                prediction_market_data = None
                prediction_platform = None

                for platform in PREDICTION_MARKETS:
                    if platform in sportsbooks_data:
                        pm_data = sportsbooks_data[platform]
                        liquidity = pm_data.get("liquidity")
                        volume = pm_data.get("volume")

                        # Check minimum liquidity/volume requirements
                        has_sufficient_liquidity = (
                            liquidity is not None and liquidity >= MIN_LIQUIDITY_DOLLARS
                        )
                        has_sufficient_volume = (
                            volume is not None and volume >= MIN_VOLUME
                        )

                        if has_sufficient_liquidity or has_sufficient_volume:
                            prediction_market_data = pm_data
                            prediction_platform = platform
                            break

                if not prediction_market_data:
                    continue

                # Get true probability from prediction market
                pm_odds = prediction_market_data.get("odds")
                if pm_odds is None:
                    continue

                true_probability = american_to_probability(int(pm_odds))
                pm_liquidity = prediction_market_data.get("liquidity")
                pm_volume = prediction_market_data.get("volume")

                # Calculate confidence score
                confidence_score = calculate_confidence_score(pm_liquidity, pm_volume)
                confidence_label = confidence_to_label(confidence_score)

                # Compare against each sportsbook
                for sportsbook, sb_data in sportsbooks_data.items():
                    # Skip prediction markets (we're using them as true odds)
                    if sportsbook.lower() in PREDICTION_MARKETS:
                        continue

                    sb_odds = sb_data.get("odds")
                    if sb_odds is None:
                        continue

                    sb_odds = int(sb_odds)

                    # Calculate EV
                    ev = calculate_ev(sb_odds, true_probability)

                    # Only include if EV meets minimum threshold
                    if ev < MIN_EV_PERCENTAGE:
                        continue

                    # Calculate edge and Kelly fraction
                    edge = calculate_edge(sb_odds, true_probability)
                    kelly = calculate_kelly_fraction(sb_odds, true_probability)

                    bet_id += 1

                    ev_bet = {
                        "id": f"ev_{event_id}_{market_type}_{sportsbook}_{bet_id}",
                        "league": league,
                        "matchup": matchup,
                        "market": market_display,
                        "game_time": game_time,
                        "bet": {
                            "team": outcome_name,
                            "odds": sb_odds,
                            "sportsbook": sportsbook,
                            "implied_probability": round(american_to_probability(sb_odds), 4)
                        },
                        "true_odds": {
                            "platform": prediction_platform.capitalize() if prediction_platform else "",
                            "probability": round(true_probability, 4),
                            "american_odds": probability_to_american(true_probability),
                            "liquidity": pm_liquidity,
                            "volume": pm_volume,
                            "confidence_score": round(confidence_score, 2)
                        },
                        "expected_value": ev,
                        "edge": edge,
                        "kelly_fraction": kelly,
                        "confidence": confidence_label,
                        "found_at": datetime.now(timezone.utc).isoformat() + "Z"
                    }

                    ev_bets.append(ev_bet)

    # Sort by EV percentage (highest first)
    ev_bets.sort(key=lambda x: x["expected_value"], reverse=True)

    return ev_bets
