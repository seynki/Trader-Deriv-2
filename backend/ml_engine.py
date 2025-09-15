"""
Módulo ML para bot de Volatility (Deriv).
Entrega: modelagem supervisionada + transformer sequence + ensembling + walk-forward backtest.
Use em DEMO. NÃO GARANTE LUCRO.
"""

import math
import time
from typing import List, Tuple, Dict, Any, Optional
import numpy as np
import pandas as pd
from dataclasses import dataclass
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import Dataset, DataLoader
except Exception:
    torch = None
    class _Dummy: pass
    nn = _Dummy()
    Dataset = object
    def DataLoader(*args, **kwargs):
        raise RuntimeError("PyTorch não está instalado nesta imagem. ML Engine funcionará em modo LightGBM apenas.")
from tqdm import tqdm
import joblib
import logging
from scipy import stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ------------------------
# Config / Hyperparams
# ------------------------
@dataclass
class MLConfig:
    lookback_seconds: int = 60             # janela de entrada (em segundos)
    candle_agg: Tuple[str,int] = ("s",1)   # como os candles são gerados (type, val) — usado para shape
    seq_len: int = 32                      # comprimento de janelas para o transformer
    feat_freqs: List[int] = (1,5,15,60)    # janelas (em segundos) para features multiescala
    lgb_params: Dict = None
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    ensemble_weights: Dict[str,float] = None

    def __post_init__(self):
        if self.lgb_params is None:
            self.lgb_params = {
                "objective":"binary",
                "metric":"auc",
                "verbosity":-1,
                "boosting_type":"gbdt",
                "learning_rate":0.05,
                "num_leaves":31,
                "n_estimators":200,
            }
        if self.ensemble_weights is None:
            self.ensemble_weights = {"lgb":0.6, "transformer":0.4}

CFG = MLConfig()

# ------------------------
# Feature engineering
# ------------------------
def compute_technical_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    """
    candles: DataFrame com index ts (timestamp) e colunas open,high,low,close,volume
    Retorna DataFrame com features técnicas.
    """
    if candles.empty:
        return candles
    df = candles.copy()
    # basic returns
    df['ret_1'] = df['close'].pct_change(1).fillna(0.0)
    df['ret_2'] = df['close'].pct_change(2).fillna(0.0)
    # moving averages
    df['ma_5'] = df['close'].rolling(5, min_periods=1).mean()
    df['ma_10'] = df['close'].rolling(10, min_periods=1).mean()
    df['ma_20'] = df['close'].rolling(20, min_periods=1).mean()
    # ema
    df['ema_8'] = df['close'].ewm(span=8, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    # volatility
    df['std_10'] = df['close'].rolling(10, min_periods=1).std().fillna(0.0)
    df['std_30'] = df['close'].rolling(30, min_periods=1).std().fillna(0.0)
    # momentum / RSI-like (simple)
    delta = df['close'].diff().fillna(0.0)
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.rolling(14, min_periods=1).mean()
    avg_down = down.rolling(14, min_periods=1).mean()
    rs = avg_up / (avg_down + 1e-9)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    # volume features
    df['vol_mean_10'] = df['volume'].rolling(10, min_periods=1).mean()
    # price position relative to MA
    df['pct_from_ma20'] = (df['close'] - df['ma_20']) / (df['ma_20'] + 1e-9)
    # returns normalization
    df.fillna(method='ffill', inplace=True)
    df.fillna(0.0, inplace=True)
    return df

def multi_scale_features(candles: pd.DataFrame, freqs: List[int]) -> pd.DataFrame:
    """
    Gera features agregadas em múltiplas escalas (freqs em unidades de candles).
    Aqui assumimos candles já na granularidade base (ex: 1s).
    """
    if candles.empty:
        return candles
    feats = []
    for f in freqs:
        rolled = candles['close'].rolling(f, min_periods=1).agg(['mean','std','max','min']).add_prefix(f'_{f}_')
        feats.append(rolled)
    base = compute_technical_indicators(candles)
    for r in feats:
        base = pd.concat([base, r], axis=1)
    base.fillna(0.0, inplace=True)
    return base

# ------------------------
# Dataset & windows
# ------------------------
class TimeSeriesDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray):
        assert len(X) == len(y)
        self.X = X.astype(np.float32)
        self.y = y.astype(np.float32)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]

