from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
	APP_NAME: str = "Trueshot FastAPI"
	ENV: str
	FRONTEND_URL: str

	GOOGLE_APPLICATION_CREDENTIALS: str
	STRIPE_SECRET_KEY: str
	RESEND_API_KEY: str
	RESEND_EMAIL: str

	REDIS_HOST: str
	REDIS_PORT: int
	REDIS_DB: int
	REDIS_PASSWORD: str = ""
	REDIS_KEY_PREFIX: str
	CACHE_TTL_MEDIUM: int
	FREE_KEY_PREFIX: str
	PREMIUM_KEY_PREFIX: str

	# Uvicorn logging settings (optional)
	UVICORN_LOG_LEVEL: str = "info"
	UVICORN_ACCESS_LOG: bool = True

	TIER_RATE_LIMITS: dict = {
		"free": 60,
		"premium": 5
	}

	TIER_MAX_ARBS: dict = {
		"free": 5,
		"premium": None
	}

	TIER_MAX_GAMES: dict = {
		"free": 10,
		"premium": None
	}

	TIER_ALLOWED_LEAGUES: dict = {
		"free": ["NBA", "NFL", "MLB"],
		"premium": None  # None means all leagues allowed
	}

	# WebSocket settings
	WEBSOCKET_PING_INTERVAL: int = 30  # seconds
	WEBSOCKET_PONG_TIMEOUT: int = 10   # seconds
	MAX_CONNECTIONS_PER_USER: int = 5

	class Config:
		env_file = ".env"
		# extra = "ignore"  # Ignore extra fields in .env that aren't defined here
	
@lru_cache
def get_settings() -> Settings:
	"""Retrieves the fastapi settings"""
	return Settings()

# Sportsbook configuration - maps normalized names to icon filenames and display names
SPORTSBOOKS = {
	"ballybet": {"icon": "ballybet.avif", "display_name": "Bally Bet"},
	"bally": {"icon": "ballybet.avif", "display_name": "Bally Bet"},
	"bet105": {"icon": "bet105.png", "display_name": "Bet105"},
	"bet365": {"icon": "bet365.png", "display_name": "Bet365"},
	"betmgm": {"icon": "betmgm.avif", "display_name": "BetMGM"},
	"betparx": {"icon": "betparx.png", "display_name": "BetParx"},
	"betr": {"icon": "betr.png", "display_name": "Betr"},
	"betrivers": {"icon": "betrivers.avif", "display_name": "BetRivers"},
	"betus": {"icon": "betus.png", "display_name": "BetUS"},
	"betwhale": {"icon": "betwhale.png", "display_name": "BetWhale"},
	"bodog": {"icon": "bodog.png", "display_name": "Bodog"},
	"borgata": {"icon": "borgata.avif", "display_name": "Borgata"},
	"bovada": {"icon": "bovada.png", "display_name": "Bovada"},
	"caesars": {"icon": "caesars.avif", "display_name": "Caesars"},
	"circa": {"icon": "circa.png", "display_name": "Circa"},
	"crabsports": {"icon": "crabsports.avif", "display_name": "Crab Sports"},
	"desertdiamond": {"icon": "desertdiamond.avif", "display_name": "Desert Diamond"},
	"draftkings": {"icon": "draftkings.avif", "display_name": "DraftKings"},
	"espnbet": {"icon": "espnbet.png", "display_name": "ESPN Bet"},
	"fanatics": {"icon": "fanatics.avif", "display_name": "Fanatics"},
	"fanduel": {"icon": "fanduel.avif", "display_name": "FanDuel"},
	"fliff": {"icon": "fliff.png", "display_name": "Fliff"},
	"hardrockbet": {"icon": "hardrockbet.avif", "display_name": "Hard Rock Bet"},
	"hardrock": {"icon": "hardrockbet.avif", "display_name": "Hard Rock Bet"},
	"mybookie": {"icon": "mybookie.png", "display_name": "MyBookie"},
	"novig": {"icon": "novig.webp", "display_name": "Novig"},
	"pinnacle": {"icon": "pinnacle.png", "display_name": "Pinnacle"},
	"prophetx": {"icon": "prophetx.png", "display_name": "ProphetX"},
	"rebet": {"icon": "rebet.png", "display_name": "Rebet"},
	"sporttrade": {"icon": "sporttrade.avif", "display_name": "Sporttrade"},
	"sportzino": {"icon": "sportzino.jfif", "display_name": "Sportzino"},
	"thescore": {"icon": "thescore.png", "display_name": "theScore"},
	"unibet": {"icon": "unibet.png", "display_name": "Unibet"},
	"kalshi": {"icon": "kalshi.png", "display_name": "Kalshi"},
	"polymarket": {"icon": "polymarket.png", "display_name": "Polymarket"}
}

# Tier features configuration for subscription page
TIER_FEATURES = {
	"free": {
		"name": "Free",
		"description": "Use Trueshot's basic features",
		"price": "$0",
		"features": [
			"Odds charts (Available leagues)",
			"Access to 5 arbitrage bets at a time",
			"Finds bets every 60 seconds",
		]
	},
	"premium": {
		"name": "Premium",
		"description": "Full access to all features",
		"features_intro": "Everything in Free, and:",
		"features": [
			"Access to unlimited arbitrage bets",
			"Real time updates on bets and lines",
		]
	}
}
