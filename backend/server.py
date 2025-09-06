from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
import certifi
from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime, date
import asyncio
import json
import time

import ml_utils
import ml_trainer
import online_learning

# 3rd party realtime
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

import pandas as pd
import numpy as np
from fastapi import Query

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection (MUST use env)
mongo_url = os.environ.get('MONGO_URL')
client = None
db = None

if mongo_url:
    try:
        if mongo_url.startswith("mongodb+srv://"):
            # Fixed SSL configuration for MongoDB Atlas
            client = AsyncIOMotorClient(
                mongo_url, 
                tls=True, 
                tlsCAFile=certifi.where(),
                tlsAllowInvalidCertificates=False,
                tlsAllowInvalidHostnames=False,
                maxPoolSize=10,
                retryWrites=True,
                serverSelectionTimeoutMS=5000  # Timeout rápido para evitar travamento
            )
        else:
            client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
        
        # Não testar a conexão durante inicialização - apenas configurar
        db = client[os.environ.get('DB_NAME', 'test_database')]
        logging.info("MongoDB client configured (connection will be tested on demand)")
    except Exception as e:
        client = None
        db = None
        logging.warning(f"Mongo connection init failed: {e}")
else:
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

# ---------------------- Contracts schema ----------------------
class ContractCreate(BaseModel):
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: Optional[Any] = Field(default_factory=lambda: int(time.time()))  # accepts int/iso
    symbol: Optional[str] = None
    market: Optional[str] = None
    duration: Optional[int] = None
    duration_unit: Optional[str] = None
    stake: Optional[float] = None
    payout: Optional[float] = None
    barrier: Optional[str] = None
    barriers: Optional[List[str]] = None
    contract_type: Optional[str] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    result: Optional[str] = None  # win/lose/breakeven
    strategy_id: Optional[str] = None
    features: Optional[Dict[str, Any]] = None
    user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    currency: Optional[str] = None
    product_type: Optional[str] = None
    deriv_contract_id: Optional[int] = None
    status: Optional[str] = None  # open/closed

    def to_mongo(self) -> Dict[str, Any]:
        # normalize timestamp
        ts = self.timestamp
        ts_int: Optional[int] = None
        if isinstance(ts, (int, float)):
            ts_int = int(ts)
        else:
            try:
                # parse iso str / datetime
                if isinstance(ts, datetime):
                    ts_int = int(ts.timestamp())
                elif isinstance(ts, str):
                    ts_int = int(datetime.fromisoformat(ts).timestamp())
            except Exception:
                ts_int = int(time.time())
        doc = self.model_dump()
        doc["timestamp"] = ts_int or int(time.time())
        # ensure string id
        if not doc.get("id"):
            doc["id"] = str(uuid.uuid4())
        return doc

# Configure logging
# CORS
cors_origins = os.environ.get("CORS_ORIGINS", "*")
try:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("deriv")

# Global stats tracking
class GlobalStats:
    def __init__(self):
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.daily_pnl = 0.0
        self.stats_recorded = set()  # Track recorded contracts to avoid double counting
        self.no_stats_contracts = set()  # Contracts that should not update global stats

    def add_trade_result(self, deriv_contract_id: int, profit: float) -> bool:
        # Check if already recorded or should be ignored
        if deriv_contract_id in self.stats_recorded or deriv_contract_id in self.no_stats_contracts:
            return False
        
        self.total_trades += 1
        if profit > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.daily_pnl += profit
        self.stats_recorded.add(deriv_contract_id)
        return True

    def add_paper_trade_result(self, profit: float):
        """Add paper trade result to global stats"""
        self.total_trades += 1
        if profit > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.daily_pnl += profit

    def mark_no_stats(self, deriv_contract_id: int):
        """Mark a contract to not update global stats"""
        self.no_stats_contracts.add(deriv_contract_id)

    def get_win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.wins / self.total_trades) * 100

class GlobalPnL:
    def __init__(self):
        self.total = 0.0

    def add(self, amount: float):
        self.total += amount

_global_stats = GlobalStats()
_global_pnl = GlobalPnL()

# Online Learning Manager - initialize global instance
_online_manager = online_learning.OnlineLearningManager()

# Auto-initialize online learning models
async def ensure_online_models_active():
    """
    Ensure we have active online learning models for continuous improvement.
    Creates default models if none exist.
    """
    try:
        if not _online_manager.active_models:
            logger.info("🧠 Iniciando sistema de Online Learning automático...")
            
            # Try to load existing online models first
            models_dir = Path("/app/backend/ml_models")
            if models_dir.exists():
                for model_file in models_dir.glob("*_online.joblib"):
                    model_id = model_file.stem.replace("_online", "")
                    loaded_model = _online_manager.load_online_model(model_id)
                    if loaded_model:
                        logger.info(f"✅ Modelo online carregado: {model_id}")
            
            # If still no models, create a default one with sample data
            if not _online_manager.active_models:
                await create_default_online_model()
                
    except Exception as e:
        logger.error(f"Erro ao inicializar modelos online: {e}")

