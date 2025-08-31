from typing import Any, Dict, List, Optional
import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

# Import the main FastAPI app and Deriv/Mongo singletons from the stable backup module
from server_backup import app, _deriv, db  # type: ignore

from fastapi import APIRouter, HTTPException
from joblib import load as joblib_load
from sklearn.tree import DecisionTreeClassifier, export_text
import pandas as pd

import ml_utils

logger = logging.getLogger(__name__)

# ---------------------- Helpers used by ML endpoints ----------------------

def _granularity_from_timeframe(tf: str) -> int:
    tf = (tf or "").strip().lower()
    try:
        if tf.endswith("m"):
            return int(float(tf[:-1]) * 60)
        if tf.endswith("h"):
            return int(float(tf[:-1]) * 3600)
        if tf.endswith("t"):
            # ticks-based timeframe is not applicable to candles; default to 60s
            return 60
        # if pure number, treat as minutes
        if tf.isdigit():
            return int(tf) * 60
    except Exception:
        pass
    # default 3m
    return 180


async def _fetch_candles_paginated(symbol: str, granularity: int, total: int) -> List[Dict[str, Any]]:
    """Fetch up to `total` candles by paging backwards using Deriv ticks_history.
    Returns candles sorted ascending by epoch.
    """
    if not _deriv.connected:
        raise HTTPException(status_code=503, detail="Deriv not connected")

    out: List[Dict[str, Any]] = []
    remaining = int(total)
    end_marker: Optional[int] = None  # epoch to page before

    while remaining > 0:
        chunk = min(5000, remaining)  # Deriv typically allows large chunks; keep conservative
        req_id = int(time.time() * 1000)
        fut = asyncio.get_running_loop().create_future()
        _deriv.pending[req_id] = fut
        payload: Dict[str, Any] = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": chunk,
            "end": end_marker if end_marker is not None else "latest",
            "start": 1,
            "style": "candles",
            "granularity": int(granularity),
            "req_id": req_id,
        }
        await _deriv._send(payload)
        try:
            data = await asyncio.wait_for(fut, timeout=20)
        except asyncio.TimeoutError:
            _deriv.pending.pop(req_id, None)
            raise HTTPException(status_code=504, detail="Timeout waiting for candles")
        if data.get("error"):
            raise HTTPException(status_code=400, detail=data["error"].get("message", "history error"))
        candles = data.get("candles") or []
        if not candles:
            break
        # Page backwards: next 'end' is before the first candle epoch of this batch
        try:
            first_epoch = int(candles[0].get("epoch"))
            end_marker = first_epoch - 1
        except Exception:
            break
        # Keep ascending order overall: prepend older candles
        if not out:
            out = candles
        else:
            out = candles + out
        remaining = total - len(out)
        if remaining <= 0:
            break
    # Trim to requested total (keep the most recent total items while preserving ascending order)
    if len(out) > total:
        out = out[-total:]
    return out


# ---------------------- ML endpoints ----------------------
ml_router = APIRouter(prefix="/api/ml")


@ml_router.get("/status")
async def ml_status():
    meta = ml_utils.load_champion()
    return meta or {"message": "no champion"}


