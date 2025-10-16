from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any
import pandas as pd


@dataclass
class StrategyDecision:
    signal: str  # "RISE" | "FALL" | "NEUTRAL"
    confidence: float
    reason: str
    meta: Dict[str, Any]


@dataclass
class StrategyContext:
    symbol: str = "R_10"
    timeframe: str = "1m"
    # market regime info, e.g., detect_market_regime output
    regime: Optional[Dict[str, Any]] = None
    # any config overrides
    config: Optional[Dict[str, Any]] = None


class BaseStrategy:
    name: str = "base"

    def decide(self, df: pd.DataFrame, ctx: StrategyContext) -> StrategyDecision:
        raise NotImplementedError
