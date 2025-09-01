import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { Card, CardHeader, CardContent, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from "./ui/select";
import { Input } from "./ui/input";
import { Badge } from "./ui/badge";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

export default function MlPanel() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("deriv");
  const [symbol, setSymbol] = useState("R_100");
  const [timeframe, setTimeframe] = useState("3m");
  const [horizon, setHorizon] = useState(3);
  const [threshold, setThreshold] = useState(0.003);
  const [modelType, setModelType] = useState("rf");
  const [lastResult, setLastResult] = useState(null);

  // Async training job controls
  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null); // queued | running | done | error
  const [jobProgress, setJobProgress] = useState({ done: 0, total: 0 });
  const pollRef = useRef(null);
  const transientErrRef = useRef(0);

  // Post-calibration probability threshold (for Step 4 usage)
  const [probThreshold, setProbThreshold] = useState(() => {
    const v = localStorage.getItem("ml_prob_threshold");
    return v ? parseFloat(v) : 0.5;
  });

  const champion = status;
  const hasChampion = champion && champion.metrics;

  const improvement = useMemo(() => {
    if (!lastResult || !hasChampion) return null;
    const prevF1 = champion.metrics?.f1 ?? 0;
    const newF1 = lastResult.metrics?.f1 ?? 0;
    if (prevF1 === 0) return newF1 > 0 ? 100 : 0;
    return ((newF1 - prevF1) / prevF1) * 100;
  }, [lastResult, hasChampion, champion]);

  const refresh = async () => {
    try {
      const { data } = await axios.get(`${API}/ml/status`);
      setStatus(data);
    } catch {
      setStatus(null);
    }
  };

  useEffect(() => { refresh(); }, []);

  const isTransientError = (e) => {
    const sc = e?.response?.status;
    // Treat network/timeout and 5xx/408/504 as transient
    return !sc || [502,503,504,408].includes(sc);
  };

  // ---- Async training flow (uses /api/ml/train_async + /api/ml/job/{id}) ----
  const startAsyncTrain = async () => {
    setLoading(true);
    setLastResult(null);
    setJobId(null);
    setJobStatus(null);
    setJobProgress({ done: 0, total: 0 });
    transientErrRef.current = 0;
    try {
      const { data } = await axios.post(`${API}/ml/train_async`, null, {
        params: {
          source,
          symbol,
          timeframe,
          horizon,
          threshold,
          model_type: modelType,
          count: 20000,
          thresholds: "0.002,0.003,0.004,0.005",
          horizons: "1,3,5",
          class_weight: "balanced",
          calibrate: "sigmoid",
          objective: "precision",
        },
      });
      const jid = data?.job_id;
      if (!jid) {
        setLastResult({ error: "Falha ao iniciar job assíncrono (sem job_id)" });
        setLoading(false);
        return;
      }
      setJobId(jid);
      setJobStatus(data?.status || "queued");

      // Start polling
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const { data: j } = await axios.get(`${API}/ml/job/${jid}`);
          transientErrRef.current = 0; // reset on success
          setJobStatus(j?.status || null);
          if (j?.progress) {
            setJobProgress({ done: j.progress.done || 0, total: j.progress.total || 0 });
          }
          if (j?.status === "done") {
            clearInterval(pollRef.current);
            pollRef.current = null;
            const result = j?.result || null;
            setLastResult(result);
            await refresh();
            setLoading(false);
          } else if (j?.status === "error") {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setLastResult({ error: j?.error || "Erro no job ML" });
            setLoading(false);
          }
        } catch (e) {
          // Handle transient errors without stopping the polling immediately
          if (isTransientError(e) && transientErrRef.current < 5) {
            transientErrRef.current += 1;
            // keep polling; show no terminal error
            return;
          }
          // Non-transient or too many consecutive errors: stop and show error
          try { clearInterval(pollRef.current); } catch {}
          pollRef.current = null;
          setLastResult({ error: e?.response?.data?.detail || e.message || String(e) });
          setLoading(false);
        }
      }, 2500);
    } catch (e) {
      setLastResult({ error: e?.response?.data?.detail || e.message || String(e) });
      setLoading(false);
    }
  };

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const saveProbThreshold = () => {
    const v = Math.max(0.01, Math.min(0.99, Number(probThreshold || 0.5)));
    setProbThreshold(v);
    localStorage.setItem("ml_prob_threshold", String(v));
  };

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Modelo atual (ML)</span>
          {hasChampion ? (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <Badge variant="secondary">v: {champion.model_id}</Badge>
              <span className="opacity-80">F1: {champion.metrics?.f1?.toFixed?.(3)}</span>
              <span className="opacity-80">Precision: {champion.metrics?.precision?.toFixed?.(3)}</span>
              <span className="opacity-80">DD: {champion.backtest?.max_drawdown?.toFixed?.(3)}</span>
              <span className="opacity-60">{champion.updated_at ? new Date(champion.updated_at).toLocaleString() : ""}</span>
            </div>
          ) : (
            <span className="text-sm opacity-70">Sem campeão ainda</span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-3 items-center">
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Fonte</span>
            <Select value={source} onValueChange={setSource}>
              <SelectTrigger className="w-40"><SelectValue placeholder="Fonte"/></SelectTrigger>
              <SelectContent>
                <SelectItem value="mongo">Mongo</SelectItem>
                <SelectItem value="file">CSV (/data/ml/ohlcv.csv)</SelectItem>
                <SelectItem value="deriv">Deriv (baixar candles online)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Símbolo</span>
            <Input className="w-32" value={symbol} onChange={(e)=>setSymbol(e.target.value)} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Timeframe</span>
            <Input className="w-24" value={timeframe} onChange={(e)=>setTimeframe(e.target.value)} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Horizon</span>
            <Input className="w-24" type="number" value={horizon} onChange={(e)=>setHorizon(Number(e.target.value||3))} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Threshold (target)</span>
            <Input className="w-28" type="number" step="0.0001" value={threshold} onChange={(e)=>setThreshold(Number(e.target.value||0.003))} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Modelo</span>
            <Select value={modelType} onValueChange={setModelType}>
              <SelectTrigger className="w-36"><SelectValue placeholder="Modelo"/></SelectTrigger>
              <SelectContent>
                <SelectItem value="rf">RandomForest</SelectItem>
                <SelectItem value="dt">DecisionTree</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Button disabled={loading} onClick={startAsyncTrain}>
              {loading ? "Treinando..." : jobId ? "Reiniciar treino" : "Treinar agora"}
            </Button>
          </div>
        </div>

        {/* Prob Threshold Control (post-calibration) */}
        <div className="mt-2 p-3 rounded-md border border-amber-500/30 bg-amber-500/10 text-amber-100">
          <div className="font-medium mb-1">Ajuste de prob_threshold (pós-calibração)</div>
          <div className="text-xs opacity-90 mb-2">
            Use este controle para travar uma precisão mínima exigindo probabilidade calibrada &gt;= threshold na decisão. O valor é salvo localmente e será usado na Passo 4 (quando o modelo for plugado na estratégia).
          </div>
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={0.5}
              max={0.9}
              step={0.01}
              value={probThreshold}
              onChange={(e)=>setProbThreshold(parseFloat(e.target.value))}
              className="flex-1"
            />
            <Input
              className="w-24"
              type="number"
              step="0.01"
              min={0.01}
              max={0.99}
              value={probThreshold}
              onChange={(e)=>setProbThreshold(parseFloat(e.target.value || 0.5))}
            />
            <Button variant="secondary" onClick={saveProbThreshold}>Salvar</Button>
            <Badge variant="secondary">mín. precisão via proba: {probThreshold.toFixed(2)}</Badge>
          </div>
        </div>

        {/* Async job status */}
        {jobId && (
          <div className="rounded-md border border-sky-500/30 bg-sky-500/10 text-sky-100 p-3 text-sm">
            <div className="font-medium mb-1">Job ML em execução</div>
            <div>job_id: {jobId}</div>
            <div>status: {jobStatus || "-"}</div>
            {jobProgress?.total ? (
              <div className="mt-1">progresso: {jobProgress.done}/{jobProgress.total} combos</div>
            ) : null}
            {loading && transientErrRef.current > 0 && (
              <div className="mt-1 text-xs opacity-80">Algumas leituras falharam ({transientErrRef.current}). Tentando novamente…</div>
            )}
          </div>
        )}

        {/* Resultado do treino */}
        {lastResult && (
          <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-200 p-3 text-sm">
            <div className="font-medium mb-1">Resultado do treino (melhor combinação)</div>
            {jobStatus && (
              <div className="mb-2 text-xs opacity-90">Status atual: {jobStatus}</div>
            )}
            {lastResult.error ? (
              <div className="text-red-300">Erro: {String(lastResult.error)}</div>
            ) : (
              <>
                <div>model_id: {lastResult.model_id}</div>
                <div>
                  F1: {lastResult.metrics?.f1?.toFixed?.(3)} • Precision: {lastResult.metrics?.precision?.toFixed?.(3)} • Recall: {lastResult.metrics?.recall?.toFixed?.(3)}
                </div>
                <div>
                  Backtest: EQ={lastResult.backtest?.equity_final?.toFixed?.(3)} • DD={lastResult.backtest?.max_drawdown?.toFixed?.(3)} • EV/trade={lastResult.backtest?.ev_per_trade?.toFixed?.(3)}
                </div>
                <div>Promoção: {String(lastResult.promoted)}</div>
                {improvement !== null && (
                  <div className="mt-1 opacity-90">Melhora vs campeão: {improvement.toFixed(1)}%</div>
                )}
                {/* Grid detalhado */}
                {Array.isArray(lastResult.grid) && lastResult.grid.length > 0 && (
                  <div className="mt-3">
                    <div className="font-medium mb-1">Grid completo</div>
                    <div className="rounded-md overflow-hidden border border-emerald-500/20">
                      <div className="grid grid-cols-6 text-xs bg-emerald-900/30">
                        <div className="px-2 py-1">horizon</div>
                        <div className="px-2 py-1">threshold</div>
                        <div className="px-2 py-1">precision</div>
                        <div className="px-2 py-1">ev/trade</div>
                        <div className="px-2 py-1">trades/dia</div>
                        <div className="px-2 py-1">model_id</div>
                      </div>
                      <div className="max-h-72 overflow-auto">
                        {lastResult.grid.map((row, idx) => (
                          <div key={idx} className="grid grid-cols-6 text-xs border-t border-emerald-500/10">
                            <div className="px-2 py-1">{row.horizon}</div>
                            <div className="px-2 py-1">{Number(row.threshold).toFixed(3)}</div>
                            <div className="px-2 py-1">{row.precision != null ? Number(row.precision).toFixed(3) : "-"}</div>
                            <div className="px-2 py-1">{row.ev_per_trade != null ? Number(row.ev_per_trade).toFixed(3) : "-"}</div>
                            <div className="px-2 py-1">{row.trades_per_day != null ? Number(row.trades_per_day).toFixed(2) : "-"}</div>
                            <div className="px-2 py-1 truncate" title={row.model_id}>{row.model_id}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        <div className="text-xs opacity-70">
          Agendador semanal: serviço "trainer" roda 1x/semana automaticamente com defaults (R_100, 3m, horizon=3, threshold=0.003). Para CSV local, monte /data/ml/ohlcv.csv.
        </div>
      </CardContent>
    </Card>
  );
}