async def create_default_online_model():
    """Create a default online learning model with initial market data"""
    try:
        logger.info("📚 Criando modelo online padrão com dados de mercado...")
        
        # Fetch initial training data
        symbol = "R_100"
        granularity = 180  # 3m
        count = 500  # Initial training data
        
        df = await fetch_candles(symbol, granularity, count)
        
        if len(df) < 100:
            logger.warning("Poucos dados disponíveis para treinar modelo online")
            return
            
        # Build features
        features_df = ml_utils.build_features(df)
        features_df = ml_utils.add_feature_interactions(features_df, max_interactions=15)
        
        # Create simple target based on price movement
        features_df['future_return'] = features_df['close'].pct_change().shift(-1)
        features_df['target'] = (features_df['future_return'] > 0.001).astype(int)
        
        # Remove rows with NaN target
        features_df = features_df.dropna(subset=['target'])
        
        if len(features_df) < 50:
            logger.warning("Dados insuficientes após processamento para modelo online")
            return
            
        # Get feature columns (exclude target and price columns)
        exclude_cols = ['target', 'open', 'high', 'low', 'close', 'volume', 'future_return', 'timestamp']
        feature_cols = [col for col in features_df.columns if col not in exclude_cols and not col.endswith('_shift')]
        
        if len(feature_cols) < 10:
            logger.warning("Poucas features disponíveis para modelo online")
            return
        
        # Limit features to prevent overfitting
        feature_cols = feature_cols[:50]  # Max 50 features
        
        model_id = f"online_model_{symbol}_auto"
        
        # Create online model
        online_model = _online_manager.create_online_model(
            model_id=model_id,
            initial_data=features_df,
            features=feature_cols,
            target_col='target',
            model_type='sgd'  # SGD is best for online learning
        )
        
        logger.info(f"✅ Modelo online criado: {model_id} com {len(feature_cols)} features, {len(features_df)} amostras iniciais")
        return model_id
        
    except Exception as e:
        logger.error(f"Erro ao criar modelo online padrão: {e}")
        return None

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

                            # Update global stats when contract expires and persist to Mongo
                            if poc.get("is_expired"):
                                try:
                                    profit = float(poc.get("profit") or 0.0)
                                    accounted = _global_stats.add_trade_result(cid_int, profit)
                                    if accounted:
                                        try:
                                            _global_pnl.add(profit)
                                        except Exception:
                                            pass
                                        logger.info(f"Updated global stats: contract_id={cid_int}, profit={profit}, total_trades={_global_stats.total_trades}")
                                        
                                        # Online Learning: Adapt models with trade outcome
                                        try:
                                            await _adapt_online_models_with_trade(cid_int, profit, poc)
                                        except Exception as e:
                                            logger.warning(f"Online learning adaptation failed: {e}")
                                            
                                except Exception as e:
                                    logger.warning(f"Failed to update global stats: {e}")
                                # Persist contract closing to Mongo
                                try:
                                    if db is not None:
                                        res_str = "win" if (float(poc.get("profit") or 0.0) > 0) else ("breakeven" if float(poc.get("profit") or 0.0) == 0 else "lose")
                                        await db.contracts.update_one(
                                            {"deriv_contract_id": cid_int},
                                            {"$set": {
                                                "exit_price": float(poc.get("bid_price") or 0.0),
                                                "pnl": float(poc.get("profit") or 0.0),
                                                "result": res_str,
                                                "status": "closed",
                                                "updated_at": int(time.time()),
                                            }}
                                        )
                                except Exception as e:
                                    logger.warning(f"Mongo update (contract close) failed: {e}")

                            # Send to contract listeners
                            if cid_int in self.contract_queues:
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

async def _wait_deriv_ready(max_wait: float = 20.0, require_auth: bool = True) -> bool:
    """Wait briefly for the shared Deriv WS to be connected (and authorized if requested).
    Returns True if ready within max_wait seconds, False otherwise.
    """
    start = time.time()
    # Ensure loop started
    try:
        await _deriv.start()
    except Exception:
        pass
    while time.time() - start < max_wait:
        if _deriv.connected and (not require_auth or _deriv.authenticated):
            return True
        await asyncio.sleep(0.25)
    return False

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

# ------------------- Online Learning Helper Functions -----------------------------

async def _adapt_online_models_with_trade(contract_id: int, profit: float, poc_data: Dict[str, Any]):
    """
    Adapt online learning models with trade outcome data.
    This function updates active online models with the trade result for continuous learning.
    """
    try:
        # Ensure we have online models active - create if none exist
        if not hasattr(_online_manager, 'active_models') or not _online_manager.active_models:
            logger.info("🔄 Nenhum modelo online ativo, criando automaticamente...")
            await ensure_online_models_active()
            
        # If still no models after creation attempt, just log
        if not _online_manager.active_models:
            logger.warning(f"Não foi possível criar modelos online para trade #{contract_id}")
            return
        
        # Extract relevant features from the trade outcome
        trade_features = {
            'contract_id': contract_id,
            'profit': profit,
            'underlying': poc_data.get('underlying'),
            'entry_spot': poc_data.get('entry_spot'),
            'current_spot': poc_data.get('current_spot'),
            'buy_price': poc_data.get('buy_price'),
            'bid_price': poc_data.get('bid_price'),
            'payout': poc_data.get('payout'),
            'date_start': poc_data.get('date_start'),
            'date_expiry': poc_data.get('date_expiry'),
        }
        
        # Determine trade outcome (binary classification)
        trade_outcome = 1 if profit > 0 else 0
        
        # Update each active online model
        adaptation_count = 0
        for model_id, model in _online_manager.active_models.items():
            try:
                # Get current market data for adaptation
                symbol = poc_data.get('underlying', 'R_100')
                granularity = 180  # 3m default
                
                # Fetch recent candles for feature extraction
                try:
                    df = await fetch_candles(symbol, granularity, 50)
                    if len(df) >= 10:
                        # Build features using the same process as training
                        features_df = ml_utils.build_features(df)
                        features_df = ml_utils.add_feature_interactions(features_df, max_interactions=15)
                        
                        # Adapt the model with the latest market state and trade outcome
                        _online_manager.adapt_model(model_id, trade_features, features_df)
                        adaptation_count += 1
                        
                except Exception as feature_error:
                    logger.warning(f"Failed to get market data for adaptation: {feature_error}")
                    
            except Exception as model_error:
                logger.warning(f"Failed to update online model {model_id}: {model_error}")
                
        if adaptation_count > 0:
            outcome_text = "Lucro" if profit > 0 else "Perda"
            logger.info(f"🧠 Aprendizado Online: {adaptation_count} modelo(s) aprendeu(ram) com trade #{contract_id} ({outcome_text}: {profit:.2f})")
        else:
            logger.info(f"Modelo online aprendeu com trade #{contract_id}: {'Lucro' if profit > 0 else 'Perda'} = {profit:.2f}")
                
    except Exception as e:
        logger.error(f"Error in online learning adaptation: {e}")
        raise

# ------------------- Online Learning API -----------------------------

@api_router.post("/ml/online/create")
async def create_online_model(
    model_id: str,
    source: str = "deriv",
    symbol: str = "R_100",
    timeframe: str = "3m",
    count: int = 1000,
    horizon: int = 3,
    threshold: float = 0.003,
    model_type: str = "sgd"
):
    """Create an online learning model based on existing data"""
    try:
        # Get initial training data (reuse existing logic)
        if source == "deriv":
            granularity = {"1m": 60, "3m": 180, "5m": 300}.get(timeframe, 180)
            df = await fetch_candles(symbol, granularity, count)
        elif source == "file":
            df = await asyncio.to_thread(ml_trainer.load_data_with_fallback, symbol, timeframe)
        else:
            raise HTTPException(status_code=400, detail="Only 'deriv' and 'file' sources supported for online models")
        
        if len(df) < 500:
            raise HTTPException(status_code=400, detail="Dados insuficientes para criar modelo online")
        
        # Build features using existing feature engineering
        features_df = ml_utils.build_features(df)
        features_df = ml_utils.add_feature_interactions(features_df, max_interactions=15)
        
        # Get features and target
        features = ml_utils.select_features(features_df)
        features = ml_utils.remove_correlated_features(features_df, features, threshold=0.95)
        
        y = ml_utils.make_target(df, horizon=horizon, threshold=threshold)
        
        # Prepare data for online model
        X = features_df[features].replace([np.inf, -np.inf], np.nan)
        mask = X.notna().all(axis=1) & y.notna()
        X, y = X[mask], y[mask]
        
        if len(X) < 200:
            raise HTTPException(status_code=400, detail="Dados insuficientes após limpeza")
        
        # Create online model
        training_data = X.copy()
        training_data['target'] = y
        
        online_model = _online_manager.create_online_model(
            model_id=model_id,
            initial_data=training_data,
            features=features,
            target_col='target',
            model_type=model_type
        )
        
        return {
            "message": "Online model created successfully",
            "model_id": model_id,
            "features_count": len(features),
            "training_samples": len(X),
            "model_info": online_model.get_model_info()
        }
        
    except Exception as e:
        logger.error(f"Failed to create online model: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao criar modelo online: {e}")

