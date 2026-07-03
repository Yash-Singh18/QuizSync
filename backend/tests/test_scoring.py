"""
Unit tests for the scoring formula.
These are load-bearing correctness checks for the core scoring logic.
Run with: uv run pytest
"""

import pytest

from app.scoring import score


# wrong answer always 0
def test_wrong_answer():
    assert score(correct=False, latency_ms=100, duration_ms=30_000, base=1000) == 0


# correct, instant (latency = 0) → full base points
def test_correct_instant():
    assert score(correct=True, latency_ms=0, duration_ms=30_000, base=1000) == 1000


# correct, at the buzzer (latency = duration) → base / 2
def test_correct_buzzer():
    result = score(correct=True, latency_ms=30_000, duration_ms=30_000, base=1000)
    assert result == 500


# correct, midway → between base/2 and base
def test_correct_midway():
    result = score(correct=True, latency_ms=15_000, duration_ms=30_000, base=1000)
    assert result == 750


# latency > duration clamps to duration (grace window edge case)
def test_latency_exceeds_duration():
    # Should be same as buzzer score, not negative or beyond range
    result = score(correct=True, latency_ms=35_000, duration_ms=30_000, base=1000)
    assert result == 500


# different base values
def test_different_base():
    assert score(correct=True, latency_ms=0, duration_ms=10_000, base=500) == 500
    assert score(correct=True, latency_ms=10_000, duration_ms=10_000, base=500) == 250


# wrong answer with zero latency is still 0
def test_wrong_instant():
    assert score(correct=False, latency_ms=0, duration_ms=10_000, base=2000) == 0
