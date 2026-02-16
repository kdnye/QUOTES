"""Tests for quote threshold guardrails."""

from app.quote.thresholds import AIR_PIECE_LIMIT_WARNING, check_air_piece_limit


def test_air_piece_limit_triggers_for_actual_weight_over_300_per_piece() -> None:
    """Air piece limit should trigger when actual lbs/piece is above 300."""

    warning = check_air_piece_limit("Air", actual_weight=901, pieces=3)

    assert warning == AIR_PIECE_LIMIT_WARNING


def test_air_piece_limit_triggers_for_dim_weight_over_300_per_piece() -> None:
    """Air piece limit should trigger when dimensional lbs/piece is above 300."""

    warning = check_air_piece_limit(
        "Air",
        actual_weight=400,
        pieces=2,
        dim_weight=650,
    )

    assert warning == AIR_PIECE_LIMIT_WARNING


def test_air_piece_limit_ignores_non_air_shipments() -> None:
    """Only air shipments should enforce the piece-weight threshold."""

    warning = check_air_piece_limit(
        "Hotshot",
        actual_weight=1200,
        pieces=2,
        dim_weight=1400,
    )

    assert warning is None
