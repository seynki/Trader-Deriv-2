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

# 3rd party realtime
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection (MUST use env)
mongo_url = os.environ.get('MONGO_URL')
if mongo_url:
    try:
        if mongo_url.startswith("mongodb+srv://"):
            client = AsyncIOMotorClient(mongo_url, tls=True, tlsCAFile=certifi.where())
        else:
            client = AsyncIOMotorClient(mongo_url)
        db = client[os.environ.get('DB_NAME', 'test_database')]
    except Exception as e:
        client = None
        db = None
        logging.warning(f"Mongo connection init failed: {e}")
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
                            
                            # Update global stats when contract expires
                            if poc.get("is_expired"):
                                try:
                                    profit = float(poc.get("profit") or 0.0)
                                    accounted = _global_stats.add_trade_result(cid_int, profit)
                                    if accounted:
                                        # Only add to global PnL once per contract
                                        try:
                                            _global_pnl.add(profit)
                                        except Exception:
                                            pass
                                        logger.info(f"Updated global stats: contract_id={cid_int}, profit={profit}, total_trades={_global_stats.total_trades}")
                                except Exception as e:
                                    logger.warning(f"Failed to update global stats: {e}")
                            
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

    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    await _deriv._send(payload)
    try:
        data = await asyncio.wait_for(fut, timeout=12)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for proposal")
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", "Proposal error"))
    p = data.get("proposal", {})
    return {
        "id": p.get("id"),
        "symbol": p.get("symbol"),
        "contract_type": p.get("contract_type"),
        "ask_price": float(p.get("ask_price", 0) or 0),
        "payout": float(p.get("payout", 0) or 0),
        "spot": p.get("spot"),
        "barrier": p.get("barrier"),
    }

@api_router.post("/deriv/buy")
async def deriv_buy(req: BuyRequest):
    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")

    t = (req.type or "CALLPUT").upper()
    if t == "CALLPUT":
        # 1) proposal
        proposal = await deriv_proposal(BuyRequest(**{**req.model_dump(), "type": "CALLPUT"}))
        # 2) send buy for proposal id
        req_id = int(time.time() * 1000)
        fut = asyncio.get_running_loop().create_future()
        _deriv.pending[req_id] = fut
        await _deriv._send({
            "buy": proposal["id"],
            "price": req.max_price or proposal["ask_price"],
            "subscribe": 1,
            "req_id": req_id,
        })
        try:
            data = await asyncio.wait_for(fut, timeout=12)
        except asyncio.TimeoutError:
            _deriv.pending.pop(req_id, None)
            raise HTTPException(status_code=504, detail="Timeout waiting for buy response")
        if data.get("error"):
            raise HTTPException(status_code=400, detail=data["error"].get("message", "Buy error"))
        b = data.get("buy", {})
    else:
        # Direct buy with parameters
        # Build appropriate payload for non-vanilla contracts
        payload: Dict[str, Any] = {"buy": 1, "price": float(req.max_price or 0), "parameters": {}}
        if t == "ACCUMULATOR":
            payload["price"] = float(req.max_price if req.max_price is not None else req.stake)
            payload["parameters"] = {
                "amount": float(req.stake),
                "basis": "stake",
                "contract_type": "ACCU",
                "currency": req.currency,
                "symbol": req.symbol,
            }
            if req.growth_rate is not None:
                payload["parameters"]["growth_rate"] = float(req.growth_rate)
            if req.limit_order:
                lo: Dict[str, Any] = {}
                try:
                    tp = req.limit_order.get("take_profit")
                    if tp is not None:
                        lo["take_profit"] = float(tp)
                except Exception:
                    pass
                if lo:
                    payload["parameters"]["limit_order"] = lo
        elif t == "TURBOS":
            payload["parameters"] = {
                "amount": float(req.stake),
                "basis": "stake",
                "contract_type": (req.contract_type or "TURBOSLONG").upper(),
                "currency": req.currency,
                "symbol": req.symbol,
                "strike": req.strike or "ATM",
            }
        elif t == "MULTIPLIERS":
            payload["price"] = float(req.max_price if req.max_price is not None else req.stake)
            payload["parameters"] = {
                "amount": float(req.stake),
                "basis": "stake",
                "contract_type": (req.contract_type or "MULTUP").upper(),
                "currency": req.currency,
                "symbol": req.symbol,
            }
            if req.multiplier:
                payload["parameters"]["multiplier"] = int(req.multiplier)
            if req.limit_order:
                payload["parameters"]["limit_order"] = req.limit_order
        else:
            # Fallback generic
            payload["parameters"] = {
                "amount": float(req.stake),
                "basis": "stake",
                "contract_type": (req.contract_type or "CALL").upper(),
                "currency": req.currency,
                "symbol": req.symbol,
            }
            if req.duration:
                payload["parameters"]["duration"] = int(req.duration)
            if req.duration_unit:
                payload["parameters"]["duration_unit"] = req.duration_unit
            if req.barrier:
                payload["parameters"]["barrier"] = req.barrier

        req_id = int(time.time() * 1000)
        fut = asyncio.get_running_loop().create_future()
        _deriv.pending[req_id] = fut
        payload["req_id"] = req_id
        await _deriv._send(payload)
        try:
            data = await asyncio.wait_for(fut, timeout=12)
        except asyncio.TimeoutError:
            _deriv.pending.pop(req_id, None)
            raise HTTPException(status_code=504, detail="Timeout waiting for buy response")
        if data.get("error"):
            raise HTTPException(status_code=400, detail=data["error"].get("message", "Buy error"))
        b = data.get("buy", {})

    # Try to prime contract subscription as soon as we know the id
    try:
        cid = int(b.get("contract_id")) if b.get("contract_id") is not None else None
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
    mode: str = "paper"  # paper | live
    # ML gating (Passo 4)
    ml_gate: bool = False
    ml_prob_threshold: float = 0.5