@api_router.get("/ml/online/status/{model_id}")
async def get_online_model_status(model_id: str):
    """Get status and performance metrics of online model"""
    status = _online_manager.get_model_status(model_id)
    if status['status'] == 'not_found':
        raise HTTPException(status_code=404, detail="Modelo online não encontrado")
    
    return status

@api_router.get("/ml/online/list")
async def list_online_models():
    """List all active online learning models"""
    models = _online_manager.list_online_models()
    statuses = {}
    for model_id in models:
        statuses[model_id] = _online_manager.get_model_status(model_id)
    
    return {
        "models": models,
        "count": len(models),
        "statuses": statuses
    }

@api_router.get("/ml/online/progress")
async def get_online_learning_progress():
    """Get overall online learning progress and metrics"""
    models = _online_manager.list_online_models()
    
    overall_stats = {
        "active_models": len(models),
        "total_updates": 0,
        "models_detail": []
    }
    
    for model_id in models:
        status = _online_manager.get_model_status(model_id)
        if status['status'] == 'active':
            model_info = status.get('model_info', {})
            performance_history = status.get('performance_history', [])
            
            # Calculate improvement metrics
            improvement_trend = "stable"
            if len(performance_history) >= 2:
                recent_accuracy = performance_history[-1].get('accuracy', 0)
                older_accuracy = performance_history[0].get('accuracy', 0)
                if recent_accuracy > older_accuracy * 1.05:
                    improvement_trend = "improving"
                elif recent_accuracy < older_accuracy * 0.95:
                    improvement_trend = "declining"
            
            model_detail = {
                "model_id": model_id,
                "update_count": model_info.get('update_count', 0),
                "features_count": model_info.get('features_count', 0),
                "current_accuracy": performance_history[-1].get('accuracy', 0) if performance_history else 0,
                "current_precision": performance_history[-1].get('precision', 0) if performance_history else 0,
                "improvement_trend": improvement_trend,
                "performance_samples": len(performance_history)
            }
            
            overall_stats["models_detail"].append(model_detail)
            overall_stats["total_updates"] += model_info.get('update_count', 0)
    
    return overall_stats

@api_router.post("/ml/online/predict/{model_id}")
async def predict_with_online_model(
    model_id: str,
    symbol: str = "R_100",
    timeframe: str = "3m",
    count: int = 50
):
    """Make prediction with online model using current market data"""
    if model_id not in _online_manager.active_models:
        raise HTTPException(status_code=404, detail="Modelo online não encontrado")
    
    try:
        # Get recent market data
        granularity = {"1m": 60, "3m": 180, "5m": 300}.get(timeframe, 180)
        df = await fetch_candles(symbol, granularity, count)
        
        if len(df) < 10:
            raise HTTPException(status_code=400, detail="Dados insuficientes para previsão")
        
        # Build features
        features_df = ml_utils.build_features(df)
        features_df = ml_utils.add_feature_interactions(features_df, max_interactions=15)
        
        # Get prediction for latest data
        model = _online_manager.active_models[model_id]
        latest_data = features_df.iloc[-1:].copy()
        
        prediction = model.predict(latest_data)
        probability = model.predict_proba(latest_data)
        
        return {
            "model_id": model_id,
            "symbol": symbol,
            "prediction": int(prediction[0]),
            "probability": {
                "negative": float(probability[0][0]),
                "positive": float(probability[0][1])
            },
            "timestamp": int(time.time()),
            "model_info": model.get_model_info()
        }
        
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        raise HTTPException(status_code=500, detail=f"Erro na previsão: {e}")

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    if db is not None:
        _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    if db is None:
        return []
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

