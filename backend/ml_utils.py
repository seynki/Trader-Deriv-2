from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score
from sklearn.calibration import CalibratedClassifierCV
from datetime import datetime

ROOT = Path(__file__).parent
ML_DIR = ROOT / "ml_models"
ML_DIR.mkdir(exist_ok=True)
CHAMP_PATH = ML_DIR / "champion.json"


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / (roll_down + 1e-12)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    line = ema_fast - ema_slow
    sig = ema(line, signal)
    hist = line - sig
    return line, sig, hist


def bollinger(series: pd.Series, length: int = 20, k: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid = series.rolling(length).mean()
    sd = series.rolling(length).std()
    upper = mid + k * sd
    lower = mid - k * sd
    return mid, upper, lower


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi_14"] = rsi(df["close"], 14)
    macd_line, macd_sig, macd_hist = macd(df["close"], 12, 26, 9)
    df["macd_line"], df["macd_signal"], df["macd_hist"] = macd_line, macd_sig, macd_hist
    bb_mid, bb_up, bb_lo = bollinger(df["close"], 20, 2.0)
    df["bb_basis"], df["bb_upper"], df["bb_lower"] = bb_mid, bb_up, bb_lo
    for col in [
        "rsi_14","macd_line","macd_signal","macd_hist","bb_basis","bb_upper","bb_lower","close"
    ]:
        df[f"{col}_slope3"] = df[col].diff(3)
    df["close_z20"] = (df["close"] - df["close"].rolling(20).mean()) / (df["close"].rolling(20).std() + 1e-9)
    return df


def make_target(df: pd.DataFrame, horizon: int, threshold: float) -> pd.Series:
    fut = df["close"].shift(-horizon)
    ret = (fut - df["close"]) / df["close"]
    return (ret > threshold).astype(int)


def select_features(df: pd.DataFrame):
    cands = [c for c in df.columns if any(k in c for k in ["rsi","macd","bb_","close","volume","slope","z"])]
    blacklist = {"open","high","low"}
    feats = [c for c in cands if c not in blacklist and pd.api.types.is_numeric_dtype(df[c])]
    return feats


def compute_metrics(y_true, y_pred, y_proba=None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
    }
    if y_proba is not None:
        try:
            out["roc_auc"] = float(roc_auc_score(y_true, y_proba))
        except Exception:
            out["roc_auc"] = None
    else:
        out["roc_auc"] = None
    return out


def backtest_simple(close: pd.Series, preds: pd.Series, horizon: int) -> Dict[str, Any]:
    fut = close.shift(-horizon)
    ret = (fut - close) / close
    strat_ret = ret.where(preds == 1, 0.0)
    eq = strat_ret.fillna(0.0).cumsum()
    peak = eq.cummax()
    dd = (eq - peak)
    max_dd = float(dd.min())
    return {"equity_final": float(eq.iloc[-1] if len(eq) else 0.0), "max_drawdown": max_dd}


def load_champion() -> Dict[str, Any]:
    if CHAMP_PATH.exists():
        try:
            return json.loads(CHAMP_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_champion(meta: Dict[str, Any]):
    # ensure timestamp
    if "updated_at" not in meta:
        meta["updated_at"] = datetime.utcnow().isoformat() + "Z"
    CHAMP_PATH.write_text(json.dumps(meta, indent=2))


def train_and_maybe_promote(df: pd.DataFrame, horizon: int, threshold: float, model_type: str = "rf", save_prefix: str = "weekly") -> Dict[str, Any]:
    # Build dataset
    feats_df = build_features(df)
    y = make_target(df, horizon=horizon, threshold=threshold)
    X_cols = select_features(feats_df)
    X = feats_df[X_cols].replace([np.inf, -np.inf], np.nan)
    mask = X.notna().all(axis=1) & y.notna()
    X, y = X[mask], y[mask]
    if len(X) < 400:
        raise ValueError("Dados insuficientes após limpeza (>= 400 linhas)")
    # temporal holdout
    n = len(X)
    cut = int(n * 0.8)
    Xtr, Xte = X.iloc[:cut], X.iloc[cut:]
    ytr, yte = y.iloc[:cut], y.iloc[cut:]
    # model
    if model_type == "dt":
        model = DecisionTreeClassifier(max_depth=4, random_state=42, min_samples_leaf=20)
    else:
        model = RandomForestClassifier(n_estimators=300, max_depth=None, min_samples_leaf=20, random_state=42, n_jobs=-1)
    model.fit(Xtr, ytr)
    p = model.predict(Xte)
    proba = model.predict_proba(Xte)[:,1] if hasattr(model, "predict_proba") else None
    metrics = compute_metrics(yte, p, proba)
    # backtest simples na parte de teste
    bt = backtest_simple(df.loc[Xte.index, "close"], pd.Series(p, index=Xte.index), horizon)
    # promoção automática
    champ = load_champion()
    cond_f1 = metrics["f1"] >= 1.10 * float(champ.get("metrics", {}).get("f1", 0.0))
    cond_prec = metrics["precision"] >= float(champ.get("metrics", {}).get("precision", 0.0))
    cond_dd = bt["max_drawdown"] >= float(champ.get("backtest", {}).get("max_drawdown", -1e9))  # menos negativo é melhor
    promoted = bool(cond_f1 and cond_prec and cond_dd)
    model_id = f"{save_prefix}_{model_type}"
    model_path = ML_DIR / f"{model_id}.joblib"
    dump({"model": model, "features": X_cols}, model_path)
    if promoted:
        meta = {
            "model_id": model_id,
            "path": str(model_path),
            "metrics": metrics,
            "backtest": bt,
            "horizon": horizon,
            "threshold": threshold,
            "ts_rows": int(len(Xtr)),
            "vs_rows": int(len(Xte)),
        }
        # stamp time on champion metadata
        meta["updated_at"] = datetime.utcnow().isoformat() + "Z"
        save_champion(meta)
    return {"model_id": model_id, "metrics": metrics, "backtest": bt, "promoted": promoted}