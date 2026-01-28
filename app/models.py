from pydantic import BaseModel

class LoginSchema(BaseModel):
	email:str
	password:str

	class Config:
		json_schema_extra ={
			"example":{
				"email":"sample@gmail.com",
				"password":"samplepass123"
			}
		}


# ==================== ARBITRAGE BET MODELS ====================

class BetSide(BaseModel):
	"""Individual bet side in an arbitrage opportunity"""
	team: str
	odds: float
	sportsbook: str
	stake: float  # Amount to bet (out of $100 total)

	class Config:
		json_schema_extra = {
			"example": {
				"team": "Lakers",
				"odds": 2.10,
				"sportsbook": "DraftKings",
				"stake": 47.62
			}
		}


class ArbitrageBet(BaseModel):
	"""Complete arbitrage betting opportunity with two opposing bets"""
	id: int
	sport: str
	matchup: str
	market: str  # Bet type: "Moneyline", "Spread", "Total", etc.
	game_time: str  # ISO format datetime string for when the game starts
	profit_percentage: float
	bet1: BetSide
	bet2: BetSide
	found_at: str  # ISO format datetime string
	expires_in_minutes: int

	class Config:
		json_schema_extra = {
			"example": {
				"id": 1,
				"sport": "NBA",
				"matchup": "Lakers vs Warriors",
				"profit_percentage": 3.45,
				"bet1": {
					"team": "Lakers",
					"odds": 2.10,
					"sportsbook": "DraftKings",
					"stake": 47.62
				},
				"bet2": {
					"team": "Warriors",
					"odds": 2.05,
					"sportsbook": "FanDuel",
					"stake": 52.38
				},
				"found_at": "2025-11-17T14:30:00Z",
				"expires_in_minutes": 15
			}
		}


# ==================== TERMINAL/LINE MOVEMENT MODELS ====================

class LineDataPoint(BaseModel):
	"""Single odds data point in time series"""
	odds: float
	sportsbook: str
	timestamp: int  # Unix timestamp

	class Config:
		json_schema_extra = {
			"example": {
				"odds": -110,
				"sportsbook": "DraftKings",
				"timestamp": 1704461400
			}
		}


class OutcomeLine(BaseModel):
	"""Complete line history for a specific outcome"""
	outcome_id: str
	outcome_name: str  # "Lakers ML", "Over 220.5", "Chiefs -3.5"
	history: list[LineDataPoint]  # Time-ordered
	current_best_odds: float
	current_best_sportsbook: str
	history_by_sportsbook: dict[str, list[LineDataPoint]] | None = None  # Optional: grouped by sportsbook

	class Config:
		json_schema_extra = {
			"example": {
				"outcome_id": "lakers_ml",
				"outcome_name": "Lakers ML",
				"history": [
					{"odds": -110, "sportsbook": "DraftKings", "timestamp": 1704461400},
					{"odds": -115, "sportsbook": "DraftKings", "timestamp": 1704461410}
				],
				"current_best_odds": -110,
				"current_best_sportsbook": "FanDuel",
				"history_by_sportsbook": {
					"DraftKings": [{"odds": -110, "sportsbook": "DraftKings", "timestamp": 1704461400}],
					"FanDuel": [{"odds": -110, "sportsbook": "FanDuel", "timestamp": 1704461400}]
				}
			}
		}


class MarketLines(BaseModel):
	"""All outcomes for a specific market"""
	market_type: str  # "MONEY", "SPREAD", "TOTAL"
	market_display: str  # "Moneyline", "Spread -3.5", "Total 220.5"
	outcomes: list[OutcomeLine]

	class Config:
		json_schema_extra = {
			"example": {
				"market_type": "MONEY",
				"market_display": "Moneyline",
				"outcomes": [
					{
						"outcome_id": "lakers_ml",
						"outcome_name": "Lakers ML",
						"history": [],
						"current_best_odds": -110,
						"current_best_sportsbook": "FanDuel"
					}
				]
			}
		}


class GameTerminalData(BaseModel):
	"""Complete terminal data for a single game"""
	event_id: str
	sport: str
	league: str
	home_team: str
	away_team: str
	matchup: str
	start_time: str  # ISO format
	game_status: str  # "upcoming", "live", "completed"
	markets: list[MarketLines]

	class Config:
		json_schema_extra = {
			"example": {
				"event_id": "nba_lakers_warriors_20260105_1900",
				"sport": "basketball",
				"league": "NBA",
				"home_team": "Warriors",
				"away_team": "Lakers",
				"matchup": "Lakers @ Warriors",
				"start_time": "2026-01-05T19:00:00Z",
				"game_status": "upcoming",
				"markets": []
			}
		}


class TerminalResponse(BaseModel):
	"""Response for terminal stream endpoint"""
	tier: str
	data: list[GameTerminalData]
	metadata: dict | None = None
	cached_at: str | None = None
	message: str | None = None


# ==================== EV BET MODELS ====================

class TrueOddsSource(BaseModel):
	"""Source of true odds from prediction markets"""
	platform: str  # "Kalshi" or "Polymarket"
	probability: float
	american_odds: int
	liquidity: float | None = None
	volume: float | None = None
	confidence_score: float

	class Config:
		json_schema_extra = {
			"example": {
				"platform": "Kalshi",
				"probability": 0.65,
				"american_odds": -186,
				"liquidity": 50000,
				"volume": 2500,
				"confidence_score": 0.85
			}
		}


class EVBetSide(BaseModel):
	"""Individual bet side in an EV opportunity"""
	team: str
	odds: int
	sportsbook: str
	implied_probability: float

	class Config:
		json_schema_extra = {
			"example": {
				"team": "Lakers",
				"odds": 150,
				"sportsbook": "DraftKings",
				"implied_probability": 0.4
			}
		}


class EVBet(BaseModel):
	"""Complete EV betting opportunity"""
	id: str
	league: str
	matchup: str
	market: str
	game_time: str
	bet: EVBetSide
	true_odds: TrueOddsSource
	expected_value: float  # EV percentage
	edge: float  # Edge percentage
	kelly_fraction: float  # Recommended bet size (0-0.25)
	confidence: str  # HIGH/MEDIUM/LOW
	found_at: str  # ISO format datetime string

	class Config:
		json_schema_extra = {
			"example": {
				"id": "ev_nba_lakers_warriors_MONEY_draftkings_1",
				"league": "NBA",
				"matchup": "Lakers @ Warriors",
				"market": "Moneyline",
				"game_time": "2026-01-05T19:00:00Z",
				"bet": {
					"team": "Lakers",
					"odds": 150,
					"sportsbook": "DraftKings",
					"implied_probability": 0.4
				},
				"true_odds": {
					"platform": "Kalshi",
					"probability": 0.55,
					"american_odds": -122,
					"liquidity": 50000,
					"volume": 2500,
					"confidence_score": 0.85
				},
				"expected_value": 7.5,
				"edge": 15.0,
				"kelly_fraction": 0.05,
				"confidence": "HIGH",
				"found_at": "2026-01-05T15:30:00Z"
			}
		}


class EVResponse(BaseModel):
	"""Response for EV bets endpoint"""
	tier: str
	data: list[EVBet]
	metadata: dict | None = None
	cached_at: str | None = None
	message: str | None = None