def _parse_csv_or_raise(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise HTTPException(status_code=400, detail="Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")
    return pd.read_csv(path)


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
                         objective: str,
                         min_prob: Optional[float]):
    try:
        _update_job(job_id, {"status": "running", "started_at": int(time.time())})
        df: Optional[pd.DataFrame] = None
        tf = timeframe
        if source == "mongo" and db is not None:
            try:
                recs = await db.candles.find({"symbol": symbol, "timeframe": tf}).sort("time", 1).to_list(50000)
                if recs:
                    df = pd.DataFrame(recs)
            except Exception as e:
                logger.warning(f"ML load mongo failed: {e}")
        elif source == "file":
            df = _parse_csv_or_raise(Path("/data/ml/ohlcv.csv"))
        elif source == "deriv":
            if not _deriv.connected:
                raise HTTPException(status_code=503, detail="Deriv not connected")
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
            df = _parse_csv_or_raise(Path("/data/ml/ohlcv.csv"))

        if df is None or df.empty:
            raise HTTPException(status_code=400, detail="Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")

        ren = {c: c.lower() for c in df.columns}
        df = df.rename(columns=ren)
        for c in ["open", "high", "low", "close", "volume"]:
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
                        df[["open", "high", "low", "close", "volume"]].copy(),
                        horizon=h,
                        threshold=float(tval),
                        model_type=model_type,
                        save_prefix=f"{symbol}_{tf}_h{h}_th{tval:.3f}",
                        class_weight=class_weight,
                        calibrate=calibrate,
                        payout_ratio=0.95,
                        candles_per_day=float(candles_per_day),
                        objective=objective,
                        decision_threshold_prob=min_prob,
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
                    logger.warning(f"Grid combo failed h={h} t={tval}: {e}")
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
                "label_rate": r.get("metrics", {}).get("label_rate"),
                "min_prob": r.get("metrics", {}).get("min_prob"),
            } for r in results],
            "rows": int(len(df)),
            "granularity": gran,
            "symbol": symbol,
            "timeframe": tf,
            "used_min_prob": float(min_prob) if min_prob is not None else None,
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
    min_prob: Optional[float] = 0.5,
):
    job_id = str(uuid.uuid4())
    _ml_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": int(time.time()),
        "params": {"source": source, "symbol": symbol, "timeframe": timeframe, "count": count,
                    "thresholds": thresholds, "horizons": horizons, "model_type": model_type,
                    "class_weight": class_weight, "calibrate": calibrate, "objective": objective,
                    "min_prob": min_prob}
    }
    asyncio.create_task(_run_train_job(job_id, source, symbol, timeframe, horizon, threshold, model_type, count, thresholds, horizons, class_weight, calibrate, objective, min_prob))
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
    min_prob: Optional[float] = 0.5,
):
    df: Optional[pd.DataFrame] = None
    tf = timeframe
    if source == "mongo" and db is not None:
        try:
            recs = await db.candles.find({"symbol": symbol, "timeframe": tf}).sort("time", 1).to_list(50000)
            if recs:
                df = pd.DataFrame(recs)
        except Exception as e:
            logger.warning(f"ML load mongo failed: {e}")
    elif source == "file":
        df = _parse_csv_or_raise(Path("/data/ml/ohlcv.csv"))
    elif source == "deriv":
        if not _deriv.connected:
            raise HTTPException(status_code=503, detail="Deriv not connected")
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
        df = _parse_csv_or_raise(Path("/data/ml/ohlcv.csv"))

    if df is None or df.empty:
        raise HTTPException(status_code=400, detail="Sem dados: Mongo vazio e /data/ml/ohlcv.csv não existe")

    ren = {c: c.lower() for c in df.columns}
    df = df.rename(columns=ren)
    for c in ["open", "high", "low", "close", "volume"]:
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

    for h in hor_list:
        for tval in thr_list:
            try:
                out = ml_utils.train_and_maybe_promote(
                    df[["open", "high", "low", "close", "volume"]].copy(),
                    horizon=h,
                    threshold=float(tval),
                    model_type=model_type,
                    save_prefix=f"{symbol}_{tf}_h{h}_th{tval:.3f}",
                    class_weight=class_weight,
                    calibrate=calibrate,
                    payout_ratio=0.95,
                    candles_per_day=float(candles_per_day),
                    objective=objective,
                    decision_threshold_prob=min_prob,
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
                logger.warning(f"Grid combo failed h={h} t={tval}: {e}")
                continue

    if not best:
        raise HTTPException(status_code=400, detail="Falha ao treinar qualquer combinação")

    return {
        **best,
        "grid": [{
            "model_id": r.get("model_id"),
            "horizon": r.get("horizon"),
            "threshold": r.get("threshold"),
            "precision": r.get("metrics", {}).get("precision"),
            "ev_per_trade": r.get("backtest", {}).get("ev_per_trade"),
            "trades_per_day": r.get("metrics", {}).get("trades_per_day"),
            "label_rate": r.get("metrics", {}).get("label_rate"),
            "min_prob": r.get("metrics", {}).get("min_prob"),
        } for r in results],
        "rows": int(len(df)),
        "granularity": gran,
        "used_min_prob": float(min_prob) if min_prob is not None else None,
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


# Register router
app.include_router(ml_router)