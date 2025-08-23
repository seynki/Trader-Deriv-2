from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime
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
async def deriv_contracts_for(symbol: str, currency: str = "USD"):
    # TTL cache
    now = time.time()
    cached = _contracts_cache.get(symbol)
    if cached and now - cached.get("_ts", 0) < _CONTRACTS_TTL:
        return cached["data"]

    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")
    req_id = int(time.time() * 1000)
    fut = asyncio.get_running_loop().create_future()
    _deriv.pending[req_id] = fut
    await _deriv._send({
        "contracts_for": symbol,
        "currency": currency,
        "product_type": "basic",
        "req_id": req_id,
    })
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
    }

    _contracts_cache[symbol] = {"_ts": now, "data": result}
    return result

# ---------------- Proposal/Buy -----------------

def build_proposal_payload(req: BuyRequest) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "proposal": 1,
        "amount": float(req.stake),
        "basis": "stake",
        "currency": req.currency,
        "symbol": req.symbol,
    }
    t = (req.type or "CALLPUT").upper()
    # Default contract_type fallback
    ct = (req.contract_type or "CALL").upper() if t == "CALLPUT" else (req.contract_type.upper() if req.contract_type else None)

    if t == "CALLPUT":
        base.update({
            "contract_type": ct,
            "duration": int(req.duration or 5),
            "duration_unit": req.duration_unit or "t",
        })
        if req.barrier:
            base["barrier"] = req.barrier
    elif t == "ACCUMULATOR":
        base.update({
            "contract_type": "ACCUMULATOR",
        })
        if req.limit_order:
            base["limit_order"] = req.limit_order
    elif t == "TURBOS":
        base.update({
            "contract_type": (ct or "TURBOSLONG"),
            "strike": req.strike or "ATM",
        })
    elif t == "MULTIPLIERS":
        base.update({
            "contract_type": (ct or "MULTUP"),
        })
        if req.multiplier:
            base["multiplier"] = int(req.multiplier)
        if req.limit_order:
            base["limit_order"] = req.limit_order
    else:
        # Fallback: send as-is plus extras
        if ct:
            base["contract_type"] = ct
        if req.duration:
            base["duration"] = int(req.duration)
        if req.duration_unit:
            base["duration_unit"] = req.duration_unit
        if req.barrier:
            base["barrier"] = req.barrier

    if req.extra:
        base.update(req.extra)
    return base

@api_router.post("/deriv/proposal")
async def deriv_proposal(req: BuyRequest):
    """Get a pricing proposal for a contract. Supports CALLPUT, ACCUMULATOR, TURBOS, MULTIPLIERS."""
    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")
    req_id = int(time.time() * 1000)
    payload = build_proposal_payload(req)
    payload["req_id"] = req_id

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
    # 1) proposal
    proposal = await deriv_proposal(req)
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
        "buy_price": b.get("buy_price"),
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

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)