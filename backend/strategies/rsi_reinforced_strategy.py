from __future__ import annotations
from typing import Dict, Any, List
import pandas as pd
from .base import BaseStrategy, StrategyContext, StrategyDecision
from backend.rsi_reinforced import RsiReinforcedParams, generate_signals


class RSIReinforcedStrategy(BaseStrategy):
    name = "rsi_reinforced"

    def decide(self, df: pd.DataFrame, ctx: StrategyContext) -> StrategyDecision:
        if df.empty or len(df) < 60:
            return StrategyDecision("NEUTRAL", 0.0, "dados insuficientes", {})
        params = RsiReinforcedParams()
        cdf, signals = generate_signals(df, params)
        # take latest signal if any
        if not signals:
            return StrategyDecision("NEUTRAL", 0.0, "sem sinal RSI reforçado", {})
        last_sig = signals[-1]
        idx = int(last_sig["index"]) if isinstance(last_sig.get("index"), (int, float)) else None
        conf = 0.55
        reason = "Reentrada nas bandas RSI"
        # confidence from RSI distance from midline and band width
        try:
            if idx is not None:
                rsi = float(cdf.iloc[idx]["rsi"])
                mid = float(cdf.iloc[idx]["rsi_bb_mid"])
                width = float(cdf.iloc[idx]["rsi_bb_width"])
                dist = abs(rsi - mid)
                # normalize
                conf = max(0.5, min(0.9, 0.5 + (dist/20.0) + (min(width, 30.0)/100.0)))
                reason = f"RSI dist={dist:.1f} width={width:.1f}"
        except Exception:
            pass
        # regime-aware
        reg = ctx.regime or {}
        if reg.get("trend_strength") == "range":
            conf += 0.1
        elif reg.get("trend_strength") == "strong_trend":
            conf -= 0.05
        conf = max(0.0, min(1.0, conf))
        side = "RISE" if last_sig["side"] == "CALL" else "FALL"
        return StrategyDecision(side, conf, reason, {"index": last_sig.get("index")})
