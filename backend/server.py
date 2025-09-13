@@
-from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException
+from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Body
@@
 import ml_utils
 import ml_trainer
 import online_learning
+import io
+from typing import Optional as _Optional
+import river_online_model
@@
 api_router = APIRouter(prefix="/api")
@@
 # ------------------- Online Learning API -----------------------------
@@
     return {
         "models": list(models_status.keys()),
         "details": models_status,
     }
@@
     return {"status": "initialized", "models": _online_manager.list_online_models()}
@@
     return {"predictions": proba_up.tolist(), "classes": [0, 1]}
+
+# ------------------- River Online (OHLCV online learning) -----------------------------
+
+from pydantic import BaseModel as _BaseModel
+
+
+class RiverPredictCandle(_BaseModel):
+    datetime: _Optional[str] = None
+    open: float
+    high: float
+    low: float
+    close: float
+    volume: float
+
+
+_river_model: _Optional[river_online_model.RiverOnlineCandleModel] = None
+
+
+def _get_river_model() -> river_online_model.RiverOnlineCandleModel:
+    global _river_model
+    if _river_model is not None:
+        return _river_model
+    # Try to load from disk else create new
+    try:
+        _river_model = river_online_model.RiverOnlineCandleModel.load()
+    except Exception:
+        _river_model = river_online_model.RiverOnlineCandleModel()
+    return _river_model
+
+
+@api_router.get("/ml/river/status")
+async def river_status():
+    try:
+        m = _get_river_model()
+        return {
+            "initialized": True,
+            "samples": getattr(m, "sample_count", 0),
+            "acc": float(m.metric_acc.get()) if getattr(m, "sample_count", 0) &gt; 0 else None,
+            "logloss": float(m.metric_logloss.get()) if getattr(m, "sample_count", 0) &gt; 0 else None,
+            "model_path": river_online_model.MODEL_SAVE_PATH,
+        }
+    except Exception as e:
+        return {"initialized": False, "error": str(e)}
+
+
+@api_router.post("/ml/river/train_csv")
+async def river_train_csv(csv_text: str = Body(..., embed=True)):
+    """Treina/Atualiza o modelo online processando um CSV (texto)."""
+    try:
+        df = pd.read_csv(io.StringIO(csv_text))
+        result = river_online_model.run_on_dataframe(df, _get_river_model())
+        # Persist updated singleton
+        result["model"].save(river_online_model.MODEL_SAVE_PATH)
+        return result["summary"]
+    except Exception as e:
+        raise HTTPException(status_code=400, detail=f"Erro no processamento do CSV: {e}")
+
+
+@api_router.post("/ml/river/train_csv_upload")
+async def river_train_csv_upload(file: UploadFile = File(...)):
+    """Treina/Atualiza o modelo online enviando arquivo CSV (multipart/form-data)."""
+    try:
+        content = (await file.read()).decode("utf-8")
+        df = pd.read_csv(io.StringIO(content))
+        result = river_online_model.run_on_dataframe(df, _get_river_model())
+        # Persist updated singleton
+        result["model"].save(river_online_model.MODEL_SAVE_PATH)
+        return result["summary"]
+    except Exception as e:
+        raise HTTPException(status_code=400, detail=f"Erro no upload do CSV: {e}")
+
+
+@api_router.post("/ml/river/predict")
+async def river_predict(candle: RiverPredictCandle):
+    """Predição online para um candle (sem atualizar o modelo)."""
+    try:
+        m = _get_river_model()
+        info = m.predict_and_update(
+            candle.datetime or datetime.utcnow().isoformat(),
+            candle.open,
+            candle.high,
+            candle.low,
+            candle.close,
+            candle.volume,
+            next_close=None,
+        )
+        # not learning since next_close is None
+        return {
+            "prob_up": info["prob_up"],
+            "pred_class": info["pred_class"],
+            "signal": info["signal"],
+            "features": info["features"],
+        }
+    except Exception as e:
+        raise HTTPException(status_code=400, detail=f"Erro na predição: {e}")
+
+
+class RiverDecideTradeRequest(_BaseModel):
+    symbol: str = "R_100"
+    duration: int = 5
+    duration_unit: str = "t"
+    stake: float = 1.0
+    currency: str = "USD"
+    dry_run: bool = True
+    candle: RiverPredictCandle
+
+
+@api_router.post("/ml/river/decide_trade")
+async def river_decide_trade(req: RiverDecideTradeRequest):
+    """Decide LONG/SHORT e opcionalmente envia ordem real via Deriv (CALL/PUT) quando dry_run=False.
+    Requer DERIV_API_TOKEN configurado e WS conectado para execução real.
+    """
+    m = _get_river_model()
+    info = m.predict_and_update(
+        req.candle.datetime or datetime.utcnow().isoformat(),
+        req.candle.open,
+        req.candle.high,
+        req.candle.low,
+        req.candle.close,
+        req.candle.volume,
+        next_close=None,
+    )
+    action = "CALL" if info["pred_class"] == 1 else "PUT"
+
+    if req.dry_run:
+        return {
+            "decision": action,
+            "prob_up": info["prob_up"],
+            "signal": info["signal"],
+            "dry_run": True,
+        }
+
+    # Execução real (usa endpoint existente internamente)
+    # Monta BuyRequest e chama deriv_buy
+    buy_payload = BuyRequest(
+        symbol=req.symbol,
+        type="CALLPUT",
+        contract_type=action,
+        duration=req.duration,
+        duration_unit=req.duration_unit,
+        stake=req.stake,
+        currency=req.currency,
+    )
+
+    try:
+        result = await deriv_buy(buy_payload)
+        return {
+            "decision": action,
+            "prob_up": info["prob_up"],
+            "executed": True,
+            "order_result": result,
+        }
+    except HTTPException as he:
+        # Propaga erro amigável
+        raise he
+    except Exception as e:
+        raise HTTPException(status_code=500, detail=f"Falha ao executar ordem Deriv: {e}")