def build_supervised_dataset(candles: pd.DataFrame, seq_len: int, horizon: int = 3) -> Tuple[np.ndarray,np.ndarray]:
    """
    target: prob that price closes higher after `horizon` candles.
    Returns X: windows (num_samples, seq_len, num_features) or flattened (for LGB).
    We'll return flattened X for LGB and sequence X for transformer (user can reshape).
    """
    df = candles.copy()
    df = multi_scale_features(df, CFG.feat_freqs)
    # target binary (up/down) using future close after `horizon`
    df['future_close'] = df['close'].shift(-horizon)
    df['target'] = (df['future_close'] > df['close']).astype(int)
    df.dropna(inplace=True)
    features = [c for c in df.columns if c not in ['future_close','target']]
    arr = df[features].values
    targets = df['target'].values
    # build sliding windows
    X_seq = []
    y_seq = []
    for i in range(len(arr) - seq_len + 1):
        X_seq.append(arr[i:i+seq_len])
        y_seq.append(targets[i+seq_len-1])  # target aligned to end of window
    X_seq = np.array(X_seq)
    y_seq = np.array(y_seq)
    # flattened for LGB: use only last row of each window (or aggregates)
    # We'll use aggregates across the window: mean, std, last
    X_lgb = []
    for w in X_seq:
        mean = w.mean(axis=0)
        std = w.std(axis=0)
        last = w[-1]
        X_lgb.append(np.concatenate([mean, std, last]))
    X_lgb = np.vstack(X_lgb)
    return X_lgb, X_seq, y_seq, features

# ------------------------
# LightGBM trainer
# ------------------------
def train_lgb(X: np.ndarray, y: np.ndarray, cfg: MLConfig = CFG) -> lgb.LGBMClassifier:
    # simple time-series split
    tscv = TimeSeriesSplit(n_splits=3)
    models = []
    best_model = None
    best_score = -np.inf
    for train_idx, val_idx in tscv.split(X):
        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]
        clf = lgb.LGBMClassifier(**cfg.lgb_params)
        clf.fit(X_train, y_train,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)])
        preds = clf.predict_proba(X_val)[:,1]
        auc = roc_auc_score(y_val, preds)
        logging.info(f"LGB fold AUC: {auc:.4f}")
        models.append(clf)
        if auc > best_score:
            best_score = auc
            best_model = clf
    logging.info(f"LGB best AUC (cv): {best_score:.4f}")
    return best_model

# ------------------------
# Transformer Sequence Model (PyTorch)
# ------------------------
class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=5000):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len).unsqueeze(1).float()
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)

    def forward(self, x):
        x = x + self.pe[:, :x.size(1)]
        return x