# Contracts API
@api_router.post("/contracts")
async def create_contract(contract: ContractCreate):
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB indisponível (configure MONGO_URL no backend/.env)")
    try:
        doc = contract.to_mongo()
        doc["created_at"] = int(time.time())
        await db.contracts.insert_one(doc)
        return {"id": doc["id"], "message": "saved", "deriv_contract_id": doc.get("deriv_contract_id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao salvar contrato: {e}")

# Deriv helper endpoints
@api_router.get("/deriv/status", response_model=DerivStatus)
async def deriv_status():
    return DerivStatus(
        connected=_deriv.connected,
        authenticated=_deriv.authenticated,
        symbols=list(_deriv.subscribed_symbols.keys()),
        last_heartbeat=_deriv.last_heartbeat,
    )

# cache for contracts_for
_contracts_cache: Dict[str, Dict[str, Any]] = {}
_CONTRACTS_TTL = 60  # seconds

def _parse_duration(s: Optional[str]):
    if not s or not isinstance(s, str):
        return None, None
    try:
        num = int(''.join(ch for ch in s if ch.isdigit()))
        unit = ''.join(ch for ch in s if ch.isalpha())
        return num, unit
    except Exception:
        return None, None

@api_router.get("/deriv/contracts_for/{symbol}")
async def deriv_contracts_for(symbol: str, currency: Optional[str] = None, product_type: Optional[str] = None, landing_company: Optional[str] = None):
    # TTL cache key includes product_type
    now = time.time()
    # Derive defaults from last authorize when not provided
    resolved_currency = currency or _deriv.currency or "USD"
    resolved_lc = (landing_company or _deriv.landing_company_name or "").lower() or "any"
    cache_key = f"{symbol}:{product_type or 'basic'}:{resolved_currency}:{resolved_lc}"
    cached = _contracts_cache.get(cache_key)
    if cached and now - cached.get("_ts", 0) < _CONTRACTS_TTL:
        return cached["data"]

    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")
    req_id = int(time.time() * 1000)
    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    payload = {
        "contracts_for": symbol,
        "currency": resolved_currency,
        "req_id": req_id,
    }
    # Send product_type if the caller specified it (accumulator, turbos, multipliers, basic)
    if product_type:
        payload["product_type"] = product_type
    # Include landing_company when available to get account-specific availability
    if resolved_lc != "any":
        payload["landing_company"] = resolved_lc
    await _deriv._send(payload)
    try:
        data = await asyncio.wait_for(fut, timeout=10)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for contracts_for")

    if data.get("error"):
        msg = data["error"].get("message", "contracts_for error")
        raise HTTPException(status_code=400, detail=msg)

    cf = data.get("contracts_for", {})
    if not cf:
        raise HTTPException(status_code=400, detail="There's no contract available for this symbol.")
    available = cf.get("available", [])
    types = set()
    durations: Dict[str, Dict[str, int]] = {}
    barrier_categories = set()

    for item in available:
        ctype = item.get("contract_type") or item.get("trade_type")
        if ctype:
            types.add(ctype)
        min_d, min_u = _parse_duration(item.get("min_duration"))
        max_d, max_u = _parse_duration(item.get("max_duration"))
        if min_u and min_d is not None:
            d = durations.setdefault(min_u, {"min": min_d, "max": min_d})
            d["min"] = min(d["min"], min_d)
        if max_u and max_d is not None:
            d = durations.setdefault(max_u, {"min": max_d, "max": max_d})
            d["max"] = max(d["max"], max_d)
        bc = item.get("barrier_category")
        if bc:
            barrier_categories.add(bc)

    result = {
        "symbol": symbol,
        "contract_types": sorted(types),
        "durations": durations,
        "duration_units": list(durations.keys()),
        "barriers": sorted(barrier_categories),
        "has_barrier": len(barrier_categories) > 0,
        "product_type": product_type or "basic",
        "currency": resolved_currency,
        "landing_company": None if resolved_lc == "any" else resolved_lc,
    }

    _contracts_cache[cache_key] = {"_ts": now, "data": result}
    return result


@api_router.get("/deriv/contracts_for_smart/{symbol}")
async def deriv_contracts_for_smart(symbol: str, currency: Optional[str] = None, product_type: Optional[str] = None, landing_company: Optional[str] = None):
    """Smart helper: checks symbol with correct product_type and falls back to 1HZ variant.
    Extra: quando product_type não é aceito pela Deriv (ou não retorna suporte), tenta novamente com 'basic' e valida se os tipos específicos existem.
    """
    base_symbol = symbol
    tried: List[str] = []
    results: Dict[str, Any] = {}

    desired = (product_type or "basic").lower()

    def types_match_for_product(res: Dict[str, Any]) -> bool:
        if not isinstance(res, dict):
            return False
        types = {str(t).upper() for t in (res.get("contract_types") or [])}
        if not types:
            return False
        if desired == "accumulator":
            return "ACCU" in types or "ACCUMULATOR" in types
        if desired == "turbos":
            return "TURBOSLONG" in types or "TURBOSSHORT" in types
        if desired == "multipliers":
            return "MULTUP" in types or "MULTDOWN" in types
        return True

    async def query_with_pt_fallback(sym: str):
        # 1) tentativa com product_type pedido
        primary: Any = None
        try:
            primary = await deriv_contracts_for(sym, currency=currency, product_type=product_type, landing_company=landing_company)
        except HTTPException as e:
            primary = {"error": e.detail}
        # 2) se não suportou, tenta com basic
        chosen = primary
        fallback: Any = None
        if (not types_match_for_product(primary)) and desired in {"accumulator", "turbos", "multipliers"}:
            try:
                fallback = await deriv_contracts_for(sym, currency=currency, product_type="basic", landing_company=landing_company)
                if types_match_for_product(fallback):
                    chosen = fallback
            except HTTPException as e2:
                fallback = {"error": e2.detail}
        return {"primary": primary, "fallback": fallback, "chosen": chosen}

    # Try provided symbol first
    res0 = await query_with_pt_fallback(base_symbol)
    tried.append(base_symbol)
    results[base_symbol] = res0["chosen"]
    first_supported = base_symbol if types_match_for_product(res0["chosen"]) else None

    # If not supported, try 1HZ alias
    if first_supported is None:
        alt = None
        if base_symbol.startswith("R_") and not base_symbol.endswith("_1HZ"):
            alt = f"{base_symbol}_1HZ"
        if alt:
            res1 = await query_with_pt_fallback(alt)
            tried.append(alt)
            results[alt] = res1["chosen"]
            if types_match_for_product(res1["chosen"]):
                first_supported = alt

    return {
        "tried": tried,
        "first_supported": first_supported,
        "results": results,
        "product_type": product_type or "basic",
    }

# ---------------- Proposal/Buy -----------------

@api_router.post("/deriv/proposal")
async def deriv_proposal(req: BuyRequest):
    """Get a pricing proposal for a contract. Supports CALLPUT, ACCUMULATOR, TURBOS, MULTIPLIERS.
    Note: For ACCUMULATOR/TURBOS/MULTIPLIERS we build a buy parameters payload, but here we only return an error
    if type isn't CALLPUT since proposal isn't applicable."""
    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")
    if (req.type or "CALLPUT").upper() != "CALLPUT":
        raise HTTPException(status_code=400, detail="proposal only applies to CALL/PUT vanilla options")

    req_id = int(time.time() * 1000)
    payload = {
        "proposal": 1,
        "amount": float(req.stake),
        "basis": "stake",
        "currency": req.currency,
        "symbol": req.symbol,
        "contract_type": (req.contract_type or "CALL").upper(),
        "duration": int(req.duration or 5),
        "duration_unit": req.duration_unit or "t",
        "req_id": req_id,
    }
    if req.barrier:
        payload["barrier"] = req.barrier

    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    await _deriv._send(payload)
    try:
        data = await asyncio.wait_for(fut, timeout=10)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for proposal")

    if data.get("error"):
        msg = data["error"].get("message", "proposal error")
        raise HTTPException(status_code=400, detail=msg)

    prop = data.get("proposal", {})
    if not prop:
        raise HTTPException(status_code=400, detail="Empty proposal response")

    return {
        "id": prop.get("id"),
        "ask_price": float(prop.get("ask_price", 0)),
        "payout": float(prop.get("payout", 0)),
        "spot": float(prop.get("spot", 0)),
        "display_value": prop.get("display_value"),
        "longcode": prop.get("longcode"),
    }

def build_proposal_payload(req: BuyRequest) -> Dict[str, Any]:
    """
    Builds buy/proposal payload supporting multiple product types: CALLPUT, ACCUMULATOR, TURBOS, MULTIPLIERS.
    """
    payload = {
        "currency": req.currency,
        "symbol": req.symbol,
    }

    req_type = (req.type or "CALLPUT").upper()

    if req_type == "CALLPUT":
        # CALL/PUT vanilla options
        payload.update({
            "proposal": 1,
            "amount": float(req.stake),
            "basis": "stake",
            "contract_type": (req.contract_type or "CALL").upper(),
            "duration": int(req.duration or 5),
            "duration_unit": req.duration_unit or "t",
        })
        if req.barrier:
            payload["barrier"] = req.barrier

    elif req_type == "ACCUMULATOR":
        # Accumulator contracts
        payload.update({
            "buy": 1,
            "price": req.max_price or req.stake,  # Use max_price as ceiling or fallback to stake
            "parameters": {
                "accumulator": 1,
                "growth_rate": req.growth_rate or 0.03,
            }
        })
        # Filter out stop_loss for ACCUMULATOR contracts
        if req.limit_order:
            cleaned_limit = {k: v for k, v in req.limit_order.items() if k != "stop_loss"}
            if cleaned_limit:
                payload["parameters"]["limit_order"] = cleaned_limit

    elif req_type == "TURBOS":
        # Turbo contracts (TURBOSLONG/TURBOSSHORT)
        contract_type = req.contract_type or "TURBOSLONG"
        payload.update({
            "buy": 1,
            "price": req.max_price or req.stake,
            "parameters": {
                contract_type.lower(): 1,
                "duration": int(req.duration or 5),
                "duration_unit": req.duration_unit or "t",
            }
        })
        if req.barrier:
            payload["parameters"]["barrier"] = req.barrier
        if req.limit_order:
            payload["parameters"]["limit_order"] = req.limit_order

    elif req_type == "MULTIPLIERS":
        # Multiplier contracts (MULTUP/MULTDOWN)
        contract_type = req.contract_type or "MULTUP"
        payload.update({
            "buy": 1,
            "price": req.max_price or req.stake,
            "parameters": {
                contract_type.lower(): req.multiplier or 10,
            }
        })
        if req.limit_order:
            payload["parameters"]["limit_order"] = req.limit_order

    else:
        raise ValueError(f"Unsupported contract type: {req_type}")

    return payload

@api_router.post("/deriv/buy")
async def deriv_buy(req: BuyRequest):
    """Buy a contract. Supports CALLPUT, ACCUMULATOR, TURBOS, MULTIPLIERS.
    For CALLPUT: gets proposal first then buys
    For others: uses buy with parameters directly"""

    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")

    req_type = (req.type or "CALLPUT").upper()
    req_id = int(time.time() * 1000)

    if req_type == "CALLPUT":
        # Traditional CALL/PUT flow: proposal then buy
        prop_payload = build_proposal_payload(req)
        prop_payload["req_id"] = req_id

        fut = asyncio.get_running_loop().create_future()
        _deriv.pending[req_id] = fut
        await _deriv._send(prop_payload)

        try:
            prop_data = await asyncio.wait_for(fut, timeout=10)
        except asyncio.TimeoutError:
            _deriv.pending.pop(req_id, None)
            raise HTTPException(status_code=504, detail="Timeout waiting for proposal")

        if prop_data.get("error"):
            msg = prop_data["error"].get("message", "proposal error")
            raise HTTPException(status_code=400, detail=msg)

        prop = prop_data.get("proposal", {})
        if not prop:
            raise HTTPException(status_code=400, detail="Empty proposal response")

        # Now buy using proposal ID
        buy_req_id = int(time.time() * 1000) + 1
        buy_payload = {
            "buy": prop["id"],
            "price": float(req.max_price or prop.get("ask_price", 0)),
            "req_id": buy_req_id,
        }
        buy_fut = asyncio.get_running_loop().create_future()
        _deriv.pending[buy_req_id] = buy_fut
        await _deriv._send(buy_payload)

        try:
            buy_data = await asyncio.wait_for(buy_fut, timeout=10)
        except asyncio.TimeoutError:
            _deriv.pending.pop(buy_req_id, None)
            raise HTTPException(status_code=504, detail="Timeout waiting for buy")

        if buy_data.get("error"):
            msg = buy_data["error"].get("message", "buy error")
            raise HTTPException(status_code=400, detail=msg)

        buy_response = buy_data.get("buy", {})

    else:
        # Direct buy for ACCUMULATOR, TURBOS, MULTIPLIERS
        buy_payload = build_proposal_payload(req)
        buy_payload["req_id"] = req_id

        fut = asyncio.get_running_loop().create_future()
        _deriv.pending[req_id] = fut
        await _deriv._send(buy_payload)

        try:
            buy_data = await asyncio.wait_for(fut, timeout=10)
        except asyncio.TimeoutError:
            _deriv.pending.pop(req_id, None)
            raise HTTPException(status_code=504, detail="Timeout waiting for buy")

        if buy_data.get("error"):
            msg = buy_data["error"].get("message", "buy error")
            raise HTTPException(status_code=400, detail=msg)

        buy_response = buy_data.get("buy", {})

    if not buy_response:
        raise HTTPException(status_code=400, detail="Empty buy response")

    # Extract contract info
    contract_id = int(buy_response.get("contract_id", 0))
    buy_price = float(buy_response.get("buy_price", 0))
    payout = float(buy_response.get("payout", 0))
    start_time = buy_response.get("start_time")
    expiry_time = buy_response.get("expiry_time")

    # Store contract if MongoDB available
    contract_doc = None
    if db is not None:
        try:
            contract_create = ContractCreate(
                symbol=req.symbol,
                market="deriv",
                duration=req.duration,
                duration_unit=req.duration_unit,
                stake=req.stake,
                payout=payout,
                contract_type=req.contract_type or ("CALL" if req_type == "CALLPUT" else req_type),
                entry_price=buy_price,
                currency=req.currency,
                product_type=req_type,
                deriv_contract_id=contract_id,
                status="open",
                features=req.extra or {},
            )
            contract_doc = contract_create.to_mongo()
            await db.contracts.insert_one(contract_doc)
        except Exception as e:
            logger.warning(f"Failed to store contract in MongoDB: {e}")

    return {
        "contract_id": contract_id,
        "buy_price": buy_price,
        "payout": payout,
        "start_time": start_time,
        "expiry_time": expiry_time,
        "longcode": buy_response.get("longcode"),
        "transaction_id": buy_response.get("transaction_id"),
        "balance_after": buy_response.get("balance_after"),
        "contract_doc_id": contract_doc.get("id") if contract_doc else None,
    }

@api_router.post("/deriv/sell")
async def deriv_sell(req: SellRequest):
    """Sell/close a contract before expiry"""
    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")

    req_id = int(time.time() * 1000)
    payload = {
        "sell": int(req.contract_id),
        "price": float(req.price or 0),  # 0 = market price
        "req_id": req_id,
    }

    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    await _deriv._send(payload)

    try:
        data = await asyncio.wait_for(fut, timeout=10)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for sell")

    if data.get("error"):
        msg = data["error"].get("message", "sell error")
        raise HTTPException(status_code=400, detail=msg)

    sell_response = data.get("sell", {})
    if not sell_response:
        raise HTTPException(status_code=400, detail="Empty sell response")

    return {
        "sold_for": float(sell_response.get("sold_for", 0)),
        "transaction_id": sell_response.get("transaction_id"),
        "balance_after": sell_response.get("balance_after"),
    }

# ------------------- WebSocket API Endpoints -------------------

@api_router.websocket("/ws/ticks")
async def websocket_ticks(websocket: WebSocket, symbols: str = "R_10,R_25"):
    """WebSocket endpoint for real-time tick data from multiple symbols"""
    await websocket.accept()
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    queues = []
    
    try:
        # Create queues for each symbol
        for symbol in symbol_list:
            queue = await _deriv.add_queue(symbol)
            queues.append((symbol, queue))
        
        # Send initial message
        await websocket.send_json({"symbols": symbol_list})
        
        # Relay messages
        while True:
            for symbol, queue in queues:
                try:
                    message = queue.get_nowait()
                    await websocket.send_json(message)
                except asyncio.QueueEmpty:
                    continue
            await asyncio.sleep(0.1)  # Small delay to prevent busy waiting
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Clean up queues
        for symbol, queue in queues:
            _deriv.remove_queue(symbol, queue)

@api_router.websocket("/ws/contract/{contract_id}")
async def websocket_contract(websocket: WebSocket, contract_id: int):
    """WebSocket endpoint for real-time contract updates"""
    await websocket.accept()
    queue = None
    
    try:
        queue = await _deriv.add_contract_queue(contract_id)
        
        # Send initial message
        await websocket.send_json({"contract_id": contract_id, "status": "subscribed"})
        
        # Relay contract updates
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
                await websocket.send_json(message)
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({"type": "heartbeat", "timestamp": int(time.time())})
                continue
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Contract WebSocket error: {e}")
    finally:
        # Clean up queue
        if queue:
            _deriv.remove_contract_queue(contract_id, queue)

# ------------------- Strategy API (paper trading simulation) -------------------

class StrategyConfig(BaseModel):
    symbol: str = "R_100"
    granularity: int = 60  # seconds
    candle_len: int = 200  # lookback window
    duration: int = 5
    duration_unit: str = "t"  # ticks
    stake: float = 1.0
    daily_loss_limit: float = -20.0
    # Technical indicators thresholds
    adx_trend: float = 22.0
    rsi_ob: float = 70.0
    rsi_os: float = 30.0
    bbands_k: float = 2.0
    mode: str = "paper"  # paper | live

class StrategyRunner:
    def __init__(self):
        self.running = False
        self.config: Optional[StrategyConfig] = None
        self.last_run_at: Optional[int] = None
        self.today_pnl = 0.0
        self.today_trades = 0
        self.task: Optional[asyncio.Task] = None

    async def start(self, config: StrategyConfig):
        if self.running:
            return False
        
        self.config = config
        self.running = True
        self.last_run_at = int(time.time())
        self.today_pnl = 0.0
        self.today_trades = 0
        
        # Start the strategy loop
        self.task = asyncio.create_task(self._strategy_loop())
        return True

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        return True

    async def _strategy_loop(self):
        try:
            while self.running:
                await self._run_strategy_once()
                await asyncio.sleep(10)  # Run every 10 seconds
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Strategy loop error: {e}")
            # DON'T stop running - let strategy continue after errors
            logger.info("Strategy loop continuing despite error...")
            await asyncio.sleep(30)  # Wait longer after error before retrying
            if self.running:  # If still running, restart the loop
                asyncio.create_task(self._strategy_loop())

    async def _run_strategy_once(self):
        if not self.config or not self.running:
            return

        try:
            # Check daily loss limit
            if self.today_pnl <= self.config.daily_loss_limit:
                logger.info(f"Daily loss limit reached: {self.today_pnl}")
                return

            # Get recent candles
            df = await fetch_candles(self.config.symbol, self.config.granularity, self.config.candle_len)
            if len(df) < 50:
                return

            # Calculate technical indicators
            close = df['close']
            high = df['high'] 
            low = df['low']

            # Simple moving averages
            sma_20 = close.rolling(20).mean()
            sma_50 = close.rolling(50).mean()

            # RSI
            rsi = _rsi(close.tolist(), 14)[-1] if len(close) >= 14 else 50

            # MACD
            ema_12 = close.ewm(span=12).mean()
            ema_26 = close.ewm(span=26).mean()
            macd_line = ema_12 - ema_26
            macd_signal = macd_line.ewm(span=9).mean()
            macd_hist = (macd_line - macd_signal).iloc[-1] if len(macd_line) > 0 else 0

            # Bollinger Bands
            bb_basis = close.rolling(20).mean()
            bb_std = close.rolling(20).std()
            bb_upper = bb_basis + (self.config.bbands_k * bb_std)
            bb_lower = bb_basis - (self.config.bbands_k * bb_std)

            # ADX (simplified)
            price_change = close.diff().abs()
            adx_approx = price_change.rolling(14).mean().iloc[-1] if len(price_change) >= 14 else 0

            # Current values
            current_price = close.iloc[-1]
            bb_position = (current_price - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1]) if bb_upper.iloc[-1] != bb_lower.iloc[-1] else 0.5

            # Strategy logic: Enter CALL if bullish conditions
            signal = None
            if (sma_20.iloc[-1] > sma_50.iloc[-1] and  # Short MA > Long MA
                rsi < self.config.rsi_ob and rsi > self.config.rsi_os and  # RSI not overbought/oversold
                macd_hist > 0 and  # MACD bullish
                bb_position < 0.8 and  # Not near upper band
                adx_approx > self.config.adx_trend):  # Trending market
                signal = "CALL"
            elif (sma_20.iloc[-1] < sma_50.iloc[-1] and  # Short MA < Long MA
                  rsi < self.config.rsi_ob and rsi > self.config.rsi_os and  # RSI not overbought/oversold
                  macd_hist < 0 and  # MACD bearish
                  bb_position > 0.2 and  # Not near lower band
                  adx_approx > self.config.adx_trend):  # Trending market
                signal = "PUT"

            if signal:
                await self._execute_trade(signal)

            self.last_run_at = int(time.time())

        except Exception as e:
            logger.error(f"Strategy execution error: {e}")
            # Don't stop the strategy, just log and continue

    async def _execute_trade(self, signal: str):
        if not self.config:
            return

        try:
            if self.config.mode == "paper":
                # Paper trading simulation
                payout = 0.95  # 95% payout simulation
                win_prob = 0.52  # Slightly positive expectation
                won = np.random.random() < win_prob
                profit = (self.config.stake * payout) if won else -self.config.stake
                
                self.today_pnl += profit
                self.today_trades += 1
                
                # Update global stats for paper trades
                _global_stats.add_paper_trade_result(profit)
                _global_pnl.add(profit)
                
                logger.info(f"Paper trade executed: {signal}, profit: {profit:.2f}, today_pnl: {self.today_pnl:.2f}")
                
            elif self.config.mode == "live":
                # Live trading - execute actual trade
                buy_req = BuyRequest(
                    symbol=self.config.symbol,
                    contract_type=signal,
                    duration=self.config.duration,
                    duration_unit=self.config.duration_unit,
                    stake=self.config.stake,
                    extra={"no_stats": True}  # Mark for no global stats (handled by strategy)
                )
                
                result = await deriv_buy(buy_req)
                logger.info(f"Live trade executed: {signal}, contract_id: {result.get('contract_id')}")
                
                # Mark this contract to not update global stats (strategy manages its own)
                if result.get('contract_id'):
                    _global_stats.mark_no_stats(result['contract_id'])

        except Exception as e:
            logger.error(f"Trade execution error: {e}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "config": self.config.dict() if self.config else None,
            "last_run_at": self.last_run_at,
            "today_pnl": self.today_pnl,
            "today_trades": self.today_trades,
            "total_trades": _global_stats.total_trades,
            "wins": _global_stats.wins,
            "losses": _global_stats.losses,
            "win_rate": _global_stats.get_win_rate(),
            "daily_pnl": _global_stats.daily_pnl,
            "global_daily_pnl": _global_pnl.total,
        }

