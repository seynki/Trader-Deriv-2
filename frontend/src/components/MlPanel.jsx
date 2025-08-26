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
  const [source, setSource] = useState("mongo");
  const [symbol, setSymbol] = useState("R_100");
  const [timeframe, setTimeframe] = useState("3m");
  const [horizon, setHorizon] = useState(3);
  const [threshold, setThreshold] = useState(0.003);
  const [modelType, setModelType] = useState("rf");
  const [lastResult, setLastResult] = useState(null);

  const champion = status;
  const hasChampion = champion && champion.metrics;

  const improvement = useMemo(() => {
    if (!lastResult || !hasChampion) return null;
    const prevF1 = (champion.metrics?.f1 ?? 0);
    const newF1 = (lastResult.metrics?.f1 ?? 0);
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

  const runTrain = async () => {
    setLoading(true);
    try {
      const { data } = await axios.post(`${API}/ml/train`, null, { params: { source, symbol, timeframe, horizon, threshold, model_type: modelType } });
      setLastResult(data);
      await refresh();
    } catch (e) {
      setLastResult({ error: e?.response?.data?.detail || e.message || String(e) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Modelo atual (ML)</span>
          {hasChampion ? (
            <div className="flex items-center gap-3 text-sm">
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
            <span className="text-sm opacity-80">Threshold</span>
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
          <div className="ml-auto">
            <Button disabled={loading} onClick={runTrain}>Treinar agora</Button>
          </div>
        </div>

        {lastResult && (
          <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-200 p-3 text-sm whitespace-pre-wrap">
            <div className="font-medium mb-1">Resultado do treino ad-hoc</div>
            {lastResult.error ? (
              <div className="text-red-300">Erro: {String(lastResult.error)}</div>
            ) : (
              <>
                <div>model_id: {lastResult.model_id}</div>
                <div>F1: {lastResult.metrics?.f1?.toFixed?.(3)} • Precision: {lastResult.metrics?.precision?.toFixed?.(3)} • Recall: {lastResult.metrics?.recall?.toFixed?.(3)}</div>
                <div>Backtest: EQ={lastResult.backtest?.equity_final?.toFixed?.(3)} • DD={lastResult.backtest?.max_drawdown?.toFixed?.(3)}</div>
                <div>Promoção: {String(lastResult.promoted)}</div>
                {improvement !== null && (
                  <div className="mt-1 opacity-90">Melhora vs campeão: {improvement.toFixed(1)}%</div>
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
