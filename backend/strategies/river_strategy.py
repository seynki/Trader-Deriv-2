from __future__ import annotations
from typing import Dict, Any
import pandas as pd
from .base import BaseStrategy, StrategyContext, StrategyDecision
# Load river model directly to avoid circular imports with server
import river_online_model


class RiverStrategy(BaseStrategy):
    name = "river"

    def __init__(self):
        try:
            self.model = river_online_model.RiverOnlineCandleModel.load()
        except Exception:
            self.model = river_online_model.RiverOnlineCandleModel()

    def decide(self, df: pd.DataFrame, ctx: StrategyContext) -> StrategyDecision:
        if df.empty:
            return StrategyDecision("NEUTRAL", 0.0, "dados insuficientes", {})
        last = df.iloc[-1]
        ts = str(last.name) if df.index.name is not None else None
        try:
            info = self.model.predict_and_update(
                ts or (df.index[-1].isoformat() if hasattr(df.index, 'isoformat') else None) or "",
                float(last.get("open", last["close"])),
                float(last.get("high", last["close"])),
                float(last.get("low", last["close"])),
                float(last.get("close")),
                float(last.get("volume", 0.0)),
                next_close=None,
            )
            prob_up = float(info.get("prob_up", 0.5))
            if prob_up >= 0.5:
                return StrategyDecision("RISE", prob_up, "River prob_up", {"prob_up": prob_up})
            else:
                return StrategyDecision("FALL", 1.0 - prob_up, "River prob_up", {"prob_up": prob_up})
        except Exception as e:
            return StrategyDecision("NEUTRAL", 0.0, f"river erro: {e}", {})
