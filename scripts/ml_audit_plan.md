ML Audit Plan (Deriv Volatility 10 Index)

Goal: Elevar winrate de forma sustentável com validação temporal e foco em expectancy.

Fase 0 — Baseline automatizada (sem mudanças de código)
- Medir hoje (conta DEMO) para R_10 em:
  • 3 ticks (simulado com StrategyRunner paper trade duration=3)
  • 5 minutos (granularity=300, candles=4000)
- Endpoints a usar:
  • GET /api/deriv/status
  • POST /api/strategy/start (mode=paper, symbol=R_10, granularity=300, duration=5t, stake=1, ml_gate=true)
  • GET /api/strategy/status (monitorar 90s)
  • POST /api/strategy/stop
  • POST /api/ml/engine/train (symbol=R_10, timeframe=5m, count=4000, horizon=3, seq_len=32, use_transformer=false)
  • POST /api/ml/engine/predict (symbol=R_10, count=200)
  • POST /api/strategy/river/backtest (symbol=R_10, timeframe=1m e 5m, lookback=1500)
- Métricas chave: win_rate, expected_value, trades/dia, drawdown, confiança ML gate.

Fase 1 — Diagnóstico ML
- Overfitting check: comparar metrics no walk-forward vs full fit; análise de variância entre splits.
- Subfitting check: precision/EV baixos em ambos treino e teste; checar importância de features no LGB.
- Data leakage guard: usar walk-forward (já existe no ml_engine_backtest) + validação temporal.

Fase 2 — Feature Engineering
- Reduzir dimensionalidade: selecionar top-K por ganho de informação do LGB (SHAP opcional futuro).
- Criar features robustas: volatilidade local (ATR normalizado), lags e retornos multi-horizonte, regime filter (ADX regime), horário (seno/cosseno), price range compressão.
- Normalização: padronizar inputs para Transformer/LGB conforme necessário.

Fase 3 — Modelos e Tuning
- Experimentar LGB-only vs ensemble; ajustar min_conf para gate (0.4–0.6) via grid curto.
- Class balance: aplicar class_weight balanced no LGB quando alvo for direção próxima.
- Bayesian/Random search leve: 20 iterações.

Fase 4 — Estratégia
- Otimizar river_threshold via /strategy/river/backtest e aplicar config via /strategy/river/config.
- Gestão de risco: Kelly fraction limitada, stop após 3 perdas, cooldown adaptativo por regime.

Entrega incremental
- Primeiro rodar Fase 0 e apresentar relatório. Em seguida implementar Fases 2–4 conforme resultados.
