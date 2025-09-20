from __future__ import annotations
import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
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

# Configure logger
logger = logging.getLogger(__name__)


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


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index - measures trend strength"""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    plus_dm = high.diff()
    minus_dm = low.diff() * -1
    
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0)
    
    tr_smooth = tr.rolling(period).mean()
    plus_dm_smooth = pd.Series(plus_dm).rolling(period).mean()
    minus_dm_smooth = pd.Series(minus_dm).rolling(period).mean()
    
    plus_di = 100 * (plus_dm_smooth / tr_smooth)
    minus_di = 100 * (minus_dm_smooth / tr_smooth)
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    return dx.rolling(period).mean()


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator"""
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
    d_percent = k_percent.rolling(d_period).mean()
    return k_percent, d_percent


def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Williams %R indicator"""
    highest_high = high.rolling(period).max()
    lowest_low = low.rolling(period).min() 
    wr = -100 * (highest_high - close) / (highest_high - lowest_low)
    return wr


def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    """Commodity Channel Index"""
    tp = (high + low + close) / 3
    tp_sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: abs(x - x.mean()).mean())
    return (tp - tp_sma) / (0.015 * mad)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range - volatility indicator"""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
    """Money Flow Index"""
    tp = (high + low + close) / 3
    raw_mf = tp * volume
    
    positive_mf = raw_mf.where(tp > tp.shift(), 0).rolling(period).sum()
    negative_mf = raw_mf.where(tp < tp.shift(), 0).rolling(period).sum()
    
    mf_ratio = positive_mf / negative_mf
    return 100 - (100 / (1 + mf_ratio))


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Volume Weighted Average Price"""
    tp = (high + low + close) / 3
    return (tp * volume).cumsum() / volume.cumsum()


def ichimoku(high: pd.Series, low: pd.Series, close: pd.Series, 
            tenkan_period: int = 9, kijun_period: int = 26, senkou_b_period: int = 52) -> Dict[str, pd.Series]:
    """Ichimoku Cloud components"""
    tenkan_sen = (high.rolling(tenkan_period).max() + low.rolling(tenkan_period).min()) / 2
    kijun_sen = (high.rolling(kijun_period).max() + low.rolling(kijun_period).min()) / 2
    senkou_span_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun_period)
    senkou_span_b = ((high.rolling(senkou_b_period).max() + low.rolling(senkou_b_period).min()) / 2).shift(kijun_period)
    chikou_span = close.shift(-kijun_period)
    
    return {
        'tenkan_sen': tenkan_sen,
        'kijun_sen': kijun_sen, 
        'senkou_span_a': senkou_span_a,
        'senkou_span_b': senkou_span_b,
        'chikou_span': chikou_span
    }


def fibonacci_levels(high: pd.Series, low: pd.Series, period: int = 20) -> Dict[str, pd.Series]:
    """Fibonacci retracement levels"""
    period_high = high.rolling(period).max()
    period_low = low.rolling(period).min()
    diff = period_high - period_low
    
    return {
        'fib_236': period_high - 0.236 * diff,
        'fib_382': period_high - 0.382 * diff,  
        'fib_500': period_high - 0.500 * diff,
        'fib_618': period_high - 0.618 * diff,
        'fib_786': period_high - 0.786 * diff
    }


def support_resistance(close: pd.Series, period: int = 20, min_touches: int = 2) -> Dict[str, pd.Series]:
    """Dynamic support and resistance levels"""
    rolling_max = close.rolling(period).max()
    rolling_min = close.rolling(period).min()
    
    # Distance to support/resistance
    support_distance = (close - rolling_min) / rolling_min
    resistance_distance = (rolling_max - close) / close
    
    return {
        'support_distance': support_distance,
        'resistance_distance': resistance_distance,
        'support_level': rolling_min,
        'resistance_level': rolling_max
    }


def price_patterns(open_series: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> Dict[str, pd.Series]:
    """Candlestick patterns"""
    body = abs(close - open_series)
    upper_shadow = high - pd.concat([open_series, close], axis=1).max(axis=1)
    lower_shadow = pd.concat([open_series, close], axis=1).min(axis=1) - low
    
    # Doji pattern
    doji = (body / (high - low) < 0.1).astype(int)
    
    # Hammer pattern
    hammer = ((lower_shadow > 2 * body) & (upper_shadow < body)).astype(int)
    
    # Shooting star
    shooting_star = ((upper_shadow > 2 * body) & (lower_shadow < body)).astype(int)
    
    return {
        'doji': doji,
        'hammer': hammer, 
        'shooting_star': shooting_star,
        'body_ratio': body / (high - low),
        'upper_shadow_ratio': upper_shadow / (high - low),
        'lower_shadow_ratio': lower_shadow / (high - low)
    }


def volume_indicators(close: pd.Series, volume: pd.Series) -> Dict[str, pd.Series]:
    """Volume-based indicators"""
    # On Balance Volume
    obv = (volume * np.sign(close.diff())).cumsum()
    
    # Price Volume Trend  
    pvt = (volume * (close.pct_change())).cumsum()
    
    # Volume Rate of Change
    vroc = volume.pct_change(periods=10)
    
    return {
        'obv': obv,
        'pvt': pvt,
        'vroc': vroc,
        'volume_sma': volume.rolling(20).mean(),
        'volume_ratio': volume / volume.rolling(20).mean()
    }


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Enhanced feature engineering with advanced technical indicators"""
    df = df.copy()
    
    # Original indicators
    df["rsi_14"] = rsi(df["close"], 14)
    df["rsi_7"] = rsi(df["close"], 7)  # Shorter period RSI
    df["rsi_21"] = rsi(df["close"], 21)  # Longer period RSI
    
    # MACD with multiple timeframes
    macd_line, macd_sig, macd_hist = macd(df["close"], 12, 26, 9)
    df["macd_line"], df["macd_signal"], df["macd_hist"] = macd_line, macd_sig, macd_hist
    
    # Fast MACD
    macd_fast_line, macd_fast_sig, macd_fast_hist = macd(df["close"], 5, 13, 4)
    df["macd_fast_line"], df["macd_fast_signal"], df["macd_fast_hist"] = macd_fast_line, macd_fast_sig, macd_fast_hist
    
    # Bollinger Bands with multiple periods
    bb_mid, bb_up, bb_lo = bollinger(df["close"], 20, 2.0)
    df["bb_basis"], df["bb_upper"], df["bb_lower"] = bb_mid, bb_up, bb_lo
    
    bb_mid_short, bb_up_short, bb_lo_short = bollinger(df["close"], 10, 1.5)
    df["bb_basis_short"], df["bb_upper_short"], df["bb_lower_short"] = bb_mid_short, bb_up_short, bb_lo_short
    
    # Advanced momentum indicators
    df["adx_14"] = adx(df["high"], df["low"], df["close"], 14)
    
    stoch_k, stoch_d = stochastic(df["high"], df["low"], df["close"], 14, 3)
    df["stoch_k"], df["stoch_d"] = stoch_k, stoch_d
    
    df["williams_r"] = williams_r(df["high"], df["low"], df["close"], 14)
    df["cci_20"] = cci(df["high"], df["low"], df["close"], 20)
    
    # Volatility indicators
    df["atr_14"] = atr(df["high"], df["low"], df["close"], 14)
    df["atr_7"] = atr(df["high"], df["low"], df["close"], 7)
    
    # Volume indicators (if volume available)
    if "volume" in df.columns and df["volume"].sum() > 0:
        df["mfi_14"] = mfi(df["high"], df["low"], df["close"], df["volume"], 14)
        df["vwap"] = vwap(df["high"], df["low"], df["close"], df["volume"])
        
        vol_indicators = volume_indicators(df["close"], df["volume"])
        for key, value in vol_indicators.items():
            df[f"vol_{key}"] = value
    
    # Ichimoku components
    ichimoku_dict = ichimoku(df["high"], df["low"], df["close"], 9, 26, 52)
    for key, value in ichimoku_dict.items():
        df[f"ichi_{key}"] = value
    
    # Fibonacci levels
    fib_levels = fibonacci_levels(df["high"], df["low"], 20)
    for key, value in fib_levels.items():
        df[key] = value
        # Distance to fibonacci levels
        df[f"{key}_distance"] = (df["close"] - value) / value
    
    # Support/Resistance
    sr_levels = support_resistance(df["close"], 20, 2)
    for key, value in sr_levels.items():
        df[f"sr_{key}"] = value
    
    # Price patterns
    if "open" in df.columns:
        patterns = price_patterns(df["open"], df["high"], df["low"], df["close"])
        for key, value in patterns.items():
            df[f"pattern_{key}"] = value
    
    # Multiple timeframe EMAs
    for period in [5, 9, 12, 21, 50, 100, 200]:
        df[f"ema_{period}"] = ema(df["close"], period)
        # EMA slopes
        df[f"ema_{period}_slope"] = df[f"ema_{period}"].diff(3)
        # Price relative to EMA
        df[f"close_vs_ema_{period}"] = (df["close"] - df[f"ema_{period}"]) / df[f"ema_{period}"]
    
    # Price momentum features
    for period in [1, 3, 5, 10, 20]:
        df[f"returns_{period}"] = df["close"].pct_change(period)
        df[f"price_rank_{period}"] = df["close"].rolling(period).rank(pct=True)
    
    # Volatility features
    df["price_volatility_10"] = df["close"].rolling(10).std()
    df["price_volatility_20"] = df["close"].rolling(20).std()
    df["returns_volatility"] = df["close"].pct_change().rolling(20).std()
    
    # Market structure
    df["higher_high"] = (df["high"] > df["high"].shift(1)).astype(int)
    df["lower_low"] = (df["low"] < df["low"].shift(1)).astype(int)
    df["inside_bar"] = ((df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))).astype(int)
    df["outside_bar"] = ((df["high"] > df["high"].shift(1)) & (df["low"] < df["low"].shift(1))).astype(int)
    
    # Z-scores for mean reversion
    for period in [10, 20, 50]:
        df[f"close_z{period}"] = (df["close"] - df["close"].rolling(period).mean()) / (df["close"].rolling(period).std() + 1e-9)
        df[f"volume_z{period}"] = (df.get("volume", 0) - df.get("volume", pd.Series([0]*len(df))).rolling(period).mean()) / (df.get("volume", pd.Series([0]*len(df))).rolling(period).std() + 1e-9)
    
    # Bollinger Band position
    df["bb_position"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_basis"]
    
    # RSI divergence (simple approximation)
    df["rsi_divergence"] = df["rsi_14"].diff(5) - (df["close"].pct_change(5) * 100)
    
    # Create slopes for all key indicators
    slope_indicators = [
        "rsi_14", "rsi_7", "rsi_21", "macd_line", "macd_signal", "macd_hist",
        "bb_basis", "bb_upper", "bb_lower", "close", "adx_14", "stoch_k", "stoch_d",
        "williams_r", "cci_20", "atr_14", "atr_7"
    ]
    
    for col in slope_indicators:
        if col in df.columns:
            df[f"{col}_slope3"] = df[col].diff(3)
            df[f"{col}_slope5"] = df[col].diff(5)
    
    return df


def make_target(df: pd.DataFrame, horizon: int, threshold: float) -> pd.Series:
    fut = df["close"].shift(-horizon)
    ret = (fut - df["close"]) / df["close"]
    return (ret > threshold).astype(int)


def select_features(df: pd.DataFrame, max_features: int = 18, method: str = "auto"):
    """
    🎯 SISTEMA DE SELEÇÃO AUTOMÁTICA DE FEATURES OTIMIZADO
    Reduz de ~53+ features para as mais importantes baseado em:
    1. Importância técnica conhecida
    2. Correlação baixa entre features
    3. Estabilidade temporal
    """
    # 🎯 FEATURES CORE (sempre incluir) - as mais importantes para trading
    core_features = [
        "close", "rsi_14", "macd_line", "macd_signal", "adx_14", 
        "bb_position", "atr_14", "returns_1", "close_vs_ema_21"
    ]
    
    # 🎯 FEATURES COMPLEMENTARES (rankeadas por importância)  
    complementary_features = [
        "macd_hist", "rsi_7", "bb_width", "stoch_k", "williams_r",
        "cci_20", "ema_21_slope", "price_volatility_10", "close_z20",
        "returns_3", "price_rank_10", "atr_7", "rsi_divergence",
        "macd_fast_line", "bb_basis", "higher_high", "lower_low",
        "returns_5", "ema_9", "stoch_d", "vol_volume_ratio"
    ]
    
    available_features = []
    
    # Adicionar features core disponíveis
    for feat in core_features:
        if feat in df.columns and not df[feat].isna().all():
            available_features.append(feat)
    
    # Adicionar features complementares até atingir max_features
    remaining_slots = max_features - len(available_features)
    
    if method == "auto" and remaining_slots > 0:
        # 🎯 SELEÇÃO BASEADA EM CORRELAÇÃO E IMPORTÂNCIA
        feature_scores = {}
        
        for feat in complementary_features:
            if feat in df.columns and not df[feat].isna().all() and len(available_features) < max_features:
                # Calcular score baseado em:
                # 1. Variabilidade (evitar features constantes)
                # 2. Correlação com features já selecionadas (evitar redundância)
                
                feat_data = df[feat].dropna()
                if len(feat_data) < 10:
                    continue
                    
                # Score 1: Variabilidade (std normalizado)
                variability_score = feat_data.std() / (abs(feat_data.mean()) + 1e-6)
                
                # Score 2: Anti-correlação (penalizar alta correlação com features existentes)
                correlation_penalty = 0
                for existing_feat in available_features:
                    if existing_feat in df.columns:
                        try:
                            corr = df[feat].corr(df[existing_feat])
                            if not pd.isna(corr):
                                correlation_penalty += abs(corr)
                        except:
                            pass
                
                # Score combinado (maior variabilidade, menor correlação)
                combined_score = variability_score - (correlation_penalty * 0.3)
                feature_scores[feat] = combined_score
        
        # Selecionar features com maior score
        sorted_features = sorted(feature_scores.items(), key=lambda x: x[1], reverse=True)
        for feat, score in sorted_features:
            if len(available_features) < max_features:
                available_features.append(feat)
    
    # Fallback: adicionar features complementares na ordem se necessário
    for feat in complementary_features:
        if feat in df.columns and feat not in available_features and len(available_features) < max_features:
            available_features.append(feat)
    
    # Garantir que temos pelo menos algumas features básicas
    if len(available_features) < 5:
        basic_features = ["close", "open", "high", "low", "volume"]
        for feat in basic_features:
            if feat in df.columns and feat not in available_features:
                available_features.append(feat)
                if len(available_features) >= 5:
                    break
    
    logger.info(f"🎯 FEATURE SELECTION: Selecionadas {len(available_features)} features de {len(df.columns)} disponíveis")
    logger.info(f"🎯 FEATURES CORE: {[f for f in core_features if f in available_features]}")
    
    return available_features


def remove_correlated_features(df: pd.DataFrame, features: List[str], threshold: float = 0.95) -> List[str]:
    """Remove highly correlated features to reduce multicollinearity"""
    if len(features) <= 1:
        return features
        
    # Calculate correlation matrix for selected features
    feature_data = df[features].fillna(method='ffill').fillna(0)
    corr_matrix = feature_data.corr().abs()
    
    # Find highly correlated pairs
    upper_tri = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
    
    # Identify features to drop
    to_drop = [column for column in upper_tri.columns if any(upper_tri[column] > threshold)]
    
    # Keep features that aren't highly correlated
    final_features = [f for f in features if f not in to_drop]
    
    return final_features


def add_feature_interactions(df: pd.DataFrame, max_interactions: int = 10) -> pd.DataFrame:
    """Add feature interactions for key indicators"""
    df = df.copy()
    
    # Key feature pairs for interactions
    interaction_pairs = [
        ("rsi_14", "bb_position"),
        ("macd_hist", "adx_14"), 
        ("stoch_k", "williams_r"),
        ("atr_14", "close_z20"),
        ("ema_9_slope", "ema_21_slope"),
        ("returns_3", "price_volatility_10"),
        ("bb_width", "atr_14"),
        ("rsi_14", "stoch_k"),
        ("macd_line", "cci_20"),
        ("adx_14", "atr_14")
    ]
    
    count = 0
    for feat1, feat2 in interaction_pairs:
        if count >= max_interactions:
            break
        if feat1 in df.columns and feat2 in df.columns:
            # Multiplicative interaction
            df[f"{feat1}_x_{feat2}"] = df[feat1] * df[feat2]
            count += 1
    
    return df


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


def _get_estimator(model_type: str = "rf", class_weight: Optional[str] = None):
    if model_type == "dt":
        return DecisionTreeClassifier(max_depth=4, random_state=42, min_samples_leaf=20, class_weight=class_weight)
    # rf default
    return RandomForestClassifier(
        n_estimators=300,
        max_depth=None,
        min_samples_leaf=20,
        random_state=42,
        n_jobs=-1,
        class_weight=class_weight,
    )


def _fit_with_calibration(base_model, Xtr: pd.DataFrame, ytr: pd.Series, calibrate: Optional[str] = None):
    if calibrate not in {"sigmoid", "isotonic"}:
        base_model.fit(Xtr, ytr)
        return base_model
    # split last 10% of training for calibration to avoid leakage
    n = len(Xtr)
    cut = max(int(n * 0.9), 1)
    X_fit, y_fit = Xtr.iloc[:cut], ytr.iloc[:cut]
    X_cal, y_cal = Xtr.iloc[cut:], ytr.iloc[cut:]
    base_model.fit(X_fit, y_fit)
    try:
        calibrated = CalibratedClassifierCV(base_model, method=calibrate, cv="prefit")
        calibrated.fit(X_cal, y_cal)
        return calibrated
    except Exception:
        # fallback to uncalibrated if calibration fails
        return base_model


def _eval_with_ev(y_true: pd.Series, y_pred: pd.Series, y_proba: Optional[np.ndarray], close_series: pd.Series, horizon: int, payout_ratio: float, candles_per_day: float) -> Dict[str, Any]:
    # trades are only where model predicts 1 (we enter positions only on 1)
    trades_mask = (y_pred == 1)
    trades = int(trades_mask.sum())
    tp = int(((y_true == 1) & trades_mask).sum())
    fp = int(((y_true == 0) & trades_mask).sum())
    wins = tp
    losses = fp
    ev_total = wins * payout_ratio - losses * 1.0
    ev_per_trade = float(ev_total / trades) if trades > 0 else 0.0

    # approximate trades/day using candle frequency
    n = len(y_true)
    days = float(n / max(candles_per_day, 1.0))
    trades_per_day = float(trades / days) if days > 0 else float(trades)

    base_metrics = compute_metrics(y_true, y_pred, y_proba)
    base_metrics.update({
        "wins": wins,
        "losses": losses,
        "trades": trades,
        "ev_per_trade": ev_per_trade,
        "trades_per_day": trades_per_day,
    })
    # backtest proxy using predicted entries
    bt = backtest_simple(close_series, pd.Series(y_pred, index=close_series.index), horizon)
    base_metrics.update({"equity_final": bt.get("equity_final", 0.0), "max_drawdown": bt.get("max_drawdown", 0.0)})
    return base_metrics


def train_walkforward_and_maybe_promote(
    df: pd.DataFrame,
    horizon: int,
    threshold: float,
    model_type: str = "rf",
    save_prefix: str = "weekly",
    class_weight: Optional[str] = None,
    calibrate: Optional[str] = None,
    train_size: float = 0.7,
    n_splits: int = 3,
    payout_ratio: float = 0.95,
    candles_per_day: float = 480.0,
    objective: str = "f1",
) -> Dict[str, Any]:
    # Enhanced dataset building with advanced features
    feats_df = build_features(df)
    
    # Add feature interactions
    feats_df = add_feature_interactions(feats_df, max_interactions=15)
    
    y = make_target(df, horizon=horizon, threshold=threshold)
    
    # Enhanced feature selection
    initial_features = select_features(feats_df)
    
    # Remove highly correlated features
    final_features = remove_correlated_features(feats_df, initial_features, threshold=0.95)
    
    print(f"Feature engineering: {len(initial_features)} initial → {len(final_features)} final features")
    
    X = feats_df[final_features].replace([np.inf, -np.inf], np.nan)
    mask = X.notna().all(axis=1) & y.notna()
    X, y = X[mask], y[mask]
    close_series = df.loc[X.index, "close"]
    
    if len(X) < 800:
        raise ValueError("Dados insuficientes após limpeza (>= 800 linhas)")

    n = len(X)
    first_cut = int(n * train_size)
    step = max(int((n - first_cut) / max(n_splits, 1)), 1)

    agg = {
        "tp": 0,
        "fp": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "ev_total": 0.0,
        "days": 0.0,
        "metrics": [],
    }

    last_model = None
    for i in range(n_splits):
        train_end = first_cut + i * step
        if train_end >= n - 1:
            break
        test_end = min(train_end + step, n)
        Xtr, ytr = X.iloc[:train_end], y.iloc[:train_end]
        Xte, yte = X.iloc[train_end:test_end], y.iloc[train_end:test_end]
        closete = close_series.iloc[train_end:test_end]
        if len(Xte) == 0 or len(Xtr) < 100:
            continue
        est = _get_estimator(model_type, class_weight=class_weight)
        model = _fit_with_calibration(est, Xtr, ytr, calibrate)
        last_model = model
        y_pred = model.predict(Xte)
        y_proba = None
        try:
            if hasattr(model, "predict_proba"):
                y_proba = model.predict_proba(Xte)[:, 1]
        except Exception:
            y_proba = None
        # candles/day approx
        candles_per_day_local = candles_per_day
        fold_metrics = _eval_with_ev(yte, pd.Series(y_pred, index=yte.index), y_proba, closete, horizon, payout_ratio, candles_per_day_local)
        agg["tp"] += int(fold_metrics.get("wins", 0))
        agg["fp"] += int(fold_metrics.get("losses", 0))
        agg["trades"] += int(fold_metrics.get("trades", 0))
        agg["wins"] += int(fold_metrics.get("wins", 0))
        agg["losses"] += int(fold_metrics.get("losses", 0))
        agg["ev_total"] += float(fold_metrics.get("ev_per_trade", 0.0)) * max(int(fold_metrics.get("trades", 0)), 0)
        agg["days"] += float(len(Xte) / max(candles_per_day_local, 1.0))
        agg["metrics"].append(fold_metrics)

    # aggregate
    trades = agg["trades"]
    wins = agg["wins"]
    losses = agg["losses"]
    precision = float(wins / trades) if trades > 0 else 0.0
    recall = None  # not exact here without fn; compute from last fold metrics if available
    ev_per_trade = float(agg["ev_total"] / trades) if trades > 0 else 0.0
    trades_per_day = float(trades / agg["days"]) if agg["days"] > 0 else float(trades)

    metrics = {
        "accuracy": None,
        "precision": precision,
        "recall": recall,
        "f1": (2 * precision * (recall or 0.0) / (precision + (recall or 1e-9))) if recall is not None else 0.0,
        "roc_auc": None,
        "ev_per_trade": ev_per_trade,
        "trades": trades,
        "trades_per_day": trades_per_day,
        "num_features": len(final_features),  # Track feature count
    }

    # Fit final model on full data using the same configuration
    final_est = _get_estimator(model_type, class_weight=class_weight)
    final_model = _fit_with_calibration(final_est, X, y, calibrate)

    model_id = f"{save_prefix}_{model_type}"
    model_path = ML_DIR / f"{model_id}.joblib"
    dump({"model": final_model, "features": final_features}, model_path)

    # backtest proxy using full data predictions
    try:
        full_pred = final_model.predict(X)
        bt = backtest_simple(close_series, pd.Series(full_pred, index=close_series.index), horizon)
    except Exception:
        bt = {"equity_final": 0.0, "max_drawdown": 0.0}

    champ = load_champion()
    cur_prec = float(champ.get("metrics", {}).get("precision", 0.0) or 0.0)
    cur_ev = float(champ.get("backtest", {}).get("ev_per_trade", 0.0) or 0.0)

    # Enhanced promotion policy: prioritize precision, then EV, then drawdown
    promoted = bool((metrics["precision"] >= max(0.5, 1.05 * cur_prec)) and (metrics["ev_per_trade"] >= cur_ev))
    if promoted:
        meta = {
            "model_id": model_id,
            "path": str(model_path),
            "metrics": metrics,
            "backtest": {**bt, "ev_per_trade": ev_per_trade},
            "horizon": horizon,
            "threshold": threshold,
            "ts_rows": int(len(X)),
            "vs_rows": int(max(1, int((1 - train_size) * n))),
            "features": final_features,  # Store feature list
        }
        meta["updated_at"] = datetime.utcnow().isoformat() + "Z"
        save_champion(meta)

    return {
        "model_id": model_id,
        "metrics": metrics,
        "backtest": {**bt, "ev_per_trade": ev_per_trade},
        "promoted": promoted,
        "features_used": len(final_features),
    }


# Backward compatible function name used by server.py
# Note: Kept to avoid large code changes elsewhere

def train_and_maybe_promote(
    df: pd.DataFrame,
    horizon: int,
    threshold: float,
    model_type: str = "rf",
    save_prefix: str = "weekly",
    class_weight: Optional[str] = None,
    calibrate: Optional[str] = None,
    payout_ratio: float = 0.95,
    candles_per_day: float = 480.0,
    objective: str = "f1",
) -> Dict[str, Any]:
    return train_walkforward_and_maybe_promote(
        df,
        horizon=horizon,
        threshold=threshold,
        model_type=model_type,
        save_prefix=save_prefix,
        class_weight=class_weight,
        calibrate=calibrate,
        payout_ratio=payout_ratio,
        candles_per_day=candles_per_day,
        objective=objective,
    )