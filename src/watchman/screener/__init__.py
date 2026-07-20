"""Module A — Foundations: fundamentals-driven long-term screener.

Four-pillar composite score (Quality, Growth, Valuation, Momentum), ranked
watchlist with plain-English theses citing actual numbers, deteriorator flags.
All data access goes through AsOfView; missing data is shown, not guessed.
"""

from watchman.screener.runner import ScreenResult, run_screen
from watchman.screener.scoring import ScoreOutput, score_universe
from watchman.screener.store import Deterioration, find_deteriorators

__all__ = [
    "Deterioration",
    "ScoreOutput",
    "ScreenResult",
    "find_deteriorators",
    "run_screen",
    "score_universe",
]
