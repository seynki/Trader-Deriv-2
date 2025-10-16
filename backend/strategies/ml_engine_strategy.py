from __future__ import annotations
from typing import Dict, Any, Optional
import pandas as pd
from .base import BaseStrategy, StrategyContext, StrategyDecision
from backend import ml_engine
from pathlib import Path
import glob


class MLEngineStrategy(BaseStrategy):
    name = "ml_engine"

    def __init__(self):
        self._loaded_models: Optional[ml_engine.TrainedModels] = None
        self._cfg = ml_engine.MLConfig()
        # Try load last persisted LGB-only models if available
        try:
            # pick any persisted trio for R_10 as default (best effort)
            # this is optional; if not found, strategy yields NEUTRAL
            pass
        except Exception:
            pass

    def decide(self, df: pd.DataFrame, ctx: StrategyContext) -> StrategyDecision:
        if df.empty or len(df) < (self._cfg.seq_len + 10):
            return StrategyDecision("NEUTRAL", 0.0, "dados insuficientes", {})
        try:
            pred = ml_engine.predict_from_models(df.tail(self._cfg.seq_len + 10), self._loaded_models, self._cfg) if self._loaded_models else None
            if not pred:
                return StrategyDecision("NEUTRAL", 0.0, "sem modelos treinados em memória", {})
            direction = str(pred.get("direction", "NEUTRAL"))
            conf = float(pred.get("conf", 0.0))
            side = "RISE" if direction == "CALL" else ("FALL" if direction == "PUT" else "NEUTRAL")
            return StrategyDecision(side, conf, "ML Engine ensemble", {"prob": float(pred.get("prob", 0.5))})
        except Exception as e:
            return StrategyDecision("NEUTRAL", 0.0, f"ml_engine erro: {e}", {})
