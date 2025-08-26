from fastAPI import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime, date
import asyncio
import json
import time

# 3rd party realtime
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

# Light ML utils (no heavy TA libs here)
import pandas as pd
from pathlib import Path as _Path
import ml_utils
from joblib import load as joblib_load
from sklearn.tree import DecisionTreeClassifier, export_text

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection (MUST use env)
mongo_url = os.environ.get('MONGO_URL')
if mongo_url:
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ.get('DB_NAME', 'test_database')]
else:
    client = None
    db = None
    logging.warning("MONGO_URL not set; database features disabled. Set backend/.env with MONGO_URL or inject env.")

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# -------------------------------------------------------------
# Deriv Integration (Demo-ready): WS ticks + proposal/buy + tracking
# -------------------------------------------------------------
DERIV_APP_ID = os.environ.get("DERIV_APP_ID")
DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN")
DERIV_WS_URL = os.environ.get("DERIV_WS_URL", "wss://ws.derivws.com/websockets/v3")

SUPPORTED_SYMBOLS = [
    "CRYETHUSD",  # Crypto ETH/USD
    "FRXUSDJPY",  # Forex USD/JPY
    "US30",       # Wall St 30
    # Volatility indices examples
    "1HZ10V",     # Volatility 10 (1s)
    "R_10",       # Volatility 10 Index
    "R_15",       # Volatility 15 Index
    "R_25",
    "R_50",
    "R_75",
    "R_100",
]

# ... rest of existing server.py content unchanged up to ML router ...

# --------------- ML endpoints (status + manual train) -----------------
ml_router = APIRouter(prefix="/api/ml")

@ml_router.get("/status")
async def ml_status():
    meta = ml_utils.load_champion()
    return meta or {"message": "no champion"}

@ml_router.post("/train")
async def ml_train(source: str = "mongo", symbol: str = "R_100", timeframe: str = "3m", horizon: int = 3, threshold: float = 0.003, model_type: str = "rf"):
    df: Optional[pd.DataFrame] = None
    if source == "mongo" and db is not None:
        try:
            recs = await db.candles.find({"symbol": symbol, "timeframe": timeframe}).sort("time", 1).to_list(20000)
            if recs:
                df = pd.DataFrame(recs)
        except Exception as e:
            logging.getLogger(__name__).warning(f"ML load mongo failed: {e}")
    if df is None or df.empty:
        path = _Path("/data/ml/ohlcv.csv")
        if path.exists():
            df = pd.read_csv(path)
        else:
            raise HTTPException(status_code=400, detail="Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")
    ren = {c: c.lower() for c in df.columns}
    df = df.rename(columns=ren)
    for c in ["open","high","low","close","volume"]:
        if c not in df.columns:
            raise HTTPException(status_code=400, detail=f"CSV/DB sem coluna obrigatória: {c}")
    try:
        out = ml_utils.train_and_maybe_promote(df[["open","high","low","close","volume"]].copy(), horizon=horizon, threshold=threshold, model_type=model_type, save_prefix=f"{symbol}_{timeframe}")
        return out
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@ml_router.get("/model/{model_id}/rules")
async def ml_model_rules(model_id: str):
    try:
        model_path = (Path(__file__).parent / "ml_models" / f"{model_id}.joblib")
        if not model_path.exists():
            raise HTTPException(status_code=404, detail="Modelo não encontrado")
        payload = joblib_load(model_path)
        model = payload.get("model")
        features = payload.get("features", [])
        if isinstance(model, DecisionTreeClassifier):
            try:
                tree_text = export_text(model, feature_names=list(features) if features else None)
            except Exception:
                tree_text = export_text(model)
            pine = f"""//@version=5
indicator("DT Rules {model_id}", overlay=false)
// As regras abaixo são exportadas do sklearn DecisionTree.
// Converta manualmente cada condição em lógica Pine usando as séries equivalentes.
// Features usadas: {', '.join(features) if features else '-'}
/*
{tree_text}
*/
longSignal = false
shortSignal = false
plot(longSignal ? 1 : shortSignal ? -1 : 0, style=plot.style_columns, color=longSignal?color.lime: shortSignal?color.red:color.gray)
"""
            return {"model_id": model_id, "type": "dt", "features": features, "rules_text": tree_text, "pine_script": pine}
        else:
            return {"model_id": model_id, "type": "other", "message": "Regras disponíveis apenas para DecisionTree."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Include the routers in the main app
app.include_router(api_router)
app.include_router(ml_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)