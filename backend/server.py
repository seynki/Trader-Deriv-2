from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Body
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

import pandas as pd
import io
import river_online_model

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

# ------------------------ Global Stats ------------------------
class GlobalStats:
    """Contabiliza métricas globais (manual/auto/estratégia) e PnL diário.
    - Evita dupla contagem por contract_id
    - Reseta PnL automaticamente ao mudar o dia
    """
    def __init__(self):
        self.wins: int = 0
        self.losses: int = 0
        self.total_trades: int = 0
        self._day: date = date.today()
        self._daily_pnl: float = 0.0
        self._recorded_contracts: Dict[int, bool] = {}

    def _roll_day_if_needed(self):
        if date.today() != self._day:
            self._day = date.today()
            self._daily_pnl = 0.0

    def add_pnl(self, pnl: float):
        self._roll_day_if_needed()
        self._daily_pnl += float(pnl or 0.0)
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.total_trades += 1

    def add_contract_result(self, contract_id: Optional[int], profit: float):
        if contract_id is None:
            # Ainda assim computa, pois veio de fonte confiável
            self.add_pnl(profit)
            return
        if not self._recorded_contracts.get(contract_id):
            self._recorded_contracts[contract_id] = True
            self.add_pnl(profit)

    def snapshot(self) -> Dict[str, Any]:
        self._roll_day_if_needed()
        wr = (self.wins / self.total_trades * 100.0) if self.total_trades > 0 else 0.0
        return {
            "wins": self.wins,
            "losses": self.losses,
            "total_trades": self.total_trades,
            "win_rate": wr,
            "global_daily_pnl": round(self._daily_pnl, 6),
            "day": self._day.isoformat(),
        }

_global_stats = GlobalStats()

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
        # avoid double-learning per contract
        self._river_learned: Dict[int, bool] = {}
        # avoid double-counting stats per contract
        self.stats_recorded: Dict[int, bool] = {}

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
                        # Broadcast to WS subscribers
                        if cid_int is not None and cid_int in self.contract_queues:
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
                            for q in list(self.contract_queues.get(cid_int, [])):
                                if not q.full():
                                    q.put_nowait(message)
                        # Online learning (River) pós-trade: quando expira e ainda não aprendemos
                        try:
                            if cid_int is not None and bool(poc.get("is_expired")) and not self._river_learned.get(cid_int):
                                # Extrair label a partir do lucro
                                profit = float(poc.get("profit") or 0.0)
                                label = 1 if profit > 0 else 0
                                # Construir candle sintético a partir de spots do contrato
                                entry_spot = float(poc.get("entry_spot") or 0.0)
                                current_spot = float(poc.get("current_spot") or entry_spot)
                                # Usamos o último spot como "close" do candle final; volume desconhecido -> 0
                                o = entry_spot if entry_spot else current_spot
                                h = max(entry_spot, current_spot)
                                low_spot = min(entry_spot, current_spot)
                                c = current_spot
                                v = 0.0
                                ts = datetime.utcnow().isoformat()
                                # Atualizar River com (features no momento) + label via next_close
                                m = _get_river_model()
                                _ = m.predict_and_update(ts, o, h, low_spot, c, v, next_close=(c + 1e-12 if label == 1 else c - 1e-12))
                                m.save()
                                self._river_learned[cid_int] = True
                        except Exception as le:
                            logger.warning(f"River post-trade learn failed: {le}")
                        # Atualiza estatísticas globais quando contrato expira
                        try:
                            if cid_int is not None and bool(poc.get("is_expired")) and not self.stats_recorded.get(cid_int):
                                profit = float(poc.get("profit") or 0.0)
                                _global_stats.add_contract_result(cid_int, profit)
                                self.stats_recorded[cid_int] = True
                        except Exception as se:
                            logger.warning(f"Global stats add failed: {se}")
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

@api_router.get("/deriv/status", response_model=DerivStatus)
async def deriv_status():
    return DerivStatus(
        connected=_deriv.connected,
        authenticated=_deriv.authenticated,
        environment="DEMO",
        symbols=SUPPORTED_SYMBOLS,
        last_heartbeat=_deriv.last_heartbeat,
    )

@api_router.get("/deriv/contracts_for/{symbol}")
async def deriv_contracts_for(symbol: str, currency: Optional[str] = None, product_type: Optional[str] = None, landing_company: Optional[str] = None):
    """Wrapper para Deriv contracts_for: retorna apenas lista de contract_types.
    Aceita product_type opcional (basic/multipliers/turbos/accumulator). Muitas contas DEMO aceitam apenas 'basic'.
    """
    req_id = int(time.time() * 1000)
    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    payload = {
        "contracts_for": symbol,
        "req_id": req_id,
    }
    if product_type:
        payload["product_type"] = product_type
    if currency:
        payload["currency"] = currency
    await _deriv._send(payload)
    try:
        data = await asyncio.wait_for(fut, timeout=12)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for contracts_for")
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", "contracts_for error"))
    cfor = data.get("contracts_for", {})
    types: List[str] = []
    for item in (cfor.get("available") or []):
        for t in (item.get("contract_types") or []):
            n = (t.get("name") or t.get("value") or "").upper()
            if n:
                types.append(n)
    return {
        "symbol": symbol,
        "contract_types": sorted(list(set(types))),
        "product_type": product_type or "basic",
    }

