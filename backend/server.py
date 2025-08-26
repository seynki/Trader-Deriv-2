<file>
<absolute_file_name>/app/backend/server.py</absolute_file_name>
<content_replacement>
<old_content_marker># Include the router in the main app</old_content_marker>
<new_content>from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Set
import uuid
from datetime import datetime, date
import asyncio
import json
import time

# 3rd party realtime
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

# ML optional: treinamos via script externo em scripts/train_ml_rules.py para evitar dependências pesadas no backend.
import pandas as pd
import numpy as np
import ml_utils  # local helper for ML weekly training without heavy deps

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

class BuyRequest(BaseModel):
    # General
    symbol: str
    type: Optional[str] = "CALLPUT"  # CALLPUT | ACCUMULATOR | TURBOS | MULTIPLIERS
    contract_type: Optional[str] = None  # e.g., CALL/PUT, TURBOSLONG/TURBOSSHORT, MULTUP/MULTDOWN, ACCU
    duration: Optional[int] = 5
    duration_unit: Optional[str] = "t"  # ticks by default
    stake: float = 1.0
    currency: str = "USD"
    max_price: Optional[float] = None
    barrier: Optional[str] = None
    # Specific extras
    multiplier: Optional[int] = None
    strike: Optional[str] = None  # e.g., "ATM"
    limit_order: Optional[Dict[str, Any]] = None  # {take_profit, stop_loss}
    product_type: Optional[str] = None
    growth_rate: Optional[float] = None  # for ACCUMULATOR (e.g., 0.03)
    extra: Optional[Dict[str, Any]] = None  # passthrough

class SellRequest(BaseModel):
    contract_id: int
    price: Optional[float] = None  # 0 = market

class DerivStatus(BaseModel):
    connected: bool
    authenticated: bool
    environment: str = "DEMO"
    symbols: List[str] = Field(default_factory=list)
    last_heartbeat: Optional[int] = None

class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("deriv")

