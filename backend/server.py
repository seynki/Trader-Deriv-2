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
    buy_response_payload: Dict[str, Any] = {}
    proposal_snapshot: Optional[Dict[str, Any]] = None

    if t == "CALLPUT":
        # 1) proposal
        proposal = await deriv_proposal(BuyRequest(**{**req.model_dump(), "type": "CALLPUT"}))
        proposal_snapshot = proposal
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
        buy_response_payload = b
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
        buy_response_payload = b

    # Try to prime contract subscription as soon as we know the id
    try:
        cid = int(buy_response_payload.get("contract_id")) if buy_response_payload.get("contract_id") is not None else None
        if cid:
            await _deriv.ensure_contract_subscription(cid)
    except Exception:
        pass

    # Persist initial contract to MongoDB Atlas
    try:
        if db is not None:
            base_doc = ContractCreate(
                timestamp=int(time.time()),
                symbol=req.symbol,
                market="deriv",
                duration=req.duration,
                duration_unit=req.duration_unit,
                stake=float(req.stake),
                payout=float((proposal_snapshot or {}).get("payout") or buy_response_payload.get("payout") or 0.0),
                barrier=req.barrier,
                contract_type=(req.contract_type or (proposal_snapshot or {}).get("contract_type") or (req.type or "")).upper(),
                entry_price=float(buy_response_payload.get("buy_price") or buy_response_payload.get("price") or 0.0),
                exit_price=None,
                pnl=None,
                result=None,
                strategy_id=(req.extra or {}).get("strategy_id") if req.extra else None,
                features=req.extra or None,
                user_id=None,
                metadata={
                    "transaction_id": buy_response_payload.get("transaction_id"),
                },
                currency=req.currency,
                product_type=(req.type or "CALLPUT").upper(),
                deriv_contract_id=cid,
                status="open",
            ).to_mongo()
            base_doc["created_at"] = int(time.time())
            await db.contracts.insert_one(base_doc)
    except Exception as e:
        logger.warning(f"Mongo insert (contract) failed: {e}")

    return {
        "message": "purchased",
        "contract_id": buy_response_payload.get("contract_id"),
        "buy_price": buy_response_payload.get("buy_price") or buy_response_payload.get("price"),
        "payout": buy_response_payload.get("payout") or (proposal_snapshot or {}).get("payout"),
        "transaction_id": buy_response_payload.get("transaction_id"),
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

# Global stats instance
_global_stats = GlobalStats()

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

# Include the API router
app.include_router(api_router)