@api_router.get("/deriv/contracts_for_smart/{symbol}")
async def deriv_contracts_for_smart(symbol: str, currency: Optional[str] = None, product_type: Optional[str] = None, landing_company: Optional[str] = None):
    """Smart helper: tenta com o product_type pedido; se rejeitado ou sem tipos esperados,
    faz fallback automático para 'basic' e/ou para o alias _1HZ do símbolo.
    Retorna estrutura com tried, first_supported e results.
    """
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
        # basic
        return True

    async def query(sym: str) -> Dict[str, Any]:
        try:
            return await deriv_contracts_for(sym, currency=currency, product_type=product_type, landing_company=landing_company)
        except HTTPException as e:
            return {"error": e.detail}

    async def query_basic(sym: str) -> Dict[str, Any]:
        try:
            return await deriv_contracts_for(sym, currency=currency, product_type="basic", landing_company=landing_company)
        except HTTPException as e:
            return {"error": e.detail}

    tried: List[str] = []
    results: Dict[str, Any] = {}

    # 1) símbolo solicitado
    res0 = await query(symbol)
    tried.append(symbol)
    results[symbol] = res0
    chosen_symbol: Optional[str] = symbol if types_match_for_product(res0) else None

    # 2) fallback: basic
    if chosen_symbol is None and desired in {"accumulator", "turbos", "multipliers"}:
        res_basic = await query_basic(symbol)
        results[symbol + "#basic"] = res_basic
        if types_match_for_product(res_basic):
            chosen_symbol = symbol

    # 3) fallback: _1HZ
    if chosen_symbol is None and symbol.startswith("R_") and not symbol.endswith("_1HZ"):
        alt = f"{symbol}_1HZ"
        res_alt = await query(alt)
        tried.append(alt)
        results[alt] = res_alt
        if types_match_for_product(res_alt):
            chosen_symbol = alt
        elif desired in {"accumulator", "turbos", "multipliers"}:
            res_alt_basic = await query_basic(alt)
            results[alt + "#basic"] = res_alt_basic
            if types_match_for_product(res_alt_basic):
                chosen_symbol = alt

    return {
        "tried": tried,
        "first_supported": chosen_symbol,
        "results": results,
        "product_type": product_type or "basic",
    }

@api_router.post("/deriv/proposal")
async def deriv_proposal(req: BuyRequest):
    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")
    req_id = int(time.time() * 1000)
    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    payload = {
        "proposal": 1,
        "amount": req.stake,
        "basis": "stake",
        "contract_type": req.contract_type or ("CALL" if (req.extra or {}).get("side") == "RISE" else "PUT"),
        "currency": req.currency,
        "duration": req.duration,
        "duration_unit": req.duration_unit or "t",
        "symbol": req.symbol,
        "req_id": req_id,
    }
    await _deriv._send(payload)
    try:
        data = await asyncio.wait_for(fut, timeout=10)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for proposal")
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", "proposal error"))
    p = data.get("proposal", {})
    return {
        "id": p.get("id"),
        "payout": p.get("payout"),
        "ask_price": p.get("ask_price"),
        "spot": p.get("spot"),
    }

@api_router.post("/deriv/buy")
async def deriv_buy(req: BuyRequest):
    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")
    # 1) proposal primeiro para obter id
    prop = await deriv_proposal(req)
    if not prop.get("id"):
        raise HTTPException(status_code=400, detail="No proposal id")
    # 2) buy
    req_id = int(time.time() * 1000)
    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    await _deriv._send({
        "buy": prop["id"],
        "price": req.stake,
        "req_id": req_id,
    })
    try:
        data = await asyncio.wait_for(fut, timeout=12)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for buy response")
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", "buy error"))
    b = data.get("buy", {})
    cid = b.get("contract_id")
    # Garante que começaremos a acompanhar o contrato para emitir sinais de expiração/profit
    try:
        if cid:
            await _deriv.ensure_contract_subscription(cid)
    except Exception:
        pass
    return {
        "message": "purchased",
        "contract_id": b.get("contract_id"),
        "buy_price": b.get("buy_price") or b.get("price"),
        "payout": b.get("payout"),
        "transaction_id": b.get("transaction_id"),
    }

@api_router.post("/deriv/sell")
async def deriv_sell(req: SellRequest):
    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")
    req_id = int(time.time() * 1000)
    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    await _deriv._send({
        "sell": req.contract_id,
        "price": req.price or 0,
        "req_id": req_id,
    })
    try:
        data = await asyncio.wait_for(fut, timeout=10)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for sell response")
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", "Sell error"))
    s = data.get("sell", {})
    return {"message": "sold", "contract_id": s.get("contract_id"), "sold_for": s.get("sold_for")}

# -------------------- Strategy Runner (Paper/Live) -----------------------

class StrategyParams(BaseModel):
    symbol: str = "R_100"
    granularity: int = 60
    candle_len: int = 200
    duration: int = 5
    duration_unit: str = "t"
    stake: float = 1.0
    daily_loss_limit: float = -20.0
    adx_trend: float = 22.0
    rsi_ob: float = 70.0
    rsi_os: float = 30.0
    bbands_k: float = 2.0
    fast_ma: int = 9
    slow_ma: int = 21
    macd_fast: int = 12
    macd_slow: int = 26
    macd_sig: int = 9
    river_threshold: float = 0.53  # Threshold mínimo para filtro River (prob_up >= para CALL, prob_up <= 1-threshold para PUT)
    mode: str = "paper"  # paper | live

class StrategyStatus(BaseModel):
    running: bool
    mode: str
    symbol: str
    in_position: bool
    daily_pnl: float
    day: str
    last_signal: Optional[str] = None
    last_reason: Optional[str] = None
    last_run_at: Optional[int] = None
    # Global counters (para UI)
    wins: int = 0
    losses: int = 0
    total_trades: int = 0
    win_rate: float = 0.0
    global_daily_pnl: float = 0.0

