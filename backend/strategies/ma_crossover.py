from __future__ import annotations
from typing import Dict, Any
import pandas as pd
from .base import BaseStrategy, StrategyContext, StrategyDecision


class MACrossoverStrategy(BaseStrategy):
    name = "ma_crossover"

    def decide(self, df: pd.DataFrame, ctx: StrategyContext) -> StrategyDecision:
        c = df.copy()
        if c.empty or len(c) < 25:
            return StrategyDecision("NEUTRAL", 0.0, "dados insuficientes", {})
        c["ema_fast"] = c["close"].ewm(span=9, adjust=False).mean()
        c["ema_slow"] = c["close"].ewm(span=21, adjust=False).mean()
        c["macd_line"] = c["close"].ewm(span=12, adjust=False).mean() - c["close"].ewm(span=26, adjust=False).mean()
        c["macd_sig"] = c["macd_line"].ewm(span=9, adjust=False).mean()
        last = c.iloc[-1]
        prev = c.iloc[-2]
        reason = ""
        signal = "NEUTRAL"
        conf = 0.0
        # bullish cross
        if prev["ema_fast"] < prev["ema_slow"] and last["ema_fast"] > last["ema_slow"] and last["macd_line"] > last["macd_sig"]:
            signal = "RISE"
            conf = 0.6
            reason = "EMA9>EMA21 + MACD↑"
        # bearish cross
        elif prev["ema_fast"] > prev["ema_slow"] and last["ema_fast"] < last["ema_slow"] and last["macd_line"] < last["macd_sig"]:
            signal = "FALL"
            conf = 0.6
            reason = "EMA9<EMA21 + MACD↓"
        # regime-aware confidence tweak
        reg = (ctx.regime or {})
        if reg.get("trend_strength") == "strong_trend":
            conf += 0.1
        elif reg.get("trend_strength") == "range":
            conf -= 0.1
        conf = max(0.0, min(1.0, conf))
        return StrategyDecision(signal, conf, reason or "sem cruzamento claro", {
            "ema_fast": float(last["ema_fast"]),
            "ema_slow": float(last["ema_slow"]),
            "macd_line": float(last["macd_line"]),
            "macd_sig": float(last["macd_sig"])})
