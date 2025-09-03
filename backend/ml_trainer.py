from __future__ import annotations
import os
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from pymongo import MongoClient
import certifi

import ml_utils

DATA_REQ = ["open","high","low","close","volume"]
STATE_DIR = Path(__file__).parent / "ml_models"
STATE_DIR.mkdir(exist_ok=True)
LAST_RUN_FILE = STATE_DIR / "weekly_last_run.json"


def load_data_from_mongo(symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "test_database")
    if not mongo_url:
        return None
    try:
        if mongo_url.startswith("mongodb+srv://"):
            client = MongoClient(mongo_url, tls=True, tlsCAFile=certifi.where())
        else:
            client = MongoClient(mongo_url)
        db = client[db_name]
        cur = db.candles.find({"symbol": symbol, "timeframe": timeframe}).sort("time", 1)
        recs = list(cur)
        if not recs:
            return None
        df = pd.DataFrame(recs)
        return df
    except Exception:
        return None


def load_data_with_fallback(symbol: str, timeframe: str) -> pd.DataFrame:
    df = load_data_from_mongo(symbol, timeframe)
    if df is None or df.empty:
        csv_path = Path("/data/ml/ohlcv.csv")
        if csv_path.exists():
            df = pd.read_csv(csv_path)
        else:
            raise RuntimeError("Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")
    # normalize columns
    df = df.rename(columns={c: c.lower() for c in df.columns})
    for c in DATA_REQ:
        if c not in df.columns:
            raise RuntimeError(f"CSV/DB sem coluna obrigatória: {c}")
    return df[DATA_REQ].copy()


def run_once():
    symbol = os.environ.get("TRAIN_SYMBOL", "R_100")
    timeframe = os.environ.get("TRAIN_TIMEFRAME", "3m")
    horizon = int(os.environ.get("TRAIN_HORIZON", "3"))
    threshold = float(os.environ.get("TRAIN_THRESHOLD", "0.003"))
    model_type = os.environ.get("TRAIN_MODEL_TYPE", "rf")

    df = load_data_with_fallback(symbol, timeframe)
    out = ml_utils.train_and_maybe_promote(df, horizon=horizon, threshold=threshold, model_type=model_type, save_prefix=f"{symbol}_{timeframe}")
    print(json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "result": out}, indent=2))
    return out


def load_last_run_week() -> Optional[int]:
    if LAST_RUN_FILE.exists():
        try:
            data = json.loads(LAST_RUN_FILE.read_text())
            return int(data.get("iso_week"))
        except Exception:
            return None
    return None


def save_last_run_week(week_num: int):
    LAST_RUN_FILE.write_text(json.dumps({"iso_week": week_num, "ts": datetime.utcnow().isoformat() + "Z"}, indent=2))


def loop_weekly():
    while True:
        try:
            now = datetime.utcnow()
            iso_week = int(now.strftime("%V"))
            last_week = load_last_run_week()
            if last_week != iso_week:
                # Run training
                out = run_once()
                save_last_run_week(iso_week)
                # Simple log about promotion
                prom = out.get("promoted")
                print(f"[trainer] weekly run completed. promoted={prom}")
            else:
                pass
        except Exception as e:
            print(f"[trainer] error: {e}")
        # Sleep 60s between checks to pick up data soon after seeding and then train weekly
        time.sleep(60)


if __name__ == "__main__":
    if os.environ.get("TRAIN_RUN_ONCE", "0") == "1":
        run_once()
    else:
        loop_weekly()