# Track PnL per trade action type
class _PnLTracker:
    def __init__(self):
        self.total_pnl: float = 0.0
        self.daily_pnl: float = 0.0
        self.last_day: date = date.today()

    def add(self, profit: float):
        # reset if new day
        today = date.today()
        if today != self.last_day:
            self.last_day = today
            self.daily_pnl = 0.0
        self.total_pnl += profit
        self.daily_pnl += profit

_global_pnl = _PnLTracker()

class GlobalStats:
    """Global statistics tracker for all trades (manual + automated + strategy)"""
    def __init__(self):
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.daily_pnl: float = 0.0
        self.processed_contracts: set = set()  # To avoid double counting for live/manual
        self.last_day: date = date.today()
        
    def _ensure_today(self):
        today = date.today()
        if today != self.last_day:
            # Reset ALL day stats and processed set for a clean new day
            self.last_day = today
            self.total_trades = 0
            self.wins = 0
            self.losses = 0
            self.daily_pnl = 0.0
            self.processed_contracts = set()
        
    def add_trade_result(self, contract_id: int, profit: float) -> bool:
        """Add a completed LIVE/MANUAL trade result to global stats (uses contract_id dedup).
        Returns True when this contract_id was accounted for (first time), False otherwise.
        """
        self._ensure_today()
        if contract_id in self.processed_contracts:
            return False  # Already processed
        self.processed_contracts.add(contract_id)
        self._apply_profit(profit)
        return True

    def add_paper_trade_result(self, profit: float):
        """Add a simulated (paper) trade to global stats (no contract_id)."""
        self._ensure_today()
        self._apply_profit(profit)

    def _apply_profit(self, profit: float):
        self.total_trades += 1
        self.daily_pnl += profit
        if profit > 0:
            self.wins += 1
        else:
            self.losses += 1
    
    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage"""
        self._ensure_today()
        if self.total_trades == 0:
            return 0.0
        return (self.wins / self.total_trades) * 100.0
    
    def reset_daily(self):
        """Reset daily stats (forcibly resets all day counters and contracts)"""
        self.last_day = date.today()
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.daily_pnl = 0.0
        self.processed_contracts = set()

class StrategyStatus(BaseModel):
    running: bool
    mode: str
    symbol: str
    in_position: bool
    daily_pnl: float
    global_daily_pnl: float = 0.0
    day: str
    last_signal: Optional[str] = None
    last_reason: Optional[str] = None
    last_run_at: Optional[int] = None
    # Global stats (all trades: manual + automated + strategy)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0

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


def _true_range(h: float, l: float, pc: float) -> float:
    return max(h - l, abs(h - pc), abs(l - pc))


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

# Global statistics instance
_global_stats = GlobalStats()


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

        if trending:
            bull_cross = (prev_fast is not None and prev_slow is not None and ma_fast is not None and ma_slow is not None and last_macd is not None and last_sig is not None and prev_fast < prev_slow and ma_fast > ma_slow and last_macd > last_sig)
            bear_cross = (prev_fast is not None and prev_slow is not None and ma_fast is not None and ma_slow is not None and last_macd is not None and last_sig is not None and prev_fast > prev_slow and ma_fast < ma_slow and last_macd < last_sig)
            if bull_cross:
                return {"side": "RISE", "reason": f"Trend↑ ADX {last_adx:.1f} + MA/MACD"}
            if bear_cross:
                return {"side": "FALL", "reason": f"Trend↓ ADX {last_adx:.1f} + MA/MACD"}
        else:
            touch_upper = (last_upper is not None and last_price >= last_upper)
            touch_lower = (last_lower is not None and last_price <= last_lower)
            if touch_upper and (last_rsi is not None) and last_rsi >= self.params.rsi_ob:
                return {"side": "FALL", "reason": f"Range: BB↑ + RSI {int(last_rsi)} (reversão)"}
            if touch_lower and (last_rsi is not None) and last_rsi <= self.params.rsi_os:
                return {"side": "RISE", "reason": f"Range: BB↓ + RSI {int(last_rsi)} (reversão)"}
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
                # ML gate (probability calibrated > threshold)
                if self.params.ml_gate:
                    try:
                        import ml_utils
                        import numpy as np
                        champ = ml_utils.load_champion()
                        # If no champion, skip trading
                        if not champ or not champ.get("model_id"):
                            await asyncio.sleep(cooldown_seconds)
                            continue
                        # Build last window features from candles
                        import pandas as pd
                        df = pd.DataFrame(candles)
                        df = df[["open","high","low","close","volume"]].astype(float)
                        feats_df = ml_utils.build_features(df)
                        X_cols = ml_utils.select_features(feats_df)
                        X = feats_df[X_cols].replace([np.inf, -np.inf], np.nan).dropna()
                        if len(X) == 0:
                            await asyncio.sleep(cooldown_seconds)
                            continue
                        # load model
                        from joblib import load as joblib_load
                        model_path = Path(__file__).parent / "ml_models" / f"{champ.get('model_id')}.joblib"
                        if not model_path.exists():
                            await asyncio.sleep(cooldown_seconds)
                            continue
                        payload = joblib_load(model_path)
                        model = payload.get("model")
                        # predict proba on last row
                        x_last = X.iloc[[-1]]
                        proba = None
                        if hasattr(model, "predict_proba"):
                            try:
                                proba = float(model.predict_proba(x_last)[:,1][0])
                            except Exception:
                                proba = None
                        # gate check
                        if (proba is None) or (proba < self.params.ml_prob_threshold):
                            # Do not trade; keep info visible
                            proba_str = (f"{proba:.2f}" if proba is not None else "NA")
                            th_str = f"{self.params.ml_prob_threshold:.2f}"
                            self.last_reason = f"Gate ML: proba={proba_str} < th={th_str}"
                            await asyncio.sleep(cooldown_seconds)
                            continue
                    except Exception as _e:
                        logger.warning(f"ML gate failed: {_e}")
                        # Fail-open or fail-close? We'll fail-close to be safer (skip trade)
                        await asyncio.sleep(cooldown_seconds)
                        continue

                    continue
                self.last_signal = signal.get("side")
                self.last_reason = signal.get("reason")
                side = signal.get("side")
                # trade
                self.in_position = True
                if self.params.mode == "paper":
                    pnl = await self._paper_trade(self.params.symbol, side, self.params.duration, self.params.stake)
                    # Update global stats and global pnl for paper trades as requested
                    try:
                        _global_stats.add_paper_trade_result(pnl)
                    except Exception:
                        pass
                    try:
                        _global_pnl.add(pnl)
                    except Exception:
                        pass
                else:
                    pnl = await self._live_trade(self.params.symbol, side, self.params.duration, self.params.stake)
                self.daily_pnl += pnl
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
        return StrategyStatus(
            running=self.running,
            mode=self.mode,
            symbol=self.params.symbol,
            in_position=self.in_position,
            daily_pnl=self.daily_pnl,
            global_daily_pnl=_global_pnl.daily_pnl,
            day=self.day.isoformat(),
            last_signal=self.last_signal,
            last_reason=self.last_reason,
            last_run_at=self.last_run_at,
            # Include global stats from all trades
            total_trades=_global_stats.total_trades,
            wins=_global_stats.wins,
            losses=_global_stats.losses,
            win_rate=_global_stats.win_rate,
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

# ---- Candles → Mongo ingest ----
async def _fetch_candles(symbol: str, granularity: int, count: int) -> List[Dict[str, Any]]:
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
        data = await asyncio.wait_for(fut, timeout=15)
    except asyncio.TimeoutError:
        _deriv.pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Timeout waiting for candles")
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("message", "history error"))
    return data.get("candles") or []


def _tf_label_from_granularity(g: int) -> str:
    if g < 60:
        return f"{g}s"
    if g % 60 == 0:
        m = g // 60
        return f"{m}m"
    return f"{g}s"


def _granularity_from_timeframe(tf: str) -> int:
    """Parse '3m', '5m', '1h', '60', '60s' -> seconds"""
    s = str(tf).strip().lower()
    try:
        if s.endswith('s'):
            return int(s[:-1])
        if s.endswith('m'):
            return int(s[:-1]) * 60
        if s.endswith('h'):
            return int(s[:-1]) * 3600
        # plain number assumed seconds
        return int(s)
    except Exception:
        return 60


async def _fetch_candles_paginated(symbol: str, granularity: int, total: int) -> List[Dict[str, Any]]:
    """Fetch up to 'total' candles from Deriv in batches, oldest->newest."""
    all_candles: List[Dict[str, Any]] = []
    end_val: Any = "latest"
    max_batch = 4999
    while len(all_candles) < total:
        remain = total - len(all_candles)
        count = max(100, min(max_batch, remain))
        req_id = int(time.time() * 1000)
        fut = asyncio.get_running_loop().create_future()
        _deriv.pending[req_id] = fut
        await _deriv._send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": end_val,
            "start": 1,
            "style": "candles",
            "granularity": granularity,
            "req_id": req_id,
        })
        try:
            data = await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError:
            _deriv.pending.pop(req_id, None)
            raise HTTPException(status_code=504, detail="Timeout waiting for candles batch")
        if data.get("error"):
            raise HTTPException(status_code=400, detail=data["error"].get("message", "history error"))
        batch = data.get("candles") or []
        if not batch:
            break
        all_candles = batch + all_candles  # prepend older batch before existing
        # next end: earliest epoch - 1
        earliest = batch[0].get("epoch")
        if earliest is None:
            break
        end_val = int(earliest) - 1
        # safety: avoid too many loops
        if len(batch) < 5:
            break
        await asyncio.sleep(0.2)
    # ensure ascending by time
    all_candles.sort(key=lambda x: x.get("epoch", 0))
    # keep only last 'total'
    if len(all_candles) > total:
        all_candles = all_candles[-total:]
    return all_candles


@api_router.post("/candles/ingest")
async def candles_ingest(symbol: str = "R_100", granularity: int = 60, count: int = 2000, timeframe: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=503, detail="MongoDB indisponível (configure MONGO_URL no backend/.env)")
    candles = await _fetch_candles(symbol, granularity, count)
    tf = timeframe or _tf_label_from_granularity(granularity)
    col = db.candles
    inserted = 0
    updated = 0
    for c in candles:
        doc = {
            "symbol": symbol,
            "timeframe": tf,
            "time": int(c.get("epoch")),
            "open": float(c.get("open")),
            "high": float(c.get("high")),
            "low": float(c.get("low")),
            "close": float(c.get("close")),
            "volume": float(c.get("volume")) if c.get("volume") is not None else 0.0,
        }
        try:
            res = await col.update_one({"symbol": symbol, "timeframe": tf, "time": doc["time"]}, {"$set": doc}, upsert=True)
            if res.upserted_id is not None:
                inserted += 1
            else:
                # matched + modified
                updated += res.modified_count or 0
        except Exception as e:
            logging.getLogger(__name__).warning(f"Mongo upsert candle falhou: {e}")
    return {"symbol": symbol, "timeframe": tf, "received": len(candles), "inserted": inserted, "updated": updated}


# WebSocket endpoint to push ticks to clients
@app.websocket("/api/ws/ticks")
async def ws_ticks(websocket: WebSocket):
    await websocket.accept()
    queues: Dict[str, asyncio.Queue] = {}
    try:
        # Wait for a subscribe message
        init = await websocket.receive_text()
        try:
            msg = json.loads(init)
        except json.JSONDecodeError:
            await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
            await websocket.close()
            return
        symbols = msg.get("symbols") or []
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
                await websocket.send_text(json.dumps({"type": "ping"}))
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

# Include the router in the main app
app.include_router(api_router)

# --------------- ML endpoints (status + manual train + rules) -----------------
from joblib import load as joblib_load
from sklearn.tree import DecisionTreeClassifier, export_text
import pandas as pd
from pathlib import Path as _Path
import ml_utils

ml_router = APIRouter(prefix="/api/ml")

@ml_router.get("/status")
async def ml_status():
    meta = ml_utils.load_champion()
    return meta or {"message": "no champion"}


def _parse_csv_or_raise(path: _Path) -> pd.DataFrame:
    if not path.exists():
        raise HTTPException(status_code=400, detail="Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")
    df = pd.read_csv(path)
    return df


# ------------------ ASYNC JOB MANAGER FOR HEAVY TRAINING ------------------
_ml_jobs: Dict[str, Dict[str, Any]] = {}


def _update_job(job_id: str, patch: Dict[str, Any]):
    job = _ml_jobs.get(job_id)
    if not job:
        return
    job.update(patch)
    job["updated_at"] = int(time.time())


async def _run_train_job(job_id: str,
                         source: str,
                         symbol: str,
                         timeframe: str,
                         horizon: int,
                         threshold: float,
                         model_type: str,
                         count: int,
                         thresholds: Optional[str],
                         horizons: Optional[str],
                         class_weight: Optional[str],
                         calibrate: Optional[str],
                         objective: str):
    try:
        _update_job(job_id, {"status": "running", "started_at": int(time.time())})
        # replicate core of ml_train with progress
        df: Optional[pd.DataFrame] = None
        tf = timeframe
        if source == "mongo" and db is not None:
            try:
                recs = await db.candles.find({"symbol": symbol, "timeframe": tf}).sort("time", 1).to_list(50000)
                if recs:
                    df = pd.DataFrame(recs)
            except Exception as e:
                logging.getLogger(__name__).warning(f"ML load mongo failed: {e}")
        elif source == "file":
            df = _parse_csv_or_raise(_Path("/data/ml/ohlcv.csv"))
        elif source == "deriv":
            # Reutiliza a MESMA sessão WS do app e aguarda conexão ficar pronta
            ready = await _wait_deriv_ready(max_wait=10.0, require_auth=False)
            if not ready:
                raise HTTPException(status_code=503, detail="Deriv not connected (aguarde a conexão DEMO ficar verde e tente de novo)")
            _update_job(job_id, {"stage": "downloading_deriv_candles"})
            gran = _granularity_from_timeframe(tf)
            candles = await _fetch_candles_paginated(symbol, granularity=gran, total=count)
            if not candles or len(candles) < 1000:
                raise HTTPException(status_code=400, detail="Dados insuficientes vindos da Deriv")
            df = pd.DataFrame([{
                "open": float(c.get("open")),
                "high": float(c.get("high")),
                "low": float(c.get("low")),
                "close": float(c.get("close")),
                "volume": float(c.get("volume")) if c.get("volume") is not None else 0.0,
                "time": int(c.get("epoch")),
            } for c in candles])
        else:
            df = _parse_csv_or_raise(_Path("/data/ml/ohlcv.csv"))

        if df is None or df.empty:
            raise HTTPException(status_code=400, detail="Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")

        ren = {c: c.lower() for c in df.columns}
        df = df.rename(columns=ren)
        for c in ["open","high","low","close","volume"]:
            if c not in df.columns:
                raise HTTPException(status_code=400, detail=f"CSV/DB sem coluna obrigatória: {c}")
        if "time" in df.columns:
            df = df.sort_values("time").reset_index(drop=True)

        def _parse_list(sval: Optional[str], cast=float):
            if not sval:
                return None
            try:
                return [cast(x.strip()) for x in str(sval).split(',') if x.strip() != '']
            except Exception:
                return None

        thr_list = _parse_list(thresholds, float) or [0.002, 0.003, 0.004, 0.005]
        hor_list = _parse_list(horizons, int) or [1, 3, 5]

        gran = _granularity_from_timeframe(tf)
        candles_per_day = 86400.0 / max(gran, 1)

        results: List[Dict[str, Any]] = []
        best: Optional[Dict[str, Any]] = None
        total = len(hor_list) * len(thr_list)
        done = 0
        _update_job(job_id, {"progress": {"done": done, "total": total}})

        for h in hor_list:
            for tval in thr_list:
                try:
                    out = ml_utils.train_and_maybe_promote(
                        df[["open","high","low","close","volume"]].copy(),
                        horizon=h,
                        threshold=float(tval),
                        model_type=model_type,
                        save_prefix=f"{symbol}_{tf}_h{h}_th{tval:.3f}",
                        class_weight=class_weight,
                        calibrate=calibrate,
                        payout_ratio=0.95,
                        candles_per_day=float(candles_per_day),
                        objective=objective,
                    )
                    out.update({"horizon": h, "threshold": float(tval)})
                    results.append(out)
                    cur_prec = out.get("metrics", {}).get("precision", 0.0) or 0.0
                    cur_ev = out.get("backtest", {}).get("ev_per_trade", 0.0) or 0.0
                    cur_tpd = out.get("metrics", {}).get("trades_per_day", 0.0) or 0.0
                    if best is None:
                        best = out
                    else:
                        b_prec = best.get("metrics", {}).get("precision", 0.0) or 0.0
                        b_ev = best.get("backtest", {}).get("ev_per_trade", 0.0) or 0.0
                        b_tpd = best.get("metrics", {}).get("trades_per_day", 0.0) or 0.0
                        if (cur_prec, cur_ev, cur_tpd) > (b_prec, b_ev, b_tpd):
                            best = out
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Grid combo failed h={h} t={tval}: {e}")
                finally:
                    done += 1
                    _update_job(job_id, {"progress": {"done": done, "total": total}})

        if not best:
            raise HTTPException(status_code=400, detail="Falha ao treinar qualquer combinação")

        result = {
            **best,
            "grid": [{
                "model_id": r.get("model_id"),
                "horizon": r.get("horizon"),
                "threshold": r.get("threshold"),
                "precision": r.get("metrics", {}).get("precision"),
                "ev_per_trade": r.get("backtest", {}).get("ev_per_trade"),
                "trades_per_day": r.get("metrics", {}).get("trades_per_day"),
            } for r in results],
            "rows": int(len(df)),
            "granularity": gran,
            "symbol": symbol,
            "timeframe": tf,
        }
        _update_job(job_id, {"status": "done", "result": result})
    except HTTPException as he:
        _update_job(job_id, {"status": "error", "error": he.detail})
    except Exception as e:
        _update_job(job_id, {"status": "error", "error": str(e)})


@ml_router.post("/train_async")
async def ml_train_async(
    source: str = "deriv",
    symbol: str = "R_100",
    timeframe: str = "3m",
    horizon: int = 3,
    threshold: float = 0.003,
    model_type: str = "rf",
    count: int = 20000,
    thresholds: Optional[str] = None,
    horizons: Optional[str] = None,
    class_weight: Optional[str] = "balanced",
    calibrate: Optional[str] = "sigmoid",
    objective: str = "precision",
):
    job_id = str(uuid.uuid4())
    _ml_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": int(time.time()),
        "params": {"source": source, "symbol": symbol, "timeframe": timeframe, "count": count,
                    "thresholds": thresholds, "horizons": horizons, "model_type": model_type,
                    "class_weight": class_weight, "calibrate": calibrate, "objective": objective}
    }
    asyncio.create_task(_run_train_job(job_id, source, symbol, timeframe, horizon, threshold, model_type, count, thresholds, horizons, class_weight, calibrate, objective))
    return {"job_id": job_id, "status": "queued"}


@ml_router.get("/job/{job_id}")
async def ml_job_status(job_id: str):
    job = _ml_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return job


@ml_router.post("/train")
async def ml_train(
    source: str = "mongo",
    symbol: str = "R_100",
    timeframe: str = "3m",
    horizon: int = 3,
    threshold: float = 0.003,
    model_type: str = "rf",
    count: int = 20000,
    thresholds: Optional[str] = None,
    horizons: Optional[str] = None,
    class_weight: Optional[str] = "balanced",
    calibrate: Optional[str] = "sigmoid",
    objective: str = "precision",
):
    df: Optional[pd.DataFrame] = None
    tf = timeframe
    if source == "mongo" and db is not None:
        try:
            recs = await db.candles.find({"symbol": symbol, "timeframe": tf}).sort("time", 1).to_list(50000)
            if recs:
                df = pd.DataFrame(recs)
        except Exception as e:
            logging.getLogger(__name__).warning(f"ML load mongo failed: {e}")
    elif source == "file":
        df = _parse_csv_or_raise(_Path("/data/ml/ohlcv.csv"))
    elif source == "deriv":
        # Wait briefly for the shared Deriv connection to be ready (reuse same session)
        ready = await _wait_deriv_ready(max_wait=10.0, require_auth=False)
        if not ready:
            raise HTTPException(status_code=503, detail="Deriv not connected (aguarde a conexão DEMO ficar verde e tente de novo)")
        gran = _granularity_from_timeframe(tf)
        candles = await _fetch_candles_paginated(symbol, granularity=gran, total=count)
        if not candles or len(candles) < 1000:
            raise HTTPException(status_code=400, detail="Dados insuficientes vindos da Deriv")
        # to DataFrame
        df = pd.DataFrame([{
            "open": float(c.get("open")),
            "high": float(c.get("high")),
            "low": float(c.get("low")),
            "close": float(c.get("close")),
            "volume": float(c.get("volume")) if c.get("volume") is not None else 0.0,
            "time": int(c.get("epoch")),
        } for c in candles])
    else:
        # fallback: try CSV
        df = _parse_csv_or_raise(_Path("/data/ml/ohlcv.csv"))

    if df is None or df.empty:
        raise HTTPException(status_code=400, detail="Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")

    ren = {c: c.lower() for c in df.columns}
    df = df.rename(columns=ren)
    for c in ["open","high","low","close","volume"]:
        if c not in df.columns:
            raise HTTPException(status_code=400, detail=f"CSV/DB sem coluna obrigatória: {c}")

    # sort by time if present
    if "time" in df.columns:
        df = df.sort_values("time").reset_index(drop=True)

    # Sweep lists
    def _parse_list(sval: Optional[str], cast=float):
        if not sval:
            return None
        try:
            return [cast(x.strip()) for x in str(sval).split(',') if x.strip() != '']
        except Exception:
            return None

    thr_list = _parse_list(thresholds, float) or [0.002, 0.003, 0.004, 0.005]
    hor_list = _parse_list(horizons, int) or [1, 3, 5]

    gran = _granularity_from_timeframe(tf)
    candles_per_day = 86400.0 / max(gran, 1)

    results: List[Dict[str, Any]] = []
    best: Optional[Dict[str, Any]] = None

    for h in hor_list:
        for tval in thr_list:
            try:
                out = ml_utils.train_and_maybe_promote(
                    df[["open","high","low","close","volume"]].copy(),
                    horizon=h,
                    threshold=float(tval),
                    model_type=model_type,
                    save_prefix=f"{symbol}_{tf}_h{h}_th{tval:.3f}",
                    class_weight=class_weight,
                    calibrate=calibrate,
                    payout_ratio=0.95,
                    candles_per_day=float(candles_per_day),
                    objective=objective,
                )
                out.update({"horizon": h, "threshold": float(tval)})
                results.append(out)
                # choose best by precision then ev_per_trade then trades_per_day
                cur_prec = out.get("metrics", {}).get("precision", 0.0) or 0.0
                cur_ev = out.get("backtest", {}).get("ev_per_trade", 0.0) or 0.0
                cur_tpd = out.get("metrics", {}).get("trades_per_day", 0.0) or 0.0
                if best is None:
                    best = out
                else:
                    b_prec = best.get("metrics", {}).get("precision", 0.0) or 0.0
                    b_ev = best.get("backtest", {}).get("ev_per_trade", 0.0) or 0.0
                    b_tpd = best.get("metrics", {}).get("trades_per_day", 0.0) or 0.0
                    if (cur_prec, cur_ev, cur_tpd) > (b_prec, b_ev, b_tpd):
                        best = out
            except Exception as e:
                logging.getLogger(__name__).warning(f"Grid combo failed h={h} t={tval}: {e}")
                continue

    if not best:
        raise HTTPException(status_code=400, detail="Falha ao treinar qualquer combinação")

    # Return best result, include grid summary
    return {
        **best,
        "grid": [{
            "model_id": r.get("model_id"),
            "horizon": r.get("horizon"),
            "threshold": r.get("threshold"),
            "precision": r.get("metrics", {}).get("precision"),
            "ev_per_trade": r.get("backtest", {}).get("ev_per_trade"),
            "trades_per_day": r.get("metrics", {}).get("trades_per_day"),
        } for r in results],
        "rows": int(len(df)),
        "granularity": gran,
    }

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

app.include_router(ml_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)