class DerivWS:
    """Minimal Deriv WS manager with auto reconnect, dispatcher, tick and contract broadcasting."""
    def __init__(self, app_id: Optional[str], token: Optional[str], ws_url: str):
        self.app_id = app_id
        self.token = token
        self.ws_url = ws_url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.authenticated = False
        self.loop_task: Optional[asyncio.Task] = None
        self.subscribed_symbols: Dict[str, bool] = {}
        # per-symbol subscribers: symbol -> list of asyncio.Queue
        self.queues: Dict[str, List[asyncio.Queue]] = {}
        # contract tracking: contract_id -> list of queues
        self.contract_queues: Dict[int, List[asyncio.Queue]] = {}
        self.contract_subscribed: Dict[int, bool] = {}
        self.last_heartbeat: Optional[int] = None
        self._lock = asyncio.Lock()
        # pending req_id -> Future
        self.pending: Dict[str, asyncio.Future] = {}
        # store last authorize details (for landing_company/currency defaults)
        self.last_authorize: Dict[str, Any] = {}
        self.landing_company_name: Optional[str] = None
        self.currency: Optional[str] = None

        self.no_stats_contracts: Set[int] = set()
        self.stats_recorded: Set[int] = set()

    def _build_uri(self) -> str:
        if not self.app_id:
            # Deriv recommends using an app_id; we still allow anon for read-only
            return f"{self.ws_url}"
        return f"{self.ws_url}?app_id={self.app_id}"

    async def start(self):
        if self.loop_task and not self.loop_task.done():
            return
        self.loop_task = asyncio.create_task(self._run())

    async def stop(self):
        if self.loop_task:
            self.loop_task.cancel()
        if self.ws:
            await self.ws.close()
        self.connected = False
        self.authenticated = False

    @retry(stop=stop_after_attempt(8), wait=wait_exponential_jitter(initial=2, max=20))
    async def _connect(self):
        uri = self._build_uri()
        logger.info(f"Connecting to Deriv WS: {uri}")
        self.ws = await websockets.connect(uri, ping_interval=20, ping_timeout=10)
        self.connected = True
        logger.info("Connected to Deriv WS")
        if self.token:
            await self._send({"authorize": self.token})

    async def _send(self, payload: Dict[str, Any]):
        if not self.ws:
            return
        await self.ws.send(json.dumps(payload))

    async def _run(self):
        while True:
            try:
                await self._connect()
                async for raw in self.ws:
                    data = json.loads(raw)
                    msg_type = data.get("msg_type")
                    req_id = data.get("req_id")
                    if req_id is not None and req_id in self.pending:
                        fut = self.pending.pop(req_id)
                        if not fut.done():
                            fut.set_result(data)
                            continue
                    if msg_type == "authorize":
                        self.authenticated = data.get("error") is None
                        if self.authenticated:
                            auth = data.get("authorize", {})
                            self.last_authorize = auth
                            self.landing_company_name = auth.get("landing_company_name") or auth.get("landing_company_fullname")
                            self.currency = auth.get("currency")
                        logger.info(f"Authorize status: {self.authenticated}")
                    elif msg_type == "tick":
                        tick = data.get("tick", {})
                        symbol = tick.get("symbol")
                        if symbol and symbol in self.queues:
                            message = {
                                "type": "tick",
                                "symbol": symbol,
                                "price": tick.get("quote"),
                                "timestamp": tick.get("epoch"),
                                "ask": tick.get("ask"),
                                "bid": tick.get("bid"),
                            }
                            for q in list(self.queues.get(symbol, [])):
                                if not q.full():
                                    q.put_nowait(message)
                    elif msg_type == "proposal_open_contract":
                        poc = data.get("proposal_open_contract", {})
                        cid = poc.get("contract_id")
                        try:
                            cid_int = int(cid) if cid is not None else None
                        except Exception:
                            cid_int = None
                        if cid_int is not None:
                            message = {
                                "type": "contract",
                                "contract_id": cid_int,
                                "underlying": poc.get("underlying"),
                                "tick_count": poc.get("current_spot_time"),
                                "entry_spot": poc.get("entry_spot"),
                                "current_spot": poc.get("current_spot"),
                                "buy_price": poc.get("buy_price"),
                                "bid_price": poc.get("bid_price"),
                                "profit": poc.get("profit"),
                                "payout": poc.get("payout"),
                                "status": poc.get("status"),
                                "is_expired": poc.get("is_expired"),
                                "date_start": poc.get("date_start"),
                                "date_expiry": poc.get("date_expiry"),
                            }
                            # Record final result into global stats when contract ends (once per contract)
                            try:
                                if message.get("is_expired") and cid_int not in self.stats_recorded:
                                    if cid_int not in self.no_stats_contracts:
                                        pr = float(message.get("profit") or 0.0)
                                        await _global_stats.update(pr)
                                    self.stats_recorded.add(cid_int)
                            except Exception as _e:
                                logger.warning(f"Stats update failed for contract {cid_int}: {_e}")
                            # Broadcast to any listeners if present
                            for q in list(self.contract_queues.get(cid_int, [])):
                                if not q.full():
                                    q.put_nowait(message)
                    elif msg_type == "heartbeat":
                        self.last_heartbeat = int(time.time())
                    elif msg_type == "error":
                        logger.warning(f"Deriv error: {data}")
            except Exception as e:
                logger.warning(f"WS loop error, will reconnect: {e}")
                self.connected = False
                self.authenticated = False
                await asyncio.sleep(2)
            finally:
                try:
                    if self.ws:
                        await self.ws.close()
                except Exception:
                    pass

    async def ensure_subscribed(self, symbol: str):
        if symbol not in SUPPORTED_SYMBOLS:
            # Allow dynamic, but log
            logger.info(f"Subscribing non-whitelisted symbol: {symbol}")
        async with self._lock:
            if not self.subscribed_symbols.get(symbol):
                await self._send({"ticks": symbol, "subscribe": 1})
                self.subscribed_symbols[symbol] = True

    async def add_queue(self, symbol: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.queues.setdefault(symbol, []).append(q)
        await self.ensure_subscribed(symbol)
        return q

    def remove_queue(self, symbol: str, q: asyncio.Queue):
        if symbol in self.queues:
            try:
                self.queues[symbol].remove(q)
            except ValueError:
                pass

    async def ensure_contract_subscription(self, contract_id: int):
        async with self._lock:
            if not self.contract_subscribed.get(contract_id):
                await self._send({
                    "proposal_open_contract": 1,
                    "contract_id": contract_id,
                    "subscribe": 1,
                })
                self.contract_subscribed[contract_id] = True

    async def add_contract_queue(self, contract_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.contract_queues.setdefault(contract_id, []).append(q)
        await self.ensure_contract_subscription(contract_id)
        return q

    def remove_contract_queue(self, contract_id: int, q: asyncio.Queue):
        if contract_id in self.contract_queues:
            try:
                self.contract_queues[contract_id].remove(q)
            except ValueError:
                pass

# Single global instance
_deriv = DerivWS(DERIV_APP_ID, DERIV_API_TOKEN, DERIV_WS_URL)

@app.on_event("startup")
async def _startup():
    await _deriv.start()

@app.on_event("shutdown")
async def shutdown_db_client():
    if client:
        client.close()
    await _deriv.stop()

# ------------------- Public API -----------------------------
@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    if db:
        _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    if not db:
        return []
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

# Deriv helper endpoints
@api_router.get("/deriv/status", response_model=DerivStatus)
async def deriv_status():
    return DerivStatus(
        connected=_deriv.connected,
        authenticated=_deriv.authenticated,
        symbols=list(_deriv.subscribed_symbols.keys()),
        last_heartbeat=_deriv.last_heartbeat,
    )

# --------------- ML endpoints (status + manual train) -----------------
ml_router = APIRouter(prefix="/api/ml")

@ml_router.get("/status")
async def ml_status():
    meta = ml_utils.load_champion()
    return meta or {"message": "no champion"}

@ml_router.post("/train")
async def ml_train(source: str = "mongo", symbol: str = "R_100", timeframe: str = "3m", horizon: int = 3, threshold: float = 0.003, model_type: str = "rf"):
    # Load data from Mongo (preferred) or from file at /data/ml/ohlcv.csv
    df: Optional[pd.DataFrame] = None
    if source == "mongo" and db is not None:
        try:
            recs = await db.candles.find({"symbol": symbol, "timeframe": timeframe}).sort("time", 1).to_list(20000)
            if recs:
                df = pd.DataFrame(recs)
        except Exception as e:
            logger.warning(f"ML load mongo failed: {e}")
    if df is None or df.empty:
        path = Path("/data/ml/ohlcv.csv")
        if path.exists():
            df = pd.read_csv(path)
        else:
            raise HTTPException(status_code=400, detail="Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")
    # Normalize columns
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

</new_content>
</content_replacement>
</file>