# Global strategy runner instance
_strategy_runner = StrategyRunner()

@api_router.post("/strategy/start")
async def start_strategy(config: StrategyConfig):
    """Start the automated trading strategy"""
    success = await _strategy_runner.start(config)
    if success:
        return {"message": "Strategy started", "config": config.dict()}
    else:
        raise HTTPException(status_code=400, detail="Strategy already running")

@api_router.post("/strategy/stop")
async def stop_strategy():
    """Stop the automated trading strategy"""
    success = await _strategy_runner.stop()
    return {"message": "Strategy stopped", "success": success}

@api_router.get("/strategy/status")
async def get_strategy_status():
    """Get current strategy status and statistics"""
    return _strategy_runner.get_status()

# ------------------- ML API -------------------

# ML jobs storage (in-memory for demo)
_jobs: Dict[str, Dict[str, Any]] = {}

# Fetch candles helper
async def fetch_candles(symbol: str, granularity: int, count: int) -> pd.DataFrame:
    """Fetch candles from Deriv API"""
    if not _deriv.connected:
        raise RuntimeError("Deriv not connected")
    
    req_id = int(time.time() * 1000)
    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    
    await _deriv._send({
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": count,
        "end": "latest", 
        "start": 1,
        "style": "candles",
        "granularity": granularity,
        "req_id": req_id,
    })
    
    try:
        data = await asyncio.wait_for(fut, timeout=30)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise RuntimeError("Timeout fetching candles")
    
    if data.get("error"):
        raise RuntimeError(f"Deriv error: {data['error'].get('message', 'unknown')}")
    
    candles = data.get("candles") or []
    if not candles:
        raise RuntimeError("No candles received")
    
    records = []
    for candle in candles:
        records.append({
            "open": float(candle["open"]),
            "high": float(candle["high"]), 
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": int(candle.get("volume", 0)),
            "time": int(candle["epoch"])
        })
    
    return pd.DataFrame(records)

