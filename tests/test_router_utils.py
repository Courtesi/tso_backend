from datetime import datetime, timezone, timedelta

from app.router import _compute_game_status, _resolve_display_name


# --- _compute_game_status ---


def test_future_is_upcoming():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    assert _compute_game_status(future) == "upcoming"


def test_just_started_is_live():
    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    assert _compute_game_status(recent) == "live"


def test_within_4_hours_is_live():
    recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    assert _compute_game_status(recent) == "live"


def test_over_4_hours_is_completed():
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    assert _compute_game_status(old) == "completed"


def test_invalid_date_returns_upcoming():
    assert _compute_game_status("not-a-date") == "upcoming"


def test_empty_string_returns_upcoming():
    assert _compute_game_status("") == "upcoming"


# --- _resolve_display_name ---


def test_exact_home_team_match():
    assert _resolve_display_name("lakers", "Lakers", "Celtics") == "Lakers"


def test_exact_away_team_match():
    assert _resolve_display_name("celtics", "Lakers", "Celtics") == "Celtics"


def test_prefix_match_with_spread():
    result = _resolve_display_name("lakers +5.5", "Lakers", "Celtics")
    assert result == "Lakers +5.5"


def test_unknown_name_falls_back_to_title_case():
    result = _resolve_display_name("unknown team", "Lakers", "Celtics")
    assert result == "Unknown Team"
