from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np

from ml_utils import rsi as rsi_fn, bollinger as bb_fn


@dataclass
class RsiReinforcedParams:
    # Core RSI settings
    rsi_period: int = 14
    rsi_bb_length: int = 20
    rsi_bb_k: float = 2.0
    # Multi-timeframe confirmation
    higher_tf_factor: int = 5  # HTF = factor * base granularity
    confirm_with_midline: bool = True  # use 50 midline check
    confirm_with_slope: bool = True
    slope_lookback: int = 3
    # Signal logic
    min_bandwidth: float = 10.0  # min RSI band width to consider as an "extreme" context
    reentry_only: bool = True  # require cross-back inside band (reentry) instead of simple touch
    distance_from_mid_min: float = 8.0  # RSI must be this far from mid (BB basis) at signal
    # Backtest
    horizon: int = 3  # candles to evaluate outcome
    payout_ratio: float = 0.95  # binary option like payout for reference equity


def _aggregate_htf(df: pd.DataFrame, factor: int) -> pd.DataFrame:
    """Aggregate base timeframe candles into a higher timeframe by grouping every `factor` rows.
    Keeps last index timestamp for alignment convenience.
    """
    if factor <= 1 or df.empty:
        return df.copy()
    # Create group id by integer division
    grp = (np.arange(len(df)) // factor)
    agg = df.groupby(grp).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        **({"volume": "sum"} if "volume" in df.columns else {}),
    })
    # Use last timestamp of each group for indexing
    ts = df.index.to_series().groupby(grp).last()
    agg.index = pd.to_datetime(ts.values)
    return agg


def compute_rsi_bbands(rsi_series: pd.Series, length: int, k: float) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid, upper, lower = bb_fn(rsi_series, length, k)
    return mid, upper, lower


def generate_signals(df: pd.DataFrame, params: RsiReinforcedParams) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """Compute RSI Bollinger signals with multi-timeframe confirmation.

    Returns: (df_with_cols, signals)
      - df includes columns: rsi, rsi_bb_mid, rsi_bb_upper, rsi_bb_lower, rsi_bb_width, htf_rsi, htf_rsi_slope
      - signals: list of dict(timestamp, side, index)
    """
    cdf = df.copy()
    cdf = cdf[[c for c in ["open","high","low","close","volume"] if c in cdf.columns]].copy()

    # RSI on close
    cdf["rsi"] = rsi_fn(cdf["close"], params.rsi_period)
    mid, up, lo = compute_rsi_bbands(cdf["rsi"], params.rsi_bb_length, params.rsi_bb_k)
    cdf["rsi_bb_mid"], cdf["rsi_bb_upper"], cdf["rsi_bb_lower"] = mid, up, lo
    cdf["rsi_bb_width"] = (up - lo)

    # Higher timeframe RSI confirmation (resampled by factor)
    htf = _aggregate_htf(cdf[["open","high","low","close"]], params.higher_tf_factor)
    htf_rsi = rsi_fn(htf["close"], params.rsi_period)
    # Map back to base timeframe by forward filling
    htf_rsi_ff = htf_rsi.reindex(cdf.index, method="ffill")
    cdf["htf_rsi"] = htf_rsi_ff
    cdf["htf_rsi_slope"] = cdf["htf_rsi"].diff(params.slope_lookback)

    # Signal generation
    signals: List[Dict[str, Any]] = []
    rsi_val = cdf["rsi"].values
    mid_v = cdf["rsi_bb_mid"].values
    up_v = cdf["rsi_bb_upper"].values
    lo_v = cdf["rsi_bb_lower"].values
    w_v = cdf["rsi_bb_width"].values
    htf_v = cdf["htf_rsi"].values
    htf_slope_v = cdf["htf_rsi_slope"].values

    for i in range(2, len(cdf)):
        if np.isnan(rsi_val[i]) or np.isnan(up_v[i]) or np.isnan(lo_v[i]) or np.isnan(htf_v[i]):
            continue
        # Ignore when band width is too small (not at extremes)
        if w_v[i] < params.min_bandwidth:
            continue
        dist_from_mid = abs(rsi_val[i] - mid_v[i])
        if dist_from_mid < params.distance_from_mid_min:
            continue

        # Reentry logic
        long_reentry = (rsi_val[i-1] < lo_v[i-1] and rsi_val[i] >= lo_v[i]) if params.reentry_only else (rsi_val[i] <= lo_v[i])
        short_reentry = (rsi_val[i-1] > up_v[i-1] and rsi_val[i] <= up_v[i]) if params.reentry_only else (rsi_val[i] >= up_v[i])

        # HTF confirmation
        long_ok = True
        short_ok = True
        if params.confirm_with_midline:
            long_ok = long_ok and (htf_v[i] >= 50.0)
            short_ok = short_ok and (htf_v[i] <= 50.0)
        if params.confirm_with_slope and not np.isnan(htf_slope_v[i]):
            long_ok = long_ok and (htf_slope_v[i] >= 0)
            short_ok = short_ok and (htf_slope_v[i] <= 0)

        if long_reentry and long_ok:
            signals.append({"index": i, "timestamp": cdf.index[i], "side": "CALL"})
        elif short_reentry and short_ok:
            signals.append({"index": i, "timestamp": cdf.index[i], "side": "PUT"})

    return cdf, signals


def backtest_signals(df: pd.DataFrame, signals: List[Dict[str, Any]], params: RsiReinforcedParams) -> Dict[str, Any]:
    """Simple directional backtest using horizon candles ahead.
    Win rule: CALL wins if close[i+h] > close[i]; PUT wins if close[i+h] < close[i].
    Returns metrics including equity curve stats (binary option-like payout for reference).
    """
    if not signals:
        return {"total_signals": 0, "wins": 0, "losses": 0, "winrate": 0.0, "equity_final": 0.0, "max_drawdown": 0.0}

    close = df["close"].values
    h = max(1, int(params.horizon))
    wins = 0
    losses = 0
    equity = []
    eq = 0.0
    peak = 0.0
    max_dd = 0.0

    for s in signals:
        i = int(s["index"])  # entry at close i
        if i + h >= len(close):
            continue
        entry = close[i]
        out = close[i + h]
        if s["side"] == "CALL":
            win = out > entry
        else:
            win = out < entry
        if win:
            wins += 1
            eq += params.payout_ratio
        else:
            losses += 1
            eq -= 1.0
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
        equity.append(eq)

    total = wins + losses
    winrate = float(wins / total) if total > 0 else 0.0
    return {
        "total_signals": total,
        "wins": int(wins),
        "losses": int(losses),
        "winrate": winrate,
        "equity_final": float(eq if equity else 0.0),
        "max_drawdown": float(max_dd),
    }