async def _fetch_deriv_data_for_ml(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    """Fetch data from Deriv for ML training"""
    granularity = {"1m": 60, "3m": 180, "5m": 300}.get(timeframe, 180)
    df = await fetch_candles(symbol, granularity, count)
    if len(df) < 100:
        raise RuntimeError("Dados insuficientes vindos da Deriv")
    return df

@api_router.get("/ml/status")
async def ml_status():
    """Get current ML model champion status"""
    champion = ml_utils.load_champion()
    if not champion:
        return {"message": "no champion"}
    return champion

@api_router.post("/ml/train")
async def ml_train(
    source: str = Query("mongo"),
    symbol: str = Query("R_100"),
    timeframe: str = Query("3m"),
    horizon: int = Query(3),
    threshold: float = Query(0.003),
    model_type: str = Query("rf"),
    class_weight: Optional[str] = Query(None),
    calibrate: Optional[str] = Query(None),
    objective: str = Query("f1"),
):
    """Train ML model synchronously (single combination)"""
    try:
        if source == "deriv":
            df = await _fetch_deriv_data_for_ml(symbol, timeframe, 3000)
        else:
            df = await asyncio.to_thread(ml_trainer.load_data_with_fallback, symbol, timeframe)  # type: ignore

        out = await asyncio.to_thread(
            ml_utils.train_and_maybe_promote,
            df,
            horizon,
            threshold, 
            model_type,
            f"{symbol}_{timeframe}",
            class_weight,
            calibrate,
            0.95,
            480.0,
            objective,
        )
        return {
            "model_id": out.get("model_id"),
            "metrics": out.get("metrics"),
            "backtest": out.get("backtest"),
            "grid": [],
            "rows": int(len(df)),
            "features_used": out.get("features_used", 0),
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

@api_router.post("/ml/train_async")
async def ml_train_async(
    source: str = Query("mongo"),
    symbol: str = Query("R_100"),
    timeframe: str = Query("3m"),
    horizon: str = Query("1,3,5"),
    threshold: str = Query("0.002,0.003,0.004,0.005"),
    model_type: str = Query("rf"),
    class_weight: Optional[str] = Query(None),
    calibrate: Optional[str] = Query(None),
    objective: str = Query("precision"),
    horizons: Optional[str] = Query(None),
    thresholds: Optional[str] = Query(None),
    count: Optional[int] = Query(20000),
):
    # Support both 'mongo', 'deriv' and 'file' sources for async training
    if source not in ["mongo", "deriv", "file"]:
        raise HTTPException(status_code=400, detail="Supported sources: mongo, deriv, file")
    job_id = f"ml-{int(time.time()*1000)}"
    _jobs[job_id] = {"status": "queued", "progress": {"done": 0, "total": 0}}

    async def runner():
        _jobs[job_id].update({"status": "running", "stage": "loading_data", "progress": {"done": 0, "total": 0}})
        try:
            if source == "deriv":
                df = await _fetch_deriv_data_for_ml(symbol, timeframe, count or 20000)
            elif source == "file":
                df = await asyncio.to_thread(ml_trainer.load_data_with_fallback, symbol, timeframe)  # type: ignore
            else:
                df = await asyncio.to_thread(ml_trainer.load_data_with_fallback, symbol, timeframe)  # type: ignore
            h_str = horizons or str(horizon)
            t_str = thresholds or str(threshold)
            horizons_list = [int(x.strip()) for x in h_str.split(",") if x.strip()]
            thresholds_list = [float(x.strip()) for x in t_str.split(",") if x.strip()]
            best = None
            grid = []
            step = 0
            total = max(1, len(horizons_list) * len(thresholds_list))
            _jobs[job_id]["stage"] = "training_grid"
            _jobs[job_id]["progress"] = {"done": 0, "total": total}
            for h in horizons_list:
                for th in thresholds_list:
                    step += 1
                    _jobs[job_id]["progress"] = {"done": step, "total": total}
                    out = await asyncio.to_thread(
                        ml_utils.train_and_maybe_promote,
                        df,
                        h,
                        th,
                        model_type,
                        f"{symbol}_{timeframe}",
                        class_weight,
                        calibrate,
                        0.95,
                        480.0,
                        objective,
                    )
                    grid.append({"horizon": h, "threshold": th, **out})
                    cand = {
                        "precision": float(out.get("metrics", {}).get("precision", 0.0) or 0.0),
                        "ev": float(out.get("backtest", {}).get("ev_per_trade", 0.0) or 0.0),
                        "tpd": float(out.get("metrics", {}).get("trades_per_day", 0.0) or 0.0),
                    }
                    if best is None or (cand["precision"], cand["ev"], cand["tpd"]) > (best["precision"], best["ev"], best["tpd"]):
                        best = {**cand, **out, "horizon": h, "threshold": th}
            # Wrap best as 'result' to match frontend contract and mark status as 'done'
            result = None
            if best is not None:
                result = {
                    "model_id": best.get("model_id"),
                    "metrics": best.get("metrics"),
                    "backtest": best.get("backtest"),
                    "promoted": best.get("promoted", False),
                    "grid": grid,
                    "rows": int(len(df)),
                    "horizon": best.get("horizon"),
                    "threshold": best.get("threshold"),
                }
            _jobs[job_id].update({
                "status": "done",
                "result": result,
                "best": best,
                "grid": grid,
                "rows": int(len(df)),
            })
        except Exception as e:
            _jobs[job_id].update({"status": "failed", "error": str(e)})

    asyncio.create_task(runner())
    return {"job_id": job_id, "status": "queued"}

@api_router.get("/ml/job/{job_id}")
async def ml_job_status(job_id: str):
    data = _jobs.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return data

@api_router.post("/candles/ingest")
async def ingest_candles(
    symbol: str = Query("R_100"),
    granularity: int = Query(60),
    count: int = Query(1000)
):
    """Fetch candles from Deriv and store in MongoDB + create CSV fallback"""
    try:
        # Fetch from Deriv
        if not _deriv.connected:
            raise HTTPException(status_code=503, detail="Deriv desconectado")
        
        req_id = int(time.time() * 1000)
        fut = asyncio.get_running_loop().create_future()
        _deriv.pending[req_id] = fut
        
        await _deriv._send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "start": 1,
            "style": "candles",
            "granularity": granularity,
            "req_id": req_id,
        })
        
        try:
            data = await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError:
            _deriv.pending.pop(req_id, None)
            raise HTTPException(status_code=504, detail="Timeout buscando dados da Deriv")
        
        if data.get("error"):
            raise HTTPException(status_code=400, detail=f"Erro da Deriv: {data['error'].get('message', 'unknown')}")
        
        candles = data.get("candles") or []
        if not candles:
            raise HTTPException(status_code=400, detail="Nenhum candle recebido da Deriv")
        
        # Map granularity to timeframe
        timeframe_map = {
            60: "1m",
            180: "3m", 
            300: "5m",
            900: "15m",
            1800: "30m",
            3600: "1h",
            14400: "4h",
            86400: "1d"
        }
        timeframe = timeframe_map.get(granularity, f"{granularity}s")
        
        # Store in MongoDB (if available)
        mongo_inserted = 0
        mongo_updated = 0
        mongo_error = None
        
        if db is not None:
            try:
                for candle in candles:
                    doc = {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "time": int(candle["epoch"]),
                        "open": float(candle["open"]),
                        "high": float(candle["high"]),
                        "low": float(candle["low"]),
                        "close": float(candle["close"]),
                        "volume": int(candle.get("volume", 0))
                    }
                    result = await db.candles.replace_one(
                        {"symbol": symbol, "timeframe": timeframe, "time": int(candle["epoch"])},
                        doc,
                        upsert=True
                    )
                    if result.upserted_id:
                        mongo_inserted += 1
                    elif result.modified_count > 0:
                        mongo_updated += 1
            except Exception as e:
                mongo_error = f"MongoDB falhou: {str(e)[:100]}..."
        
        # Create CSV fallback
        import os
        os.makedirs("/data/ml", exist_ok=True)
        
        records = []
        for candle in candles:
            records.append({
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": int(candle.get("volume", 0)),
                "time": int(candle["epoch"])
            })
        
        import pandas as pd
        df = pd.DataFrame(records)
        df.to_csv("/data/ml/ohlcv.csv", index=False)
        
        result = {
            "message": "Dados ingeridos com sucesso",
            "symbol": symbol,
            "timeframe": timeframe,
            "received": len(candles),
            "mongo_inserted": mongo_inserted,
            "mongo_updated": mongo_updated,
            "csv_created": len(records)
        }
        
        if mongo_error:
            result["mongo_error"] = mongo_error
            result["message"] = "Dados salvos em CSV (MongoDB indisponível)"
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro na ingestão: {str(e)}")

def _rsi(close: List[float], period: int = 14) -> List[float]:
    """Simple RSI calculation"""
    if len(close) < period + 1:
        return [50.0] * len(close)
    
    deltas = [close[i] - close[i-1] for i in range(1, len(close))]
    gains = [max(0, delta) for delta in deltas]
    losses = [max(0, -delta) for delta in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    rs_values = []
    for i in range(period, len(deltas)):
        if avg_loss == 0:
            rs_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            rs_values.append(rsi)
        
        # Update averages
        gain = gains[i]
        loss = losses[i]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    
    return [50.0] * (period + 1) + rs_values

# Mount the API router
app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)