from __future__ import annotations
from typing import Dict, Any, List
import pandas as pd
from .base import BaseStrategy, StrategyContext, StrategyDecision
from .ma_crossover import MACrossoverStrategy
from .rsi_reinforced_strategy import RSIReinforcedStrategy


class HybridStrategy(BaseStrategy):
    name = "hybrid"

    def __init__(self):
        self.ma = MACrossoverStrategy()
        self.rsi = RSIReinforcedStrategy()

    def decide(self, df: pd.DataFrame, ctx: StrategyContext) -> StrategyDecision:
        reg = ctx.regime or {}
        # choose sub-strategy based on regime: trend -> MA; range -> RSI
        chooser = self.ma if reg.get("trend_strength") in {"trend", "strong_trend"} else self.rsi
        d = chooser.decide(df, ctx)
        return StrategyDecision(d.signal, min(1.0, d.confidence + 0.05), f"Hybrid→{chooser.name}: {d.reason}", d.meta)
