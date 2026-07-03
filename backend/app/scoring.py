"""
Scoring formula (speed-weighted, Kahoot-style).
Pure function — no I/O, no side effects. Unit-tested in isolation.

  correct=False          → 0 points
  correct=True, instant  → base points
  correct=True, at limit → base / 2 points
  latency > duration     → treated as full duration (buzzer-wire case)
"""


def score(correct: bool, latency_ms: int, duration_ms: int, base: int) -> int:
    """
    Returns points awarded for a single answer.

    Args:
        correct:     Whether the selected option was correct.
        latency_ms:  Time taken to answer in milliseconds.
        duration_ms: Total question time window in milliseconds.
        base:        Maximum points for this question.
    """
    if not correct:
        return 0
    frac = min(latency_ms, duration_ms) / duration_ms
    return round(base * (1 - 0.5 * frac))
