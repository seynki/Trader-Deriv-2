from __future__ import annotations
from typing import Any, Dict, Optional, Callable, Awaitable, List
import pandas as pd
import asyncio
from datetime import datetime

# Reusar modelo River existente
import river_online_model

GetCandlesFn = Callable[[str, int, int], Awaitable[List[Dict[str, Any]]]]

class RiverOnlineService:
    """Serviço utilitário para expor um snapshot atual do modelo River Online.
    Usa o modelo já persistido em disco (se existir) e calcula um snapshot
    com base no último candle disponível via função de obtenção de candles.
    """

    def __init__(self) -> None:
        self._model: Optional[river_online_model.RiverOnlineCandleModel] = None

    def _get_model(self) -> river_online_model.RiverOnlineCandleModel:
        if self._model is not None:
            return self._model
        try:
            self._model = river_online_model.RiverOnlineCandleModel.load()
        except Exception:
            self._model = river_online_model.RiverOnlineCandleModel()
        return self._model

    async def get_snapshot(self, *, symbol: str, granularity: int, get_candles: GetCandlesFn, lookback: int = 50) -> Dict[str, Any]:
        """Retorna um snapshot com:
        - model_metrics (samples, acc, logloss)
        - last_features (features calculadas no último candle)
        - prob_up e signal (LONG/SHORT)
        - symbol/timeframe usados
        """
        model = self._get_model()
        try:
            candles = await get_candles(symbol, granularity, lookback)
        except Exception:
            candles = []
        if not candles:
            return {
                "symbol": symbol,
                "timeframe": f"{granularity}s",
                "model_metrics": {
                    "samples": getattr(model, "sample_count", 0),
                    "acc": float(model.metric_acc.get()) if getattr(model, "sample_count", 0) > 0 else None,
                    "logloss": float(model.metric_logloss.get()) if getattr(model, "sample_count", 0) > 0 else None,
                },
                "last_features": None,
                "prob_up": None,
                "signal": None,
                "timestamp": int(datetime.utcnow().timestamp()),
            }
        last = candles[-1]
        ts = last.get("epoch") or last.get("timestamp") or datetime.utcnow().timestamp()
        info = model.predict_and_update(
            timestamp=ts,
            o=float(last.get("open", 0.0)),
            h=float(last.get("high", 0.0)),
            l=float(last.get("low", 0.0)),
            c=float(last.get("close", 0.0)),
            v=float(last.get("volume", 0.0)),
            next_close=None,
        )
        return {
            "symbol": symbol,
            "timeframe": f"{granularity}s",
            "model_metrics": {
                "samples": getattr(model, "sample_count", 0),
                "acc": float(model.metric_acc.get()) if getattr(model, "sample_count", 0) > 0 else None,
                "logloss": float(model.metric_logloss.get()) if getattr(model, "sample_count", 0) > 0 else None,
            },
            "last_features": info.get("features"),
            "prob_up": float(info.get("prob_up", 0.5)),
            "signal": info.get("signal"),
            "timestamp": int(datetime.utcnow().timestamp()),
        }
