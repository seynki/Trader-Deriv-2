#!/usr/bin/env python3

"""
Treinador de modelo (pseudo-ML) para Trading a partir de OHLCV.

Fluxo:
- Lê CSV com OHLCV (colunas: time, open, high, low, close, volume)
- Gera features técnicas em Python (pandas-ta): RSI/ADX/MACD/BB + slopes/zscore
- Define target como retorno futuro em horizon candles e binariza por threshold
- Treina RandomForest e/ou DecisionTree
- Reporta métricas e exporta regras legíveis (para DT) + gera PineScript base
- Salva modelos e artefatos em ./ml_out

Exemplos:
python scripts/train_ml_rules.py --csv data.csv --horizon 5 --threshold 0.003 --model rf --save-name exp_r100_1m
python scripts/train_ml_rules.py --csv data.csv --horizon 5 --threshold 0.003 --model dt --max-depth 4

Requisitos:
- pandas, numpy, scikit-learn, joblib, pandas-ta
"""

import argparse
import json
from pathlib import Path
import pandas as pd
import numpy as np
import pandas_ta as ta
from joblib import dump
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    confusion_matrix,
)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # RSI
    df["rsi_14"] = ta.rsi(df["close"], length=14)
    # ADX
    adx = ta.adx(high=df["high"], low=df["low"], close=df["close"], length=14)
    if adx is not None:
        df["adx_14"] = adx["ADX_14"]
    # MACD 12/26/9
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd_line"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"] = macd["MACDh_12_26_9"]
    # Bandas de Bollinger 20/2
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df["bb_basis"] = bb["BBM_20_2.0"]
        df["bb_upper"] = bb["BBU_20_2.0"]
        df["bb_lower"] = bb["BBL_20_2.0"]
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = compute_indicators(df)
    # Slopes simples (diferença em 3 períodos)
    for col in [
        "rsi_14",
        "adx_14",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "bb_basis",
        "bb_upper",
        "bb_lower",
        "close",
    ]:
        if col in df.columns:
            df[f"{col}_slope3"] = df[col].diff(3)
    # Z-score do close (20)
    df["close_z20"] = (df["close"] - df["close"].rolling(20).mean()) / (
        df["close"].rolling(20).std() + 1e-9
    )
    return df


def make_target_from_horizon(df: pd.DataFrame, horizon: int, threshold: float) -> pd.Series:
    fut = df["close"].shift(-horizon)
    ret = (fut - df["close"]) / df["close"]
    y = (ret > threshold).astype(int)
    return y


def select_feature_cols(df: pd.DataFrame):
    # Seleciona colunas úteis para treino
    candidates = [
        c
        for c in df.columns
        if any(
            k in c
            for k in [
                "rsi",
                "adx",
                "macd",
                "bb_",
                "close",
                "volume",
                "slope",
                "z",
            ]
        )
    ]
    blacklist = {"open", "high", "low"}
    feats = [c for c in candidates if c not in blacklist]
    # apenas numéricas
    return [c for c in feats if pd.api.types.is_numeric_dtype(df[c])]


def metrics_dict(y_true, y_pred, y_proba=None):
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, y_proba)) if y_proba is not None else None,
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
    }


