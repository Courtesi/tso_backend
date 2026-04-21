from app.filter_utils import (
    apply_arb_filters,
    apply_terminal_tier_filters,
    filter_terminal_data,
    filter_sportsbook_events_by_tier,
)


def _arb(
    profit=1.0,
    league="NBA",
    market="moneyline",
    bet1_sb="draftkings",
    bet2_sb="fanduel",
):
    return {
        "profit_percentage": profit,
        "league": league,
        "market": market,
        "bet1": {"sportsbook": bet1_sb},
        "bet2": {"sportsbook": bet2_sb},
    }


def _game(league="NBA", status="upcoming"):
    return {"league": league, "game_status": status, "markets": []}


# --- Tier caps ---


def test_free_tier_capped_at_5():
    arbs = [_arb() for _ in range(10)]
    assert len(apply_arb_filters(arbs, {}, "free")) == 5


def test_premium_tier_no_cap():
    arbs = [_arb() for _ in range(10)]
    assert len(apply_arb_filters(arbs, {}, "premium")) == 10


# --- Profit filters ---


def test_min_profit_filter():
    arbs = [_arb(0.5), _arb(1.0), _arb(2.0)]
    result = apply_arb_filters(arbs, {"min_profit": 1.0}, "premium")
    assert len(result) == 2
    assert all(a["profit_percentage"] >= 1.0 for a in result)


def test_max_profit_filter():
    arbs = [_arb(0.5), _arb(1.0), _arb(2.0)]
    result = apply_arb_filters(arbs, {"max_profit": 1.0}, "premium")
    assert len(result) == 2
    assert all(a["profit_percentage"] <= 1.0 for a in result)


def test_zero_min_profit_is_ignored():
    arbs = [_arb(0.1), _arb(0.5)]
    result = apply_arb_filters(arbs, {"min_profit": 0}, "premium")
    assert len(result) == 2


# --- League filters ---


def test_league_filter_string():
    arbs = [_arb(league="NBA"), _arb(league="NFL"), _arb(league="NBA")]
    result = apply_arb_filters(arbs, {"league": "NBA"}, "premium")
    assert len(result) == 2
    assert all(a["league"] == "NBA" for a in result)


def test_league_filter_case_insensitive():
    arbs = [_arb(league="NBA"), _arb(league="NFL")]
    result = apply_arb_filters(arbs, {"league": "nba"}, "premium")
    assert len(result) == 1


def test_league_filter_all_passthrough():
    arbs = [_arb(league="NBA"), _arb(league="NFL")]
    result = apply_arb_filters(arbs, {"league": "all"}, "premium")
    assert len(result) == 2


# --- Sportsbook filter ---


def test_sportsbook_filter():
    arbs = [
        _arb(bet1_sb="draftkings"),
        _arb(bet1_sb="fanduel"),
        _arb(bet2_sb="draftkings"),
    ]
    result = apply_arb_filters(arbs, {"sportsbooks": ["draftkings"]}, "premium")
    assert len(result) == 2


# --- Terminal tier filters ---


def test_terminal_free_excludes_ncaab():
    games = [_game("NBA"), _game("NCAAB"), _game("NFL")]
    result = apply_terminal_tier_filters(games, "free")
    leagues = {g["league"] for g in result}
    assert "NCAAB" not in leagues
    assert "NBA" in leagues


def test_terminal_premium_allows_all():
    games = [_game("NBA"), _game("NCAAB"), _game("NCAAF")]
    assert len(apply_terminal_tier_filters(games, "premium")) == 3


# --- Game time filter ---


def test_filter_by_upcoming():
    games = [_game(status="upcoming"), _game(status="live"), _game(status="completed")]
    result = filter_terminal_data(games, "upcoming")
    assert len(result) == 1
    assert result[0]["game_status"] == "upcoming"


def test_filter_by_live():
    games = [_game(status="upcoming"), _game(status="live")]
    result = filter_terminal_data(games, "live")
    assert len(result) == 1


def test_filter_all_returns_everything():
    games = [_game(status="upcoming"), _game(status="live"), _game(status="completed")]
    assert len(filter_terminal_data(games, "all")) == 3


def test_filter_none_returns_everything():
    games = [_game(status="upcoming"), _game(status="live")]
    assert len(filter_terminal_data(games, None)) == 2


# --- Sportsbook events tier filter ---


def test_sportsbook_events_free_tier():
    events = [{"league": "NBA"}, {"league": "NCAAB"}, {"league": "NFL"}]
    result = filter_sportsbook_events_by_tier(events, "free")
    assert not any(e["league"] == "NCAAB" for e in result)
    assert any(e["league"] == "NBA" for e in result)


def test_sportsbook_events_premium_tier():
    events = [{"league": "NBA"}, {"league": "NCAAB"}]
    assert len(filter_sportsbook_events_by_tier(events, "premium")) == 2