# ---- indicator helpers (python versions) ----

def _sma(arr: List[float], n: int, i: Optional[int] = None) -> Optional[float]:
    if i is None:
        i = len(arr)
    if i - n < 0:
        return None
    seg = arr[i - n:i]
    return sum(seg) / n if seg else None


def _ema_series(arr: List[float], period: int) -> List[Optional[float]]:
    if len(arr) == 0:
        return []
    k = 2 / (period + 1)
    out: List[Optional[float]] = [None] * len(arr)
    if len(arr) < period:
        return out
    ema = sum(arr[:period]) / period
    out[period - 1] = ema
    for i in range(period, len(arr)):
        ema = arr[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def _rsi(close: List[float], period: int = 14) -> List[Optional[float]]:
    n = len(close)
    out: List[Optional[float]] = [None] * n
    if n <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = close[i] - close[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_g = gains / period
    avg_l = losses / period
    rs = 100 if avg_l == 0 else (avg_g / avg_l)
    out[period] = 100 - (100 / (1 + rs))
    for i in range(period + 1, n):
        ch = close[i] - close[i - 1]
        avg_g = (avg_g * (period - 1) + max(ch, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-ch, 0)) / period
        rs = 100 if avg_l == 0 else (avg_g / avg_l)
        out[i] = 100 - (100 / (1 + rs))
    return out


def _ema_series_from_list(arr: List[Optional[float]], period: int) -> List[Optional[float]]:
    # helper for MACD when there are None values
    clean = [x if x is not None else 0.0 for x in arr]
    return _ema_series(clean, period)


def _macd(close: List[float], f: int, s: int, sig: int):
    emaF = _ema_series(close, f)
    emaS = _ema_series(close, s)
    line: List[Optional[float]] = []
    for i in range(len(close)):
        a = emaF[i] if i < len(emaF) else None
        b = emaS[i] if i < len(emaS) else None
        line.append((a - b) if (a is not None and b is not None) else None)
    # replace None with 0 for signal EMA input
    line_for_sig = [x if x is not None else 0.0 for x in line]
    sigSeries = _ema_series(line_for_sig, sig)
    hist: List[Optional[float]] = []
    for i in range(len(line)):
        s_v = sigSeries[i] if i < len(sigSeries) else None
        hist.append((line[i] - s_v) if (line[i] is not None and s_v is not None) else None)
    return {"line": line, "signal": sigSeries, "hist": hist}


def _bollinger(close: List[float], period: int = 20, k: float = 2.0):
    n = len(close)
    mid: List[Optional[float]] = [None] * n
    upper: List[Optional[float]] = [None] * n
    lower: List[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        seg = close[i - period + 1:i + 1]
        m = sum(seg) / period
        var = sum((x - m) ** 2 for x in seg) / period
        sd = var ** 0.5
        mid[i] = m
        upper[i] = m + k * sd
        lower[i] = m - k * sd
    return {"upper": upper, "mid": mid, "lower": lower}


def _true_range(h: float, low: float, pc: float) -> float:
    return max(h - low, abs(h - pc), abs(low - pc))


def _rma(arr: List[float], p: int) -> List[Optional[float]]:
    if len(arr) == 0:
        return []
    out: List[Optional[float]] = [None] * len(arr)
    if len(arr) < p:
        return out
    a = sum(arr[:p])
    prev = a
    out[p - 1] = a / p
    for i in range(p, len(arr)):
        prev = prev - prev / p + arr[i]
        out[i] = prev / p
    return out


def _adx(high: List[float], low: List[float], close: List[float], period: int = 14) -> List[Optional[float]]:
    if len(high) <= period:
        return [None] * len(high)
    tr: List[float] = []
    plusDM: List[float] = []
    minusDM: List[float] = []
    for i in range(1, len(high)):
        tr.append(_true_range(high[i], low[i], close[i - 1]))
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        plusDM.append(up if (up > dn and up > 0) else 0.0)
        minusDM.append(dn if (dn > up and dn > 0) else 0.0)
    trR = _rma(tr, period)
    plusR = _rma(plusDM, period)
    minusR = _rma(minusDM, period)
    plusDI: List[Optional[float]] = []
    minusDI: List[Optional[float]] = []
    for i in range(len(trR)):
        if trR[i] is not None and plusR[i] is not None:
            plusDI.append(100 * (plusR[i] / trR[i]))
        else:
            plusDI.append(None)
        if trR[i] is not None and minusR[i] is not None:
            minusDI.append(100 * (minusR[i] / trR[i]))
        else:
            minusDI.append(None)
    dx: List[Optional[float]] = []
    for i in range(len(plusDI)):
        p = plusDI[i]
        m = minusDI[i]
        if p is not None and m is not None and (p + m) != 0:
            dx.append(100 * (abs(p - m) / (p + m)))
        else:
            dx.append(None)
    # shift dx by 1 like JS code and compute rma
    dx_clean = [x for x in dx[1:] if x is not None]
    adxR = _rma(dx_clean, period)
    # pad to align with close length
    pad_len = len(close) - len(adxR)
    if pad_len < 0:
        pad_len = 0
    return [None] * pad_len + adxR

class StrategyRunner:
    def __init__(self):
        self.task: Optional[asyncio.Task] = None
        self.params: StrategyParams = StrategyParams()
        self.running: bool = False
        self.in_position: bool = False
        self.daily_pnl: float = 0.0
        self.mode: str = "paper"
        self.last_signal: Optional[str] = None
        self.last_reason: Optional[str] = None
        self.last_run_at: Optional[int] = None
        self.day: date = date.today()

    def _decide_signal(self, candles: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
        """
        LÓGICA HÍBRIDA: River Online Learning (condição principal) + Indicadores Técnicos (confirmação)
        1. River analisa o último candle e dá prob_up
        2. Se River sinalizar (prob_up >= threshold para CALL ou prob_up <= 1-threshold para PUT)
        3. Então verificamos se os indicadores técnicos confirmam o sinal
        4. Só executa trade se AMBOS concordarem
        """
        if len(candles) == 0:
            return None
        
        # === PASSO 1: CONSULTAR RIVER (CONDIÇÃO PRINCIPAL) ===
        last_candle = candles[-1]
        try:
            # Obter modelo River
            river_model = _get_river_model()
            
            # Preparar dados do último candle para River
            timestamp = last_candle.get("epoch") or datetime.utcnow().timestamp()
            if isinstance(timestamp, (int, float)):
                timestamp = datetime.fromtimestamp(float(timestamp)).isoformat()
                
            # Fazer predição River (sem atualizar modelo)
            river_info = river_model.predict_and_update(
                timestamp=timestamp,
                o=float(last_candle.get("open", 0)),
                h=float(last_candle.get("high", 0)),
                l=float(last_candle.get("low", 0)),
                c=float(last_candle.get("close", 0)),
                v=float(last_candle.get("volume", 0)),
                next_close=None  # Não atualizar, apenas predizer
            )
            
            prob_up = float(river_info.get("prob_up", 0.5))
            river_signal = None
            river_confidence = 0.0
            
            # Verificar se River dá sinal forte
            if prob_up >= self.params.river_threshold:
                river_signal = "RISE"
                river_confidence = prob_up
            elif prob_up <= (1.0 - self.params.river_threshold):
                river_signal = "FALL" 
                river_confidence = 1.0 - prob_up
                
            # Se River não dá sinal forte, não prosseguir
            if river_signal is None:
                return None
                
        except Exception as e:
            logger.warning(f"River prediction failed: {e}")
            return None
        
        # === PASSO 2: VERIFICAR INDICADORES TÉCNICOS (CONFIRMAÇÃO) ===
        close = [float(c.get("close")) for c in candles]
        high = [float(c.get("high")) for c in candles]
        low = [float(c.get("low")) for c in candles]

        adx_arr = _adx(high, low, close)
        last_adx = next((x for x in reversed(adx_arr) if x is not None), None)

        ma_fast = _sma(close, self.params.fast_ma)
        ma_slow = _sma(close, self.params.slow_ma)
        prev_fast = _sma(close[:-1], self.params.fast_ma)
        prev_slow = _sma(close[:-1], self.params.slow_ma)

        macd_res = _macd(close, self.params.macd_fast, self.params.macd_slow, self.params.macd_sig)
        last_macd = next((x for x in reversed(macd_res["line"]) if x is not None), None)
        last_sig = next((x for x in reversed(macd_res["signal"]) if x is not None), None)

        rsi_arr = _rsi(close)
        last_rsi = next((x for x in reversed(rsi_arr) if x is not None), None)
        bb = _bollinger(close, 20, self.params.bbands_k)
        last_price = close[-1]
        last_upper = next((x for x in reversed(bb["upper"]) if x is not None), None)
        last_lower = next((x for x in reversed(bb["lower"]) if x is not None), None)

        trending = (last_adx is not None) and (last_adx >= self.params.adx_trend)
        
        # === PASSO 3: CONFIRMAR COM INDICADORES TÉCNICOS ===
        indicators_confirm = False
        technical_reason = ""
        
        if river_signal == "RISE":
            # River quer CALL/RISE - verificar se indicadores confirmam alta
            if trending:
                bull_cross = (prev_fast is not None and prev_slow is not None and ma_fast is not None 
                             and ma_slow is not None and last_macd is not None and last_sig is not None 
                             and prev_fast < prev_slow and ma_fast > ma_slow and last_macd > last_sig)
                if bull_cross:
                    indicators_confirm = True
                    technical_reason = f"Trend↑ ADX {last_adx:.1f} + MA/MACD"
            else:
                # Em range, confirmar se toca banda inferior + RSI oversold
                touch_lower = (last_lower is not None and last_price <= last_lower)
                if touch_lower and (last_rsi is not None) and last_rsi <= self.params.rsi_os:
                    indicators_confirm = True
                    technical_reason = f"Range: BB↓ + RSI {int(last_rsi)} (reversão)"
                # Ou se há momentum de alta mesmo em range
                elif (last_macd is not None and last_sig is not None and last_macd > last_sig):
                    indicators_confirm = True
                    technical_reason = "Range: MACD↑ momentum"
                    
        elif river_signal == "FALL":
            # River quer PUT/FALL - verificar se indicadores confirmam baixa
            if trending:
                bear_cross = (prev_fast is not None and prev_slow is not None and ma_fast is not None 
                             and ma_slow is not None and last_macd is not None and last_sig is not None 
                             and prev_fast > prev_slow and ma_fast < ma_slow and last_macd < last_sig)
                if bear_cross:
                    indicators_confirm = True
                    technical_reason = f"Trend↓ ADX {last_adx:.1f} + MA/MACD"
            else:
                # Em range, confirmar se toca banda superior + RSI overbought
                touch_upper = (last_upper is not None and last_price >= last_upper)
                if touch_upper and (last_rsi is not None) and last_rsi >= self.params.rsi_ob:
                    indicators_confirm = True
                    technical_reason = f"Range: BB↑ + RSI {int(last_rsi)} (reversão)"
                # Ou se há momentum de baixa mesmo em range
                elif (last_macd is not None and last_sig is not None and last_macd < last_sig):
                    indicators_confirm = True
                    technical_reason = "Range: MACD↓ momentum"
        
        # === PASSO 4: DECISÃO FINAL (AMBOS DEVEM CONCORDAR) ===
        if indicators_confirm:
            final_reason = f"🤖 River {river_confidence:.3f} + {technical_reason}"
            return {"side": river_signal, "reason": final_reason}
        
        # Se chegou aqui, River sinalizou mas indicadores não confirmaram
        return None

    async def _get_candles(self, symbol: str, granularity: int, count: int) -> List[Dict[str, Any]]:
        if not _deriv.connected:
            raise HTTPException(status_code=503, detail="Deriv not connected")
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
            data = await asyncio.wait_for(fut, timeout=12)
        except asyncio.TimeoutError:
            _deriv.pending.pop(req_id, None)
            raise HTTPException(status_code=504, detail="Timeout waiting for candles")
        if data.get("error"):
            raise HTTPException(status_code=400, detail=data["error"].get("message", "history error"))
        return data.get("candles") or []

    async def _paper_trade(self, symbol: str, side: str, duration_ticks: int, stake: float) -> float:
        # entry = last tick
        await _deriv.ensure_subscribed(symbol)
        q = await _deriv.add_queue(symbol)
        entry_price: Optional[float] = None
        profit: float = 0.0
        try:
            # get first tick as entry
            try:
                first_msg = await asyncio.wait_for(q.get(), timeout=10)
                entry_price = float(first_msg.get("price")) if first_msg else None
            except asyncio.TimeoutError:
                return 0.0
            # collect next duration_ticks
            last_price = entry_price
            collected = 0
            t0 = time.time()
            while collected < duration_ticks and (time.time() - t0) < (duration_ticks * 5):
                try:
                    m = await asyncio.wait_for(q.get(), timeout=5)
                    if m and m.get("type") == "tick":
                        last_price = float(m.get("price"))
                        collected += 1
                except asyncio.TimeoutError:
                    pass
            # settle
            win = (last_price is not None and entry_price is not None and ((side == "RISE" and last_price > entry_price) or (side == "FALL" and last_price < entry_price)))
            # assume payout ratio 0.95 for paper
            profit = (stake * 0.95) if win else (-stake)
            return profit
        finally:
            _deriv.remove_queue(symbol, q)

    async def _live_trade(self, symbol: str, side: str, duration_ticks: int, stake: float) -> float:
        # Use existing /deriv/buy logic for CALL/PUT
        req = BuyRequest(
            type="CALLPUT",
            symbol=symbol,
            contract_type=("CALL" if side == "RISE" else "PUT"),
            duration=duration_ticks,
            duration_unit="t",
            stake=stake,
            currency="USD",
        )
        try:
            buy_res = await deriv_buy(req)
        except HTTPException as e:
            logger.warning(f"Live buy failed: {e.detail}")
            return 0.0
        cid = buy_res.get("contract_id")
        if not cid:
            return 0.0
        # wait on contract updates until is_expired and profit known
        q = await _deriv.add_contract_queue(int(cid))
        profit: float = 0.0
        try:
            t0 = time.time()
            while True:
                try:
                    mtxt = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    if time.time() - t0 > 120:
                        break
                    continue
                if isinstance(mtxt, dict) and mtxt.get("type") == "contract":
                    poc = mtxt
                    if poc.get("is_expired"):
                        try:
                            profit = float(poc.get("profit") or 0.0)
                        except Exception:
                            profit = 0.0
                        break
        finally:
            _deriv.remove_contract_queue(int(cid), q)
        return profit

    async def _loop(self):
        self.running = True
        self.day = date.today()
        self.daily_pnl = 0.0
        self.in_position = False
        cooldown_seconds = 5
        logger.info(f"Strategy loop started: {self.params}")
        while self.running:
            try:
                # reset daily on new day
                if date.today() != self.day:
                    self.day = date.today()
                    self.daily_pnl = 0.0
                if self.daily_pnl <= self.params.daily_loss_limit:
                    logger.info("Daily loss limit reached. Stopping strategy.")
                    self.running = False
                    break
                candles = await self._get_candles(self.params.symbol, self.params.granularity, self.params.candle_len)
                self.last_run_at = int(time.time())
                signal = self._decide_signal(candles)
                if not signal:
                    await asyncio.sleep(cooldown_seconds)
                    continue
                if self.in_position:
                    await asyncio.sleep(cooldown_seconds)
                    continue
                self.last_signal = signal.get("side")
                self.last_reason = signal.get("reason")
                side = signal.get("side")
                # trade
                self.in_position = True
                if self.params.mode == "paper":
                    pnl = await self._paper_trade(self.params.symbol, side, self.params.duration, self.params.stake)
                else:
                    pnl = await self._live_trade(self.params.symbol, side, self.params.duration, self.params.stake)
                self.daily_pnl += pnl
                # Atualiza estatísticas globais (paper ou live)
                try:
                    _global_stats.add_pnl(pnl)
                except Exception:
                    pass
                logger.info(f"Trade done [{self.params.mode}] side={side} pnl={pnl:.2f} daily={self.daily_pnl:.2f} reason={self.last_reason}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Strategy error: {e}")
            finally:
                self.in_position = False
                await asyncio.sleep(cooldown_seconds)
        self.running = False
        logger.info("Strategy loop stopped")

    async def start(self, params: StrategyParams):
        if self.task and not self.task.done():
            raise HTTPException(status_code=400, detail="Strategy already running")
        self.params = params
        self.mode = params.mode
        self.task = asyncio.create_task(self._loop())

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except Exception:
                pass
        self.running = False

    def status(self) -> StrategyStatus:
        # snapshot das métricas globais
        snap = _global_stats.snapshot()
        return StrategyStatus(
            running=self.running,
            mode=self.mode,
            symbol=self.params.symbol,
            in_position=self.in_position,
            daily_pnl=self.daily_pnl,
            day=self.day.isoformat(),
            last_signal=self.last_signal,
            last_reason=self.last_reason,
            last_run_at=self.last_run_at,
            wins=snap["wins"],
            losses=snap["losses"],
            total_trades=snap["total_trades"],
            win_rate=snap["win_rate"],
            global_daily_pnl=snap["global_daily_pnl"],
        )

_strategy = StrategyRunner()

@api_router.post("/strategy/start", response_model=StrategyStatus)
async def strategy_start(params: StrategyParams):
    await _strategy.start(params)
    return _strategy.status()

@api_router.post("/strategy/stop", response_model=StrategyStatus)
async def strategy_stop():
    await _strategy.stop()
    return _strategy.status()

@api_router.get("/strategy/status", response_model=StrategyStatus)
async def strategy_status():
    return _strategy.status()

# WebSocket endpoint to push ticks to clients (suporta querystring symbols=R_100,R_75 ou payload inicial JSON)
@app.websocket("/api/ws/ticks")
async def ws_ticks(websocket: WebSocket):
    await websocket.accept()
    queues: Dict[str, asyncio.Queue] = {}
    try:
        # 1) Primeiro tenta via querystring (?symbols=A,B,C)
        symbols_qs = websocket.query_params.get("symbols") if hasattr(websocket, "query_params") else None
        symbols: List[str] = []
        if symbols_qs:
            symbols = [s.strip() for s in symbols_qs.split(",") if s.strip()]
        # 2) Se não vier por query, espera payload inicial JSON {symbols:[]}
        if not symbols:
            try:
                init = await asyncio.wait_for(websocket.receive_text(), timeout=5)
                try:
                    msg = json.loads(init)
                    symbols = msg.get("symbols") or []
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                symbols = []
        if not symbols:
            await websocket.send_text(json.dumps({"type": "error", "message": "No symbols provided"}))
            await websocket.close()
            return
        # Add queues per symbol
        for s in symbols:
            q = await _deriv.add_queue(s)
            queues[s] = q
        await websocket.send_text(json.dumps({"type": "subscribed", "symbols": list(queues.keys())}))
        # Fan-out loop
        while True:
            # multiplex: get from any queue with timeout to also handle pings
            get_tasks = [asyncio.create_task(q.get()) for q in queues.values()]
            done, pending = await asyncio.wait(get_tasks, return_when=asyncio.FIRST_COMPLETED, timeout=15)
            for p in pending:
                p.cancel()
            if not done:
                # heartbeat
                await websocket.send_text(json.dumps({"type": "ping", "symbols": list(queues.keys())}))
                continue
            for d in done:
                try:
                    data = d.result()
                    await websocket.send_text(json.dumps(data))
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"Client WS error: {e}")
    finally:
        # cleanup
        for s, q in queues.items():
            _deriv.remove_queue(s, q)
        try:
            await websocket.close()
        except Exception:
            pass

# WebSocket endpoint to track a contract lifecycle
@app.websocket("/api/ws/contract/{contract_id}")
async def ws_contract(websocket: WebSocket, contract_id: int):
    await websocket.accept()
    q: Optional[asyncio.Queue] = None
    try:
        q = await _deriv.add_contract_queue(contract_id)
        await websocket.send_text(json.dumps({"type": "subscribed", "contract_id": contract_id}))
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=25)
                await websocket.send_text(json.dumps(data))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"Client WS (contract) error: {e}")
    finally:
        if q is not None:
            _deriv.remove_contract_queue(contract_id, q)
        try:
            await websocket.close()
        except Exception:
            pass

# ------------------- River Online (OHLCV online learning) -----------------------------

class RiverPredictCandle(BaseModel):
    datetime: Optional[str] = None
    open: float
    high: float
    low: float
    close: float
    volume: float

_river_model: Optional[river_online_model.RiverOnlineCandleModel] = None

def _get_river_model() -> river_online_model.RiverOnlineCandleModel:
    global _river_model
    if _river_model is not None:
        return _river_model
    # Try to load from disk else create new
    try:
        _river_model = river_online_model.RiverOnlineCandleModel.load()
    except Exception:
        _river_model = river_online_model.RiverOnlineCandleModel()
    return _river_model

@api_router.get("/ml/river/status")
async def river_status():
    try:
        m = _get_river_model()
        return {
            "initialized": True,
            "samples": getattr(m, "sample_count", 0),
            "acc": float(m.metric_acc.get()) if getattr(m, "sample_count", 0) > 0 else None,
            "logloss": float(m.metric_logloss.get()) if getattr(m, "sample_count", 0) > 0 else None,
            "model_path": river_online_model.MODEL_SAVE_PATH,
        }
    except Exception as e:
        return {"initialized": False, "error": str(e)}

@api_router.post("/ml/river/train_csv")
async def river_train_csv(csv_text: str = Body(..., embed=True)):
    """Treina/Atualiza o modelo online processando um CSV (texto)."""
    try:
        df = pd.read_csv(io.StringIO(csv_text))
        result = river_online_model.run_on_dataframe(df, _get_river_model())
        # Persist updated singleton
        result["model"].save(river_online_model.MODEL_SAVE_PATH)
        return result["summary"]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro no processamento do CSV: {e}")

@api_router.post("/ml/river/train_csv_upload")
async def river_train_csv_upload(file: UploadFile = File(...)):
    """Treina/Atualiza o modelo online enviando arquivo CSV (multipart/form-data)."""
    try:
        content = (await file.read()).decode("utf-8")
        df = pd.read_csv(io.StringIO(content))
        result = river_online_model.run_on_dataframe(df, _get_river_model())
        # Persist updated singleton
        result["model"].save(river_online_model.MODEL_SAVE_PATH)
        return result["summary"]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro no upload do CSV: {e}")

@api_router.post("/ml/river/predict")
async def river_predict(candle: RiverPredictCandle):
    """Predição online para um candle (sem atualizar o modelo)."""
    try:
        m = _get_river_model()
        info = m.predict_and_update(
            candle.datetime or datetime.utcnow().isoformat(),
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            next_close=None,
        )
        # not learning since next_close is None
        return {
            "prob_up": info["prob_up"],
            "pred_class": info["pred_class"],
            "signal": info["signal"],
            "features": info["features"],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro na predição: {e}")

class RiverDecideTradeRequest(BaseModel):
    symbol: str = "R_100"
    duration: int = 5
    duration_unit: str = "t"
    stake: float = 1.0
    currency: str = "USD"
    dry_run: bool = True
    candle: RiverPredictCandle

@api_router.post("/ml/river/decide_trade")
async def river_decide_trade(req: RiverDecideTradeRequest):
    """Decide LONG/SHORT e opcionalmente envia ordem real via Deriv (CALL/PUT) quando dry_run=False.
    Requer DERIV_API_TOKEN configurado e WS conectado para execução real.
    """
    m = _get_river_model()
    info = m.predict_and_update(
        req.candle.datetime or datetime.utcnow().isoformat(),
        req.candle.open,
        req.candle.high,
        req.candle.low,
        req.candle.close,
        req.candle.volume,
        next_close=None,
    )
    action = "CALL" if info["pred_class"] == 1 else "PUT"

    if req.dry_run:
        return {
            "decision": action,
            "prob_up": info["prob_up"],
            "signal": info["signal"],
            "dry_run": True,
        }

    # Execução real (usa endpoint existente internamente)
    buy_payload = BuyRequest(
        symbol=req.symbol,
        type="CALLPUT",
        contract_type=action,
        duration=req.duration,
        duration_unit=req.duration_unit,
        stake=req.stake,
        currency=req.currency,
    )

    try:
        result = await deriv_buy(buy_payload)
        return {
            "decision": action,
            "prob_up": info["prob_up"],
            "executed": True,
            "order_result": result,
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao executar ordem Deriv: {e}")

# ========================
# RIVER THRESHOLD CONTROL ENDPOINTS  
# ========================

class RiverThresholdConfig(BaseModel):
    river_threshold: float = Field(ge=0.5, le=0.95, description="Threshold entre 0.5 e 0.95")

class RiverBacktestRequest(BaseModel):
    symbol: str = "R_100"
    timeframe: str = "1m"  # 1m, 3m, 5m, 15m
    lookback_candles: int = 1000
    thresholds: List[float] = Field(default=[0.5, 0.53, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8])
    
class RiverPerformanceMetrics(BaseModel):
    threshold: float
    win_rate: float
    total_trades: int
    avg_trades_per_day: float
    expected_value: float
    max_drawdown: float
    sharpe_ratio: Optional[float] = None

@api_router.get("/strategy/river/config")
async def get_river_config():
    """Obter configuração atual do river_threshold"""
    return {
        "river_threshold": _strategy.params.river_threshold,
        "is_running": _strategy.running,
        "mode": _strategy.mode,
        "last_update": int(time.time())
    }

@api_router.post("/strategy/river/config")
async def update_river_config(config: RiverThresholdConfig):
    """Atualizar river_threshold em tempo real"""
    old_threshold = _strategy.params.river_threshold
    _strategy.params.river_threshold = config.river_threshold
    
    return {
        "success": True,
        "old_threshold": old_threshold,
        "new_threshold": config.river_threshold,
        "updated_at": int(time.time()),
        "message": f"River threshold alterado de {old_threshold:.3f} para {config.river_threshold:.3f}"
    }

@api_router.post("/strategy/river/backtest")
async def river_backtest(request: RiverBacktestRequest):
    """
    Backtesting rápido para diferentes river_thresholds
    Simula como diferentes thresholds afetariam a performance
    """
    try:
        # Buscar dados históricos
        candles_data = await fetch_candles_from_deriv(
            request.symbol, 
            60 if request.timeframe == "1m" else 180,  # granularity em segundos
            request.lookback_candles
        )
        
        if not candles_data or len(candles_data) < 100:
            raise HTTPException(status_code=400, detail="Dados insuficientes para backtesting")
        
        # Calcular indicadores técnicos para todos os candles
        close_prices = [float(c["close"]) for c in candles_data]
        high_prices = [float(c["high"]) for c in candles_data]
        low_prices = [float(c["low"]) for c in candles_data]
        
        adx_values = _adx(high_prices, low_prices, close_prices)
        rsi_values = _rsi(close_prices)
        bb_values = _bollinger(close_prices, 20, 2.0)
        
        results = []
        
        # Testar cada threshold
        for threshold in request.thresholds:
            trades = []
            current_pos = None
            
            # Simular River predictions (simplified)
            for i in range(50, len(candles_data) - 1):  # Skip first 50 for indicators
                try:
                    # Simular probabilidade River (simplified - na prática usaria modelo treinado)
                    current_close = float(candles_data[i]["close"])
                    prev_close = float(candles_data[i-1]["close"])
                    next_close = float(candles_data[i+1]["close"])
                    
                    # Simulated probability based on price momentum
                    momentum = (current_close - prev_close) / prev_close
                    prob_up = 0.5 + (momentum * 2)  # Simple momentum-based probability
                    prob_up = max(0.1, min(0.9, prob_up))  # Clamp between 0.1 and 0.9
                    
                    # Verificar se River dá sinal
                    river_signal = None
                    if prob_up >= threshold:
                        river_signal = "RISE"
                    elif prob_up <= (1.0 - threshold):
                        river_signal = "FALL"
                    
                    if river_signal is None:
                        continue
                        
                    # Verificar indicadores técnicos
                    if i < len(adx_values) and i < len(rsi_values):
                        adx = adx_values[i]
                        rsi = rsi_values[i]
                        
                        if adx is None or rsi is None:
                            continue
                            
                        # Lógica de confirmação (simplificada)
                        technical_signal = None
                        if river_signal == "RISE" and rsi < 70 and adx > 22:
                            technical_signal = "CALL"
                        elif river_signal == "FALL" and rsi > 30 and adx > 22:
                            technical_signal = "PUT"
                            
                        if technical_signal:
                            # Simular trade
                            entry_price = current_close
                            exit_price = next_close
                            
                            if technical_signal == "CALL":
                                pnl = 0.95 if exit_price > entry_price else -1.0
                            else:  # PUT
                                pnl = 0.95 if exit_price < entry_price else -1.0
                                
                            trades.append({
                                "entry_time": candles_data[i]["timestamp"],
                                "entry_price": entry_price,
                                "exit_price": exit_price,
                                "type": technical_signal,
                                "pnl": pnl,
                                "river_prob": prob_up
                            })
                            
                except Exception as e:
                    continue
            
            # Calcular métricas
            if len(trades) > 0:
                wins = len([t for t in trades if t["pnl"] > 0])
                win_rate = wins / len(trades)
                total_pnl = sum(t["pnl"] for t in trades)
                expected_value = total_pnl / len(trades)
                
                # Calcular drawdown
                cumulative_pnl = 0
                peak = 0
                max_dd = 0
                for trade in trades:
                    cumulative_pnl += trade["pnl"]
                    if cumulative_pnl > peak:
                        peak = cumulative_pnl
                    drawdown = peak - cumulative_pnl
                    if drawdown > max_dd:
                        max_dd = drawdown
                
                # Estimar trades por dia (assume 1 minuto candles)
                time_span_hours = (len(candles_data) * (60 if request.timeframe == "1m" else 180)) / 3600
                trades_per_day = len(trades) / (time_span_hours / 24) if time_span_hours > 0 else 0
                
                results.append(RiverPerformanceMetrics(
                    threshold=threshold,
                    win_rate=win_rate,
                    total_trades=len(trades),
                    avg_trades_per_day=trades_per_day,
                    expected_value=expected_value,
                    max_drawdown=max_dd
                ))
            else:
                results.append(RiverPerformanceMetrics(
                    threshold=threshold,
                    win_rate=0.0,
                    total_trades=0,
                    avg_trades_per_day=0.0,
                    expected_value=0.0,
                    max_drawdown=0.0
                ))
        
        # Encontrar melhor threshold baseado em expected value
        best_result = max(results, key=lambda x: x.expected_value) if results else None
        
        return {
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "candles_analyzed": len(candles_data),
            "results": results,
            "best_threshold": best_result.threshold if best_result else None,
            "current_threshold": _strategy.params.river_threshold,
            "recommendation": {
                "suggested_threshold": best_result.threshold if best_result else 0.53,
                "expected_improvement": f"+{((best_result.win_rate - 0.41) * 100):.1f}%" if best_result and best_result.win_rate > 0.41 else "N/A",
                "rationale": f"Threshold {best_result.threshold:.2f} mostrou win rate de {best_result.win_rate:.1%} com {best_result.total_trades} trades" if best_result else "Dados insuficientes"
            }
        }
        
    except Exception as e:
        logger.error(f"Erro no backtesting River: {e}")
        raise HTTPException(status_code=500, detail=f"Erro no backtesting: {str(e)}")

@api_router.get("/strategy/river/performance")
async def get_river_performance():
    """Obter métricas de performance atuais do River"""
    try:
        # Obter status do River
        river_model_path = "/app/backend/ml_models/river_online_model.pkl"
        river_stats = {}
        
        if os.path.exists(river_model_path):
            try:
                from river_online_model import RiverOnlineCandleModel
                model = RiverOnlineCandleModel.load(river_model_path)
                river_stats = {
                    "samples": model.sample_count,
                    "accuracy": float(model.metric_acc.get()) if model.sample_count > 0 else None,
                    "logloss": float(model.metric_logloss.get()) if model.sample_count > 0 else None,
                }
            except Exception as e:
                logger.warning(f"Erro ao carregar modelo River: {e}")
        
        # Obter estatísticas globais atuais
        global_stats = _global_stats.get_stats()
        
        return {
            "current_threshold": _strategy.params.river_threshold,
            "river_model": river_stats,
            "strategy_performance": {
                "win_rate": global_stats.get("win_rate", 0.0),
                "total_trades": global_stats.get("total_trades", 0),
                "wins": global_stats.get("wins", 0),
                "losses": global_stats.get("losses", 0),
                "daily_pnl": _global_pnl.get(),
            },
            "is_running": strategy_runner.running,
            "last_signal": strategy_runner.last_signal,
            "last_reason": strategy_runner.last_reason,
            "timestamp": int(time.time())
        }
        
    except Exception as e:
        logger.error(f"Erro ao obter performance River: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao obter performance: {str(e)}")

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)