def export_pine_from_rules(rules_text: str) -> str:
    # Extrai thresholds básicos das regras do DT e gera PineScript
    thresholds = []  # (feat, op, val)
    for ln in (rules_text or "").splitlines():
        s = ln.strip()
        if "<=" in s:
            f, v = s.split("<=")
            thresholds.append((f.strip(), "<=", v.strip()))
        elif ">" in s:
            f, v = s.split(">")
            thresholds.append((f.strip(), ">", v.strip()))
    thresholds = thresholds[:8]

    to_ps = {
        "rsi_14": "rsi",
        "adx_14": "adx",
        "macd_line": "macdLine",
        "macd_signal": "signalLine",
        "macd_hist": "macdHist",
        "bb_basis": "bbBasis",
        "bb_upper": "bbUpper",
        "bb_lower": "bbLower",
        "close": "close",
        "volume": "volume",
    }
    lines = [
        "//@version=5",
        'strategy("ML-Rules (gerado)", overlay=true)',
        "",
        "// Assumindo indicadores padrão no Pine:",
        "adx = ta.adx(14)",
        "rsi = ta.rsi(close, 14)",
        "macdLine = ta.ema(close, 12) - ta.ema(close, 26)",
        "signalLine = ta.ema(macdLine, 9)",
        "macdHist = macdLine - signalLine",
        "bbBasis = ta.sma(close, 20)",
        "bbUpper = bbBasis + ta.stdev(close, 20) * 2",
        "bbLower = bbBasis - ta.stdev(close, 20) * 2",
        "",
    ]
    for i, (feat, op, val) in enumerate(thresholds, 1):
        ps = to_ps.get(feat, feat)
        lines.append(f"cond_{i} = {ps} {op} {val}")
    long_conds = (
        " and ".join([f"cond_{i}" for i in range(1, len(thresholds) + 1)]) if thresholds else "false"
    )
    lines += [
        f"longCond = {long_conds}",
        "shortCond = false",
        "",
        'if longCond',
        '    strategy.entry("Long", strategy.long)',
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Caminho do CSV com OHLCV")
    ap.add_argument("--time-col", default="time", help="Nome da coluna temporal (opcional)")
    ap.add_argument("--horizon", type=int, default=5, help="Horizonte futuro em candles")
    ap.add_argument(
        "--threshold",
        type=float,
        default=0.003,
        help="Threshold para binarizar retorno futuro (ex.: 0.003 = 0.3%)",
    )
    ap.add_argument("--target", default="", help="Se informado, usa esta coluna 0/1 como alvo")
    ap.add_argument(
        "--model",
        choices=["rf", "dt", "both"],
        default="both",
        help="Modelo a treinar: RandomForest, DecisionTree ou ambos",
    )
    ap.add_argument("--max-depth", type=int, default=4, help="Profundidade DT para regras")
    ap.add_argument("--test-size", type=float, default=0.2, help="Fraçao de teste (holdout temporal)")
    ap.add_argument("--save-name", default="run", help="Prefixo para salvar artefatos")
    args = ap.parse_args()

    out_dir = Path("ml_out")
    out_dir.mkdir(exist_ok=True)

    df = pd.read_csv(args.csv)
    # Normaliza nomes
    cols_lower = {c: c.lower() for c in df.columns}
    df.rename(columns=cols_lower, inplace=True)

    required = ["open", "high", "low", "close", "volume"]
    for c in required:
        if c not in df.columns:
            raise SystemExit(f"CSV precisa conter coluna '{c}'")

    # Gera target
    if args.target and args.target in df.columns:
        y = df[args.target].astype(int)
    else:
        y = make_target_from_horizon(df, horizon=args.horizon, threshold=args.threshold)

    # Features
    feats_df = build_features(df)
    X_cols = select_feature_cols(feats_df)
    X = feats_df[X_cols].replace([np.inf, -np.inf], np.nan)

    # Limpeza
    mask = X.notna().all(axis=1) & y.notna()
    X = X[mask]
    y = y[mask]

    if len(X) < 200:
        raise SystemExit("Poucos dados após limpeza (precisa >= 200 linhas)")

    # Split temporal (holdout): primeiro bloco = treino, último bloco = teste
    n = len(X)
    cut = int(n * (1 - args.test_size))
    Xtr, Xte = X.iloc[:cut], X.iloc[cut:]
    ytr, yte = y.iloc[:cut], y.iloc[cut:]

    artifacts = {"features": X_cols}

    if args.model in ("rf", "both"):
        rf = RandomForestClassifier(
            n_estimators=300,
            max_depth=None,
            min_samples_leaf=20,
            random_state=42,
            n_jobs=-1,
        )
        rf.fit(Xtr, ytr)
        p = rf.predict(Xte)
        proba = rf.predict_proba(Xte)[:, 1]
        m = metrics_dict(yte, p, proba)
        artifacts["rf_metrics"] = m
        artifacts["rf_feature_importances"] = (
            dict(zip(X_cols, rf.feature_importances_))
        )
        dump({"model": rf, "features": X_cols}, out_dir / f"{args.save_name}_rf.joblib")

    if args.model in ("dt", "both"):
        dt = DecisionTreeClassifier(
            max_depth=args.max_depth, random_state=42, min_samples_leaf=20
        )
        dt.fit(Xtr, ytr)
        p = dt.predict(Xte)
        proba = dt.predict_proba(Xte)[:, 1] if hasattr(dt, "predict_proba") else None
        m = metrics_dict(yte, p, proba)
        artifacts["dt_metrics"] = m
        rules = export_text(dt, feature_names=list(X_cols))
        artifacts["dt_rules_text"] = rules
        pine = export_pine_from_rules(rules)
        artifacts["dt_pine"] = pine
        dump({"model": dt, "features": X_cols}, out_dir / f"{args.save_name}_dt.joblib")

    # Salva artefatos JSON e Pine (se houver)
    with open(out_dir / f"{args.save_name}_artifacts.json", "w", encoding="utf-8") as f:
        json.dump(artifacts, f, ensure_ascii=False, indent=2)

    if artifacts.get("dt_pine"):
        with open(out_dir / f"{args.save_name}_dt.pine", "w", encoding="utf-8") as f:
            f.write(artifacts["dt_pine"]) 

    # Mostra resumo
    print("Resumo do treino:")
    print(json.dumps({k: v for k, v in artifacts.items() if k.endswith("_metrics")}, indent=2))
    if artifacts.get("dt_rules_text"):
        print("\nRegras (DecisionTree):\n")
        print(artifacts["dt_rules_text"])
        print("\nPineScript gerado (base):\n")
        print(artifacts["dt_pine"])  # noqa: T201


if __name__ == "__main__":
    main()
