@@
 ml_router = APIRouter(prefix="/api/ml")
@@
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
-                         objective: str):
+                         objective: str,
+                         min_prob: Optional[float]):
@@
-        for h in hor_list:
+        for h in hor_list:
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
-                        objective=objective,
+                        objective=objective,
+                        decision_threshold_prob=min_prob,
                     )
                     out.update({"horizon": h, "threshold": float(tval)})
                     results.append(out)
                     cur_prec = out.get("metrics", {}).get("precision", 0.0) or 0.0
                     cur_ev = out.get("backtest", {}).get("ev_per_trade", 0.0) or 0.0
                     cur_tpd = out.get("metrics", {}).get("trades_per_day", 0.0) or 0.0
@@
-        result = {
+        result = {
             **best,
             "grid": [{
                 "model_id": r.get("model_id"),
                 "horizon": r.get("horizon"),
                 "threshold": r.get("threshold"),
                 "precision": r.get("metrics", {}).get("precision"),
                 "ev_per_trade": r.get("backtest", {}).get("ev_per_trade"),
                 "trades_per_day": r.get("metrics", {}).get("trades_per_day"),
+                "label_rate": r.get("metrics", {}).get("label_rate"),
+                "min_prob": r.get("metrics", {}).get("min_prob"),
             } for r in results],
             "rows": int(len(df)),
             "granularity": gran,
             "symbol": symbol,
             "timeframe": tf,
+            "used_min_prob": float(min_prob) if min_prob is not None else None,
         }
         _update_job(job_id, {"status": "done", "result": result})
@@
-@ml_router.post("/train_async")
+@ml_router.post("/train_async")
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
-    objective: str = "precision",
+    objective: str = "precision",
+    min_prob: Optional[float] = 0.5,
 ):
@@
-    _ml_jobs[job_id] = {
+    _ml_jobs[job_id] = {
         "job_id": job_id,
         "status": "queued",
         "created_at": int(time.time()),
         "params": {"source": source, "symbol": symbol, "timeframe": timeframe, "count": count,
                     "thresholds": thresholds, "horizons": horizons, "model_type": model_type,
-                    "class_weight": class_weight, "calibrate": calibrate, "objective": objective}
+                    "class_weight": class_weight, "calibrate": calibrate, "objective": objective,
+                    "min_prob": min_prob}
     }
-    asyncio.create_task(_run_train_job(job_id, source, symbol, timeframe, horizon, threshold, model_type, count, thresholds, horizons, class_weight, calibrate, objective))
+    asyncio.create_task(_run_train_job(job_id, source, symbol, timeframe, horizon, threshold, model_type, count, thresholds, horizons, class_weight, calibrate, objective, min_prob))
     return {"job_id": job_id, "status": "queued"}
@@
-@ml_router.post("/train")
+@ml_router.post("/train")
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
-    objective: str = "precision",
+    objective: str = "precision",
+    min_prob: Optional[float] = 0.5,
 ):
@@
-    for h in hor_list:
+    for h in hor_list:
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
-                    objective=objective,
+                    objective=objective,
+                    decision_threshold_prob=min_prob,
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
@@
-    return {
+    return {
         **best,
         "grid": [{
             "model_id": r.get("model_id"),
             "horizon": r.get("horizon"),
             "threshold": r.get("threshold"),
             "precision": r.get("metrics", {}).get("precision"),
             "ev_per_trade": r.get("backtest", {}).get("ev_per_trade"),
             "trades_per_day": r.get("metrics", {}).get("trades_per_day"),
+            "label_rate": r.get("metrics", {}).get("label_rate"),
+            "min_prob": r.get("metrics", {}).get("min_prob"),
         } for r in results],
         "rows": int(len(df)),
         "granularity": gran,
+        "used_min_prob": float(min_prob) if min_prob is not None else None,
     }
@@
 app.include_router(ml_router)
@@