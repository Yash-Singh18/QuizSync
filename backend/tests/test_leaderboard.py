"""
Unit tests for the composite ZSET score encoding.
Run with: uv run pytest
"""

from app.leaderboard import composite, decode


def test_points_dominate():
    # 1 more point beats any time disadvantage
    assert composite(100, 10 * 60 * 1000) > composite(99, 0)


def test_less_time_wins_ties():
    assert composite(500, 3_000) > composite(500, 4_000)


def test_zero_state():
    assert composite(0, 0) == 0.0


def test_decode_round_trip():
    for points, time_ms in [
        (0, 0),
        (0, 1),
        (1000, 12_345),
        (100_000, 0),
        (100_000, 86_400_000),  # a full day of accumulated time
    ]:
        assert decode(composite(points, time_ms)) == (points, time_ms)


def test_ordering_matches_results_query():
    # score DESC, time ASC — same as GET /results
    rows = [(500, 4_000), (500, 3_000), (700, 9_000), (0, 0)]
    by_composite = sorted(rows, key=lambda r: composite(*r), reverse=True)
    by_query = sorted(rows, key=lambda r: (-r[0], r[1]))
    assert by_composite == by_query
