from pydantic import BaseModel
from datetime import datetime

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