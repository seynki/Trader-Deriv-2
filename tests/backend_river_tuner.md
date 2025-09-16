Test Plan for River Threshold Auto-Tuning and New Risk Rules

Scope
- Validate new endpoints and modifications:
  1) POST /api/strategy/river/backtest (alias) and /api/strategy/river/backtest_run
  2) POST /api/strategy/river/config
  3) Strategy changes: ADX regime gate + dynamic confidence, cooldown adaptativo, no-trade window (vol spike), ml_prob_threshold default=0.6

Preconditions
- Backend running via supervisor on 0.0.0.0:8001 (ingress adds /api)
- backend/.env has DERIV_APP_ID and DERIV_API_TOKEN (DEMO)

Tests
1) Backtest basic
- POST /api/strategy/river/backtest with {symbol:R_10,timeframe:"1m",lookback_candles:1200,thresholds:[0.5,0.52,0.54,0.56,0.58,0.6,0.62,0.64,0.66,0.68,0.7,0.72,0.74,0.76,0.78,0.8]}
- Expect 200, fields: results[], best_threshold, current_threshold, recommendation.score
- Validate results[].expected_value and .max_drawdown present

2) Apply best threshold
- Capture best_threshold from #1; POST /api/strategy/river/config {river_threshold: best}
- Expect success true and new_threshold applied

3) Strategy regime rules (paper)
- POST /api/strategy/start with body {symbol:"R_10", granularity:60, candle_len:200, duration:5, stake:1, ml_gate:true, ml_prob_threshold:0.6, mode:"paper"}
- Poll GET /api/strategy/status for 30-60s
- Validate last_reason updates; ensure when ADX < 20 there are periods with no trades (blocked). When high volatility spike occurs, no-trade window message appears.

4) Cooldown after losses
- During run, if 3 consecutive losses occur (may simulate by running longer), observe last_reason shows cooldown message and trade frequency drops temporarily.

Notes
- Do not call live buy in tests
- If candles insufficient, reduce lookback_candles