class SeqTransformer(nn.Module if hasattr(nn, "Module") else object):
    def __init__(self, input_dim, d_model=64, nhead=4, num_layers=2, dim_feedforward=128, dropout=0.1):
        super().__init__()
        self.input_proj = nn.Linear(input_dim, d_model)
        self.pos_enc = PositionalEncoding(d_model)
        encoder_layer = nn.TransformerEncoderLayer(d_model, nhead, dim_feedforward, dropout, batch_first=True)
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.pool = nn.AdaptiveAvgPool1d(1)  # pool across seq dim
        self.head = nn.Sequential(
            nn.Linear(d_model, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
            nn.Sigmoid()
        )

    def forward(self, x):
        # x: batch, seq_len, feat
        x = self.input_proj(x)
        x = self.pos_enc(x)
        x = self.encoder(x)  # batch, seq_len, d_model
        # transpose for pooling: batch, d_model, seq_len
        x_t = x.permute(0,2,1)
        pooled = self.pool(x_t).squeeze(-1)
        out = self.head(pooled).squeeze(-1)
        return out

def train_transformer(X_seq: np.ndarray, y: np.ndarray, cfg: MLConfig = CFG, epochs: int = 10, batch_size: int = 128):
    if torch is None:
        logging.warning("PyTorch ausente – pulando treino do transformer (modo LightGBM-only)")
        return None
    device = "cuda" if (hasattr(torch, "cuda") and torch.cuda.is_available()) else "cpu"
    model = SeqTransformer(input_dim=X_seq.shape[2]).to(device)
    criterion = nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
    ds = TimeSeriesDataset(X_seq, y)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=False)
    model.train()
    for ep in range(epochs):
        losses = []
        for xb, yb in loader:
            xb = xb.to(device)
            yb = yb.to(device)
            pred = model(xb)
            loss = criterion(pred, yb)
            optimizer.zero_grad()
            loss.backward()
            try:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            except Exception:
                pass
            optimizer.step()
            losses.append(loss.item())
        logging.info(f"Transformer epoch {ep+1}/{epochs} loss={np.mean(losses):.6f}")
    return model

# ------------------------
# Ensemble / Predict wrapper
# ------------------------
@dataclass
class TrainedModels:
    lgb_model: Optional[lgb.LGBMClassifier] = None
    lgb_scaler: Optional[StandardScaler] = None
    transformer: Optional[nn.Module] = None
    features: Optional[List[str]] = None
    lgb_feat_dim: Optional[int] = None

def fit_models_from_candles(candles: pd.DataFrame, cfg: MLConfig = CFG, horizon: int = 3, use_transformer: bool = True, transformer_epochs: int = 6, transformer_batch: int = 64) -> TrainedModels:
    X_lgb, X_seq, y, feat_names = None, None, None, None
    X_lgb, X_seq, y, features = build_supervised_dataset(candles, seq_len=cfg.seq_len, horizon=horizon)
    # standardize LGB features (improves some models)
    scaler = StandardScaler()
    X_lgb_s = scaler.fit_transform(X_lgb)
    lgb_model = train_lgb(X_lgb_s, y, cfg)
    # transformer
    transformer = None
    if train_transformer:
        transformer = train_transformer(X_seq, y, cfg, epochs=transformer_epochs, batch_size=transformer_batch)
    tm = TrainedModels(lgb_model=lgb_model, lgb_scaler=scaler, transformer=transformer, features=features, lgb_feat_dim=X_lgb.shape[1])
    return tm

def predict_from_models(candles: pd.DataFrame, tm: TrainedModels, cfg: MLConfig = CFG) -> Dict[str,Any]:
    """
    Gera previsões a partir dos modelos treinados para a janela mais recente.
    Retorna: {'prob': combined_prob, 'prob_lgb':..., 'prob_trans':..., 'conf':..., 'direction': 'CALL'/'PUT'}
    """
    if candles.empty:
        return {"prob":0.5, "prob_lgb":0.5, "prob_trans":0.5, "conf":0.0, "direction":None}

    # preparar features (take last seq_len window)
    df = candles.copy()
    X_lgb_full, X_seq_full, y, features = build_supervised_dataset(df, seq_len=cfg.seq_len, horizon=3)
    if len(X_lgb_full)==0:
        return {"prob":0.5, "prob_lgb":0.5, "prob_trans":0.5, "conf":0.0, "direction":None}
    # take last sample
    x_lgb = X_lgb_full[-1:]
    x_seq = X_seq_full[-1:]
    # LGB
    if tm.lgb_model is not None:
        x_lgb_s = tm.lgb_scaler.transform(x_lgb)
        prob_lgb = tm.lgb_model.predict_proba(x_lgb_s)[:,1][0]
    else:
        prob_lgb = 0.5
    # transformer
    if tm.transformer is not None:
        model = tm.transformer
        model.eval()
        with torch.no_grad():
            xb = torch.tensor(x_seq, dtype=torch.float32).to(cfg.device)
            pred = model(xb).cpu().numpy().reshape(-1)
            prob_trans = float(pred[-1])
    else:
        prob_trans = 0.5
    # combine
    w = cfg.ensemble_weights
    combined = prob_lgb * w.get('lgb',0.5) + prob_trans * w.get('transformer',0.5)
    conf = abs(combined - 0.5) * 2.0  # 0..1
    direction = "CALL" if combined > 0.5 else "PUT"
    return {"prob":combined, "prob_lgb":prob_lgb, "prob_trans":prob_trans, "conf":conf, "direction":direction}

# ------------------------
# Walk-forward backtester (simplified)
# ------------------------
def walk_forward_backtest(candles: pd.DataFrame, train_window_sec: int=300, test_window_sec: int=60, step_sec: int=60, cfg: MLConfig = CFG):
    """
    Walk-forward: itera pela série treinando em janela passada e testando imediatamente após.
    Retorna métricas agregadas (winrate, net simulated payout).
    Observação: payout e expiry aqui são hipotéticos — ajustar para refletir payouts reais da Deriv.
    """
    results = []
    timestamps = candles.index.values.astype(float)
    start = timestamps[0] + train_window_sec
    end = timestamps[-1]
    pos = 0
    while start + test_window_sec <= end:
        train_mask = (timestamps >= (start - train_window_sec)) & (timestamps < start)
        test_mask = (timestamps >= start) & (timestamps < start + test_window_sec)
        train_df = candles.iloc[train_mask]
        test_df = candles.iloc[test_mask]
        if len(train_df) < cfg.seq_len*2 or len(test_df) < cfg.seq_len:
            start += step_sec
            continue
        # train models
        tm = fit_models_from_candles(train_df, cfg, horizon=3)
        # roll through test_df in sliding step and predict
        predictions = []
        # we will step by 1 candle
        for i in range(cfg.seq_len, len(test_df)):
            window = pd.concat([train_df, test_df.iloc[:i]])
            # keep only last part (we already trained on train_df; but this mimics online)
            window_tail = window.iloc[-(cfg.seq_len+3):]  # ensure horizon available
            pred = predict_from_models(window_tail, tm, cfg)
            # simulate trade result using real future price after horizon (3)
            # the real future is in test_df at position i+3-1
            idx_future = test_df.index[i + 3 - 1] if (i + 3 - 1) < len(test_df) else None
            if idx_future is None:
                continue
            price_now = window_tail['close'].iloc[-1]
            price_future = test_df.loc[idx_future,'close']
            direction = pred['direction']
            win = (price_future > price_now and direction=="CALL") or (price_future < price_now and direction=="PUT")
            payout = 0.8 if win else -1.0
            predictions.append(payout)
        if predictions:
            net = sum(predictions)
            wins = sum(1 for p in predictions if p>0)
            trades = len(predictions)
            results.append({"start":start, "net":net, "wins":wins, "trades":trades, "winrate": wins/trades})
        start += step_sec
    # aggregate
    if not results:
        return {"net":0.0,"winrate":None,"trades":0}
    total_net = sum(r['net'] for r in results)
    total_trades = sum(r['trades'] for r in results)
    total_wins = sum(r['wins'] for r in results)
    return {"net":total_net, "winrate": (total_wins/total_trades if total_trades>0 else None), "trades":total_trades}

# ------------------------
# Position sizing and risk utilities
# ------------------------
def kelly_fraction(winrate: float, payout: float) -> float:
    """
    Kelly fraction for binary bet: f* = (bp - q)/b
    where b = payout/stake (profit per stake), p=winrate, q=1-p
    payout here expected profit per unit staked (e.g., 0.8)
    returns fraction to risk of bankroll.
    """
    p = winrate
    q = 1 - p
    b = payout
    denom = b
    if denom == 0:
        return 0.0
    f = (b*p - q) / denom
    return max(0.0, f)

def position_size_by_fraction(bankroll: float, fraction: float) -> float:
    return bankroll * fraction

# ------------------------
# Integration helper for bot
# ------------------------
def ml_decide_and_size(candles: pd.DataFrame, tm: TrainedModels, cfg: MLConfig = CFG, bankroll: float = 1000.0, min_conf: float = 0.2) -> Dict[str,Any]:
    """
    Dado candles recentes e modelos treinados, retorna decisão e stake.
    - aplica regra: se conf >= min_conf e winrate_simulated >= threshold, então trade
    """
    pred = predict_from_models(candles, tm, cfg)
    conf = pred['conf']
    dirc = pred['direction']
    prob = pred['prob']
    # quick simulated winrate proxy: use prob as proxy
    winrate = prob
    # compute kelly fraction (payout hypothetical 0.8)
    payout = 0.8
    f = kelly_fraction(winrate, payout)
    # apply safety caps
    f_capped = min(f, 0.05)  # don't risk more than 5% per trade by default
    stake = position_size_by_fraction(bankroll, f_capped)
    decision = {
        "direction": dirc,
        "prob": prob,
        "conf": conf,
        "stake": stake,
        "fraction": f_capped,
        "do_trade": (conf >= min_conf and stake > 0)
    }
    return decision

# ------------------------
# Persistence helpers
# ------------------------
def save_trained_models(tm: TrainedModels, path_prefix: str):
    if tm.lgb_model is not None:
        joblib.dump(tm.lgb_model, f"{path_prefix}_lgb.pkl")
        joblib.dump(tm.lgb_scaler, f"{path_prefix}_scaler.pkl")
    # salvar transformer apenas se PyTorch estiver disponível
    if tm.transformer is not None and torch is not None:
        try:
            import torch as _torch
            _torch.save(tm.transformer.state_dict(), f"{path_prefix}_trans.pt")
        except Exception:
            pass
    # features meta
    joblib.dump({"features": tm.features, "lgb_feat_dim": tm.lgb_feat_dim}, f"{path_prefix}_meta.pkl")

def load_trained_models(path_prefix: str, cfg: MLConfig = CFG) -> TrainedModels:
    tm = TrainedModels()
    try:
        tm.lgb_model = joblib.load(f"{path_prefix}_lgb.pkl")
        tm.lgb_scaler = joblib.load(f"{path_prefix}_scaler.pkl")
    except Exception:
        tm.lgb_model = None
        tm.lgb_scaler = None
    try:
        meta = joblib.load(f"{path_prefix}_meta.pkl")
        tm.features = meta.get('features')
        tm.lgb_feat_dim = meta.get('lgb_feat_dim')
    except Exception:
        pass
    try:
        # to load transformer we need a model object with correct dims; we assume same input_dim
        if tm.features is not None and tm.lgb_feat_dim is not None:
            # We cannot reconstruct transformer input_dim from lgb meta easily; user should rebuild transformer separately
            pass
    except Exception:
        pass
    return tm

# ------------------------
# Example quick usage / integration (skeleton)
# ------------------------
if __name__ == "__main__":
    # small sanity check with synthetic data
    # create synthetic candles: 1s candles for 2000 seconds
    ts = np.arange(0,3000)
    price = 100 + np.cumsum(np.random.randn(len(ts))*0.01)
    df = pd.DataFrame({"open":price,"high":price,"low":price,"close":price,"volume":np.random.randint(1,10,len(ts))}, index=ts)
    logging.info("Building dataset...")
    try:
        tm = fit_models_from_candles(df, CFG)
        logging.info("Models trained.")
        dec = ml_decide_and_size(df.iloc[-200:], tm, CFG, bankroll=1000.0)
        logging.info(f"Decision sample: {dec}")
        # run walk-forward
        wf = walk_forward_backtest(df, train_window_sec=600, test_window_sec=120, step_sec=120, cfg=CFG)
        logging.info(f"Walk-forward result: {wf}")
    except Exception as e:
        logging.exception("Erro durante execução de exemplo: %s", e)