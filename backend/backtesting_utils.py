from __future__ import annotations
import json
import os
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# NOTE: This module is purposely backend-agnostic and does not import FastAPI app directly.
# Server will pass in callables to fetch candles from Deriv if CSVs are missing.

BACKTESTS_DIR = Path(__file__).parent / "backtests"
BACKTESTS_DIR.mkdir(exist_ok=True)
BACKTESTS_RESULTS = BACKTESTS_DIR / "results.json"
if not BACKTESTS_RESULTS.exists():
    BACKTESTS_RESULTS.write_text(json.dumps({"runs": []}, indent=2))


def map_timeframe_to_granularity(timeframe: str) -> int:
    tf = (timeframe or "1m").lower().strip()
    if tf.endswith("s"):
        try:
            return int(tf[:-1])
        except Exception:
            return 60
    if tf.endswith("m"):
        try:
            return int(tf[:-1]) * 60
        except Exception:
            return 60
    if tf.endswith("h"):
        try:
            return int(tf[:-1]) * 3600
        except Exception:
            return 3600
    # default minutes if pure number
    try:
        v = int(tf)
        return v * 60
    except Exception:
        return 60


def _candidate_csv_paths(symbol: str, timeframe: str) -> List[Path]:
    base = Path(__file__).parent
    patterns = [
        base / "data" / f"{symbol}_{timeframe}.csv",
        base / "data" / f"{symbol}-{timeframe}.csv",
        base / "data" / f"{symbol}.csv",
        Path("/app/data") / f"{symbol}_{timeframe}.csv",
        Path("/app/data") / f"{symbol}.csv",
    ]
    return [p for p in patterns if p is not None]


def load_csv_ohlcv(symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
    for p in _candidate_csv_paths(symbol, timeframe):
        if p.exists():
            try:
                df = pd.read_csv(p)
                # Normalize columns
                cols = {c.lower(): c for c in df.columns}
                rename_map = {}
                for key in ["datetime", "timestamp", "epoch", "time"]:
                    if key in cols:
                        rename_map[cols[key]] = "timestamp"
                        break
                for key in ["open", "high", "low", "close", "volume"]:
                    if key in cols:
                        rename_map[cols[key]] = key
                if rename_map:
                    df = df.rename(columns=rename_map)
                # Build epoch seconds if possible
                if "timestamp" in df.columns:
                    try:
                        if np.issubdtype(df["timestamp"].dtype, np.number):
                            ts = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
                        else:
                            ts = pd.to_datetime(df["timestamp"], errors="coerce")
                    except Exception:
                        ts = pd.to_datetime(df["timestamp"], errors="coerce")
                    df.index = ts
                else:
                    # fallback create index
                    df.index = pd.date_range(start=datetime.utcnow(), periods=len(df), freq="1min")
                # Ensure needed columns
                needed = ["open", "high", "low", "close"]
                if not all(c in df.columns for c in needed):
                    return None
                return df
            except Exception:
                continue
    return None


def slice_df_date(df: pd.DataFrame, date_from: Optional[str], date_to: Optional[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if not date_from and not date_to:
        return df
    s = pd.to_datetime(date_from) if date_from else None
    e = pd.to_datetime(date_to) if date_to else None
    if s is not None:
        df = df[df.index >= s]
    if e is not None:
        df = df[df.index <= e]
    return df


def decision_engine_backtest(df: pd.DataFrame, make_engine, engine_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Run simple by-trade backtest: at each t, get decision RISE/FALL from engine on df[:t],
    realize P&L at t+1 using binary payout assumption (win +0.95, loss -1.0).
    """
    if df is None or len(df) < 50:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "pnl_total": 0.0,
            "ev_per_trade": 0.0,
            "max_drawdown": 0.0,
            "sharpe": None,
        }
    # Ensure types
    w, l = 0, 0
    pnls: List[float] = []
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    # Rolling loop
    for i in range(50, len(df) - 1):
        window = df.iloc[: i + 1]
        try:
            engine = make_engine(engine_config)
            # Build ctx inside server where detect_market_regime exists; but here we only rely on engine.evaluate signature
            res = engine.evaluate(window, ctx=None)  # engine should ignore ctx=None or wrapper will provide
            side = res.get("decision")
        except Exception:
            side = None
        if side not in ("RISE", "FALL"):
            continue
        c0 = float(df.iloc[i]["close"])
        c1 = float(df.iloc[i + 1]["close"])
        pnl = 0.95 if ((side == "RISE" and c1 > c0) or (side == "FALL" and c1 < c0)) else -1.0
        pnls.append(pnl)
        if pnl > 0:
            w += 1
        else:
            l += 1
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    n = len(pnls)
    win_rate = (w / n) if n > 0 else 0.0
    ev = (sum(pnls) / n) if n > 0 else 0.0
    sharpe = None
    if n > 1:
        m = sum(pnls) / n
        sd = (sum((x - m) ** 2 for x in pnls) / (n - 1)) ** 0.5
        if sd > 0:
            sharpe = m / sd
    return {
        "trades": n,
        "wins": w,
        "losses": l,
        "win_rate": win_rate,
        "pnl_total": sum(pnls),
        "ev_per_trade": ev,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
    }


def append_run_to_results(run: Dict[str, Any]) -> None:
    try:
        data = json.loads(BACKTESTS_RESULTS.read_text())
    except Exception:
        data = {"runs": []}
    data.setdefault("runs", []).append(run)
    BACKTESTS_RESULTS.write_text(json.dumps(data, indent=2))


def load_run_from_results(run_id: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(BACKTESTS_RESULTS.read_text())
        for r in data.get("runs", []):
            if str(r.get("id")) == str(run_id):
                return r
    except Exception:
        return None
    return None
