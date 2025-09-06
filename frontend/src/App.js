import React, { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import axios from "axios";
// shadcn/ui components
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./components/ui/tabs";
import { Card, CardHeader, CardContent, CardTitle } from "./components/ui/card";
import { Button } from "./components/ui/button";
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from "./components/ui/select";
import { Input } from "./components/ui/input";
import { Switch } from "./components/ui/switch";
import { Badge } from "./components/ui/badge";
import { useToast } from "./hooks/use-toast";
import { ToastProvider } from "./components/ui/toast";
import { Rocket, ActivitySquare, Play, Square } from "lucide-react";
import MlPanel from "./components/MlPanel";

function backendBase() {
  const env = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/+$/, "");
  try {
    const u = new URL(env);
    // If env points to localhost but page is not localhost, prefer same-origin (ingress) to avoid 404s
    if ((u.hostname === "localhost" || u.hostname === "127.0.0.1") && typeof window !== "undefined" && window.location && window.location.hostname !== "localhost") {
      return window.location.origin.replace(/\/+$/, "");
    }
    return env;
  } catch {
    if (typeof window !== "undefined" && window.location) return window.location.origin.replace(/\/+$/, "");
    return "";
  }
}
const BACKEND_BASE = backendBase();
const API = BACKEND_BASE.endsWith("/api") ? BACKEND_BASE : `${BACKEND_BASE}/api`;

const defaultSymbols = ["CRYETHUSD", "FRXUSDJPY", "US30"];
const derivedSymbols = [
  // 1-second variants
  { value: "1HZ10V", label: "Volatility 10 (1s)" },
  { value: "1HZ25V", label: "Volatility 25 (1s)" },
  { value: "1HZ50V", label: "Volatility 50 (1s)" },
  { value: "1HZ75V", label: "Volatility 75 (1s)" },
  { value: "1HZ100V", label: "Volatility 100 (1s)" },
  // standard volatility indices
  { value: "R_10", label: "Volatility 10 Index" },
  { value: "R_25", label: "Volatility 25 Index" },
  { value: "R_50", label: "Volatility 50 Index" },
  { value: "R_75", label: "Volatility 75 Index" },
  { value: "R_100", label: "Volatility 100 Index" },
];

function wsHostFromEnv() {
  try {
    const u = new URL(BACKEND_BASE);
    const isSecure = u.protocol === "https:";
    return { base: `${isSecure ? "wss" : "ws"}://${u.host}` };
  } catch {
    return { base: "" }; // relative WS paths
  }
}

function wsTicksUrl() {
  const { base } = wsHostFromEnv();
  return base ? `${base}/api/ws/ticks` : `/api/ws/ticks`;
}
function wsContractUrl(contractId) {
  const { base } = wsHostFromEnv();
  return base ? `${base}/api/ws/contract/${contractId}` : `/api/ws/contract/${contractId}`;
}

function useDerivTicks(symbols) {
  const [ticks, setTicks] = useState({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const retryRef = useRef(null);
  const stopRef = useRef(false);

  useEffect(() => {
    if (!symbols || symbols.length === 0) return;
    stopRef.current = false;

    const connect = () => {
      if (stopRef.current) return;
      const url = wsTicksUrl();
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        try { ws.send(JSON.stringify({ symbols })); } catch {}
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "tick") {
            setTicks((prev) => ({ ...prev, [msg.symbol]: msg }));
          }
        } catch {}
      };
      const scheduleRetry = () => {
        setConnected(false);
        if (!stopRef.current) {
          clearTimeout(retryRef.current);
          retryRef.current = setTimeout(connect, 1500);
        }
      };
      ws.onclose = scheduleRetry;
      ws.onerror = scheduleRetry;
    };

    connect();

    return () => {
      stopRef.current = true;
      clearTimeout(retryRef.current);
      try { wsRef.current && wsRef.current.close(); } catch {}
    };
  }, [symbols.join(",")]);

  return { ticks, connected };
}

function HeaderStatus({ status }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className={`inline-flex h-2 w-2 rounded-full ${status ? "bg-emerald-500" : "bg-red-500"}`} />
      <span className="opacity-80">{status ? "Conectado à Deriv (DEMO)" : "Desconectado"}</span>
    </div>
  );
}

function MlIndicator({ active }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className={`inline-flex h-2 w-2 rounded-full ${active ? "bg-emerald-500" : "bg-slate-500"}`} />
      <span className="opacity-80">ML {active ? "Ativo" : "Inativo"}</span>
    </div>
  );
}

function LiveCard({ symbol, tick, onBuy, contracts }) {
  const price = tick?.price ?? "-";
  return (
    <Card className="live-card">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-base font-semibold tracking-tight">{symbol}</CardTitle>
        <Badge variant="secondary">{price}</Badge>
      </CardHeader>
      <CardContent className="flex items-center justify-between gap-3">
        <div className="text-xs opacity-70">{tick?.timestamp ? new Date(tick.timestamp * 1000).toLocaleTimeString() : "--:--"}</div>
        <div className="flex gap-2">
          <Button size="sm" className="btn-buy" disabled={!((contracts?.contract_types||[]).includes("CALL"))} onClick={() => onBuy(symbol, "CALL")}>Buy CALL</Button>
          <Button size="sm" variant="outline" className="btn-sell" disabled={!((contracts?.contract_types||[]).includes("PUT"))} onClick={() => onBuy(symbol, "PUT")}>Buy PUT</Button>
        </div>
      </CardContent>
    </Card>
  );
}

function StrategyPanel({ onMlActiveChange }) {
  const { toast } = useToast();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const intervalRef = useRef(null);

  const defaults = useMemo(() => ({
    symbol: "R_100",
    granularity: 60,
    candle_len: 200,
    duration: 5,
    duration_unit: "t",
    stake: 1,
    daily_loss_limit: -20,
    adx_trend: 22,
    rsi_ob: 70,
    rsi_os: 30,
    bbands_k: 2,
    mode: "paper",
    ml_gate: true,
    ml_prob_threshold: Number(localStorage.getItem("ml_prob_threshold") || 0.5),
  }), []);

  const fetchStatus = async () => {
    try {
      const { data } = await axios.get(`${API}/strategy/status`);
      setStatus(data);
      if (onMlActiveChange) onMlActiveChange(!!data?.running);
    } catch (e) { /* ignore */ }
  };

  useEffect(() => {
    fetchStatus();
    intervalRef.current = setInterval(fetchStatus, 3000);
    return () => { try { clearInterval(intervalRef.current); } catch {} };
  }, []);

  const start = async () => {
    setLoading(true);
    try {
      await axios.post(`${API}/strategy/start`, defaults);
      toast({ title: "Estratégia iniciada (paper)", description: `${defaults.symbol} • ${defaults.duration}${defaults.duration_unit}` });
      await fetchStatus();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "Erro";
      toast({ title: "Falha ao iniciar", description: String(detail), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const stop = async () => {
    setLoading(true);
    try {
      await axios.post(`${API}/strategy/stop`);
      toast({ title: "Estratégia parada" });
      await fetchStatus();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "Erro";
      toast({ title: "Falha ao parar", description: String(detail), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const winRate = status?.win_rate ?? 0;
  const wins = status?.wins ?? 0;
  const losses = status?.losses ?? 0;
  const total = status?.total_trades ?? 0;
  // Para manter consistência com os contadores (globais), exibir sempre o PnL global
  // Assim, Win rate/Acertos/Erros/Total e PnL dia são do mesmo escopo
  const dpnl = Number(status?.global_daily_pnl ?? 0);
  const globalDpnl = dpnl;

  return (
    <Card className="mb-4">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Estratégia (ADX/RSI/MACD/BB)</span>
          <div className="flex items-center gap-2 text-xs opacity-80">
            <span className={`inline-flex h-2 w-2 rounded-full ${status?.running ? "bg-emerald-500" : "bg-slate-500"}`} />
            <span>{status?.running ? "Rodando" : "Parada"}</span>
            <span>• Modo: {status?.mode || "-"}</span>
            <span>• Símbolo: {status?.symbol || "-"}</span>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-4 text-sm">
        <div className="font-medium">Win rate: {winRate.toFixed(0)}% • Acertos: {wins} • Erros: {losses} • Total: {total}</div>
        <div className="opacity-80">PnL dia: {dpnl.toFixed(2)}</div>
        <div className="opacity-80">Último sinal: {status?.last_signal || "-"}</div>
        <div className="opacity-80">Motivo: {status?.last_reason || "-"}</div>
        <div className="ml-auto flex gap-2">
          <Button disabled={loading || status?.running} onClick={start}>Start (paper)</Button>
          <Button variant="outline" disabled={loading || !status?.running} onClick={stop}>Stop</Button>
        </div>
      </CardContent>
    </Card>
  );
}

function AutomacaoPanel({ buyAdvanced, stake, duration, durationUnit, defaultSymbol = "R_10" }) {
  const [enabled, setEnabled] = useState(false);
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [period, setPeriod] = useState(20); // últimos N preços
  const [cooldown, setCooldown] = useState(30); // segundos entre trades
  const [contractEngine, setContractEngine] = useState("CALLPUT"); // CALLPUT | ACCUMULATOR | TURBOS | MULTIPLIERS
  const [multiplier, setMultiplier] = useState(200);
  const [strike, setStrike] = useState("ATM");
  const [tp, setTp] = useState(50);
  const [sl, setSl] = useState(20);
  const [growthRate, setGrowthRate] = useState(0.03);
  const [lastSignal, setLastSignal] = useState(null);
  const [avg, setAvg] = useState(null);
  const [lastError, setLastError] = useState(null);
  const [support, setSupport] = useState({ basic: null, multipliers: null, turbos: null, accumulator: null });

  const wsRef = useRef(null);
  const pricesRef = useRef([]);
  const prevRelationRef = useRef(null);
  const lastTradeAtRef = useRef(0);

  // Fetch suporte de tipos por símbolo
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [basic, multipliers, turbos, accumulator] = await Promise.all([
          axios.get(`${API}/deriv/contracts_for_smart/${symbol}?product_type=basic`).then(r=> (r.data.results?.[symbol] || r.data)).catch(()=>null),
          axios.get(`${API}/deriv/contracts_for_smart/${symbol}?product_type=multipliers`).then(r=> (r.data.results?.[symbol] || r.data)).catch(()=>null),
          axios.get(`${API}/deriv/contracts_for_smart/${symbol}?product_type=turbos`).then(r=> (r.data.results?.[symbol] || r.data)).catch(()=>null),
          axios.get(`${API}/deriv/contracts_for_smart/${symbol}?product_type=accumulator`).then(r=> {
            const data = r.data;
            // Usar o resultado escolhido pelo backend (already smart with basic fallback)
            if (data.first_supported && data.results && data.results[data.first_supported]) return data.results[data.first_supported];
            return data.results?.[symbol] || data;
          }).catch(()=>null),
        ]);
        if (!cancelled) setSupport({ basic, multipliers, turbos, accumulator });
      } catch (e) {
        if (!cancelled) setSupport({ basic: null, multipliers: null, turbos: null, accumulator: null });
      }
    })();
    return () => { cancelled = true; };
  }, [symbol]);

  const isTypeSupported = (type) => {
    if (type === "CALLPUT") {
      const ct = (support.basic?.contract_types)||[];
      return ct.includes("CALL") && ct.includes("PUT");
    }
    if (type === "ACCUMULATOR") {
      const ct = ((support.accumulator?.contract_types)||[]).map((x)=>x.toUpperCase());
      // Aceita ACCU/ACCUMULATOR do product_type=accumulator OU, quando o backend caiu para basic, os tipos aparecerem em basic
      const basicCt = ((support.basic?.contract_types)||[]).map((x)=>x.toUpperCase());
      return ct.includes("ACCU") || ct.includes("ACCUMULATOR") || basicCt.includes("ACCU") || basicCt.includes("ACCUMULATOR");
    }
    if (type === "TURBOS") {
      const ct = (support.turbos?.contract_types||[]).map((x)=>x.toUpperCase());
      return ct.includes("TURBOSLONG") || ct.includes("TURBOSSHORT");
    }
    if (type === "MULTIPLIERS") {
      const ct = (support.multipliers?.contract_types||[]).map((x)=>x.toUpperCase());
      return ct.includes("MULTUP") || ct.includes("MULTDOWN");
    }
    return false;
  };

  function buildPayloadForSide(side) {
    if (contractEngine === "CALLPUT") {
      return {
        type: "CALLPUT",
        symbol,
        contract_type: side, // CALL ou PUT
        duration: Number(duration),
        duration_unit: durationUnit,
        stake: Number(stake),
        currency: "USD",
      };
    }
    if (contractEngine === "ACCUMULATOR") {
      return {
        type: "ACCUMULATOR",
        symbol,
        stake: Number(stake),
        max_price: Number(stake),
        currency: "USD",
        growth_rate: Number(growthRate),
        // ACCU não aceita stop_loss — só enviaremos take_profit
        limit_order: { take_profit: Number(tp) },
      };
    }
    if (contractEngine === "TURBOS") {
      const ct = side === "CALL" ? "TURBOSLONG" : "TURBOSSHORT";
      return {
        type: "TURBOS",
        symbol,
        contract_type: ct,
        stake: Number(stake),
        currency: "USD",
        strike: String(strike || "ATM"),
      };
    }
    if (contractEngine === "MULTIPLIERS") {
      const ct = side === "CALL" ? "MULTUP" : "MULTDOWN";
      return {
        type: "MULTIPLIERS",
        symbol,
        contract_type: ct,
        stake: Number(stake),
        currency: "USD",
        multiplier: Number(multiplier || 200),
        limit_order: { take_profit: Number(tp), stop_loss: Number(sl) },
      };
    }
    return { symbol, contract_type: side, stake: Number(stake), currency: "USD" };
  }

  useEffect(() => {
    if (!enabled) {
      try { wsRef.current?.close(); } catch {}
      wsRef.current = null;
      return;
    }
    // Abrir WS para ticks do símbolo escolhido via backend seguro
    const url = wsTicksUrl();
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ symbols: [symbol] }));
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "tick" && msg.symbol === symbol) {
          const price = Number(msg.price);
          if (!Number.isFinite(price)) return;
          const arr = pricesRef.current.slice();
          arr.push(price);
          if (arr.length > period) arr.shift();
          pricesRef.current = arr;
          const a = arr.reduce((s, x) => s + x, 0) / Math.max(arr.length, 1);
          setAvg(a);
          if (arr.length < Math.max(3, period)) return; // aguarda dados suficientes
          // relação com a média
          const last = arr[arr.length - 1];
          const prev = arr[arr.length - 2];
          const relation = last > a ? "above" : last < a ? "below" : "equal";
          const prevRel = prevRelationRef.current;
          const now = Date.now();
          const cooled = now - lastTradeAtRef.current > cooldown * 1000;
          // Gatilho: cruzamento da média e respeita cooldown
          if (prevRel && relation !== prevRel && relation !== "equal" && cooled) {
            const side = relation === "above" ? "CALL" : "PUT";
            setLastSignal({ ts: now, side, price: last, avg: a });
            lastTradeAtRef.current = now;
            
            console.log(`🎯 Sinal detectado: ${side} - Preço: ${last.toFixed(4)}, Média: ${a.toFixed(4)}`);
            
            // Verifica suporte
            if (!isTypeSupported(contractEngine)) {
              setLastError(`Tipo ${contractEngine} não suportado para ${symbol}.`);
              console.warn(`❌ Tipo não suportado: ${contractEngine} para ${symbol}`);
              return;
            }
            
            // Dispara compra via backend seguro (não para o sistema em caso de erro)
            const payload = buildPayloadForSide(side);
            buyAdvanced(payload, (error) => {
              setLastError(error);
              console.error(`❌ Erro na compra automática: ${error}`);
              // Continua funcionando mesmo com erro
            });
          }
          prevRelationRef.current = relation;
        }
      } catch (e) {
        setLastError(String(e?.message||e));
      }
    };
    ws.onerror = () => {};
    ws.onclose = () => {};
    return () => {
      try { ws.close(); } catch {}
    };
  }, [enabled, symbol, period, cooldown, contractEngine, multiplier, strike, tp, sl, stake, duration, durationUnit, growthRate, buyAdvanced, isTypeSupported]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Rocket size={18}/> Automação</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {lastError && (
          <div className="rounded-md border border-red-500/30 bg-red-500/10 text-red-200 p-3 text-sm">
            <div className="font-medium">Erro</div>
            <div className="mt-1 whitespace-pre-wrap break-words">{String(lastError)}</div>
          </div>
        )}
        <div className="flex items-center justify-between">
          <div>
            <div className="font-medium">Entradas automáticas</div>
            <div className="text-xs opacity-70">Regra: cruzamento da média simples. Backend seguro. Exibe erros detalhados aqui.</div>
          </div>
          <div className="flex items-center gap-3">
            <Switch checked={enabled} onCheckedChange={(v)=>{ setLastError(null); setEnabled(v); }} />
            {enabled ? <Play size={16} className="text-emerald-500"/> : <Square size={16} className="text-slate-400"/>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Símbolo</span>
            <Select value={symbol} onValueChange={(v)=>{ setSymbol(v); setLastError(null); }}>
              <SelectTrigger className="w-44"><SelectValue placeholder="Símbolo"/></SelectTrigger>
              <SelectContent>
                {derivedSymbols.map((d) => (
                  <SelectItem key={d.value} value={d.value}>{d.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Período</span>
            <Input className="w-24" type="number" min={5} max={200} value={period} onChange={(e) => setPeriod(Number(e.target.value||20))} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Cooldown (s)</span>
            <Input className="w-24" type="number" min={0} max={600} value={cooldown} onChange={(e) => setCooldown(Number(e.target.value||30))} />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm opacity-80">Tipo</span>
            <Select value={contractEngine} onValueChange={(v)=>{ setContractEngine(v); setLastError(null); }}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Tipo"/></SelectTrigger>
              <SelectContent>
                <SelectItem value="CALLPUT">CALL/PUT</SelectItem>
                <SelectItem value="ACCUMULATOR">ACCUMULATOR</SelectItem>
                <SelectItem value="TURBOS">TURBOS</SelectItem>
                <SelectItem value="MULTIPLIERS">MULTIPLIERS</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {/* Aviso de suporte quando o tipo selecionado pode não ser suportado */}
          {contractEngine !== "CALLPUT" && !isTypeSupported(contractEngine) ? (
            <div className="text-xs text-amber-300">
              Aviso: o tipo {contractEngine} pode não ser suportado para {symbol}. Você ainda pode tentar operar; se falhar, verifique os parâmetros e o símbolo.
            </div>
          ) : null}

          {contractEngine === "MULTIPLIERS" && (
            <>
              <div className="flex items-center gap-2">
                <span className="text-sm opacity-80">Multiplier</span>
                <Input className="w-24" type="number" min={1} max={2000} value={multiplier} onChange={(e) => setMultiplier(Number(e.target.value||200))} />
              </div>
            </>
          )}
          {contractEngine === "TURBOS" && (
            <div className="flex items-center gap-2">
              <span className="text-sm opacity-80">Strike</span>
              <Input className="w-28" value={strike} onChange={(e) => setStrike(e.target.value)} placeholder="ATM" />
            </div>
          )}
          {contractEngine === "ACCUMULATOR" && (
            <div className="flex items-center gap-2">
              <span className="text-sm opacity-80">Growth</span>
              <Input className="w-24" type="number" step="0.01" min={0.01} max={0.05} value={growthRate} onChange={(e) => setGrowthRate(Number(e.target.value||0.03))} />
            </div>
          )}
          {contractEngine === "ACCUMULATOR" && (
            <>
              <div className="flex items-center gap-2">
                <span className="text-sm opacity-80">TP</span>
                <Input className="w-24" type="number" value={tp} onChange={(e) => setTp(Number(e.target.value||50))} />
              </div>
              {/* SL oculto para ACCUMULATOR (não suportado pela Deriv) */}
            </>
          )}

          {contractEngine === "MULTIPLIERS" && (
            <>
              <div className="flex items-center gap-2">
                <span className="text-sm opacity-80">TP</span>
                <Input className="w-24" type="number" value={tp} onChange={(e) => setTp(Number(e.target.value||50))} />
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm opacity-80">SL</span>
                <Input className="w-24" type="number" value={sl} onChange={(e) => setSl(Number(e.target.value||20))} />
              </div>
            </>
          )}
          <div className="flex items-center gap-2 text-sm opacity-80">
            <span>Média:</span>
            <span className="font-mono">{avg ? avg.toFixed(4) : "-"}</span>
          </div>
          <div className="flex items-center gap-2 text-sm opacity-80">
            <span>Último sinal:</span>
            <span>{lastSignal ? `${new Date(lastSignal.ts).toLocaleTimeString()} • ${lastSignal.side}` : "-"}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ContractPanel({ contract }) {
  if (!contract) return null;
  const fmt = (v) => (v === undefined || v === null ? "-" : v);
  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>Contrato #{fmt(contract.contract_id)}</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-3 text-sm">
        <div><span className="opacity-70">Ativo:</span> {fmt(contract.underlying)}</div>
        <div><span className="opacity-70">Status:</span> {fmt(contract.status)}</div>
        <div><span className="opacity-70">Entrada:</span> {fmt(contract.entry_spot)}</div>
        <div><span className="opacity-70">Atual:</span> {fmt(contract.current_spot)}</div>
        <div><span className="opacity-70">Compra:</span> {fmt(contract.buy_price)}</div>
        <div><span className="opacity-70">Lance:</span> {fmt(contract.bid_price)}</div>
        <div><span className="opacity-70">Payout:</span> {fmt(contract.payout)}</div>
        <div><span className="opacity-70">Lucro:</span> {fmt(contract.profit)}</div>
        <div><span className="opacity-70">Início:</span> {contract.date_start ? new Date(contract.date_start * 1000).toLocaleTimeString() : '-'}
        </div>
        <div><span className="opacity-70">Expira:</span> {contract.date_expiry ? new Date(contract.date_expiry * 1000).toLocaleTimeString() : '-'}
        </div>
      </CardContent>
    </Card>
  );
}

export default function App() {
  const [statusMl, setStatusMl] = useState(false);
  const { toast } = useToast();
  const [symbols, setSymbols] = useState(defaultSymbols);
  const { ticks, connected } = useDerivTicks(symbols);

  const [stake, setStake] = useState(1);
  const [duration, setDuration] = useState(5);
  const [durationUnit, setDurationUnit] = useState("t");
  const [contractsFor, setContractsFor] = useState({});
  const [openContract, setOpenContract] = useState(null);
  const contractWsRef = useRef(null);
  const [lastError, setLastError] = useState(null);

  const buy = async (symbol, contractType) => {
    try {
      const res = await axios.post(`${API}/deriv/buy`, {
        type: "CALLPUT",
        symbol,
        contract_type: contractType,
        duration: Number(duration),
        duration_unit: durationUnit,
        stake: Number(stake),
        currency: "USD",
      });
      const cid = res.data.contract_id;
      toast({ title: `Compra enviada (${contractType})`, description: `Contrato #${cid || "-"}` });
      // Track contract via WS
      if (cid) {
        try { if (contractWsRef.current) contractWsRef.current.close(); } catch {}
        const url = wsContractUrl(cid);
        const ws = new WebSocket(url);
        contractWsRef.current = ws;
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "contract") {
              setOpenContract(msg);
            }
          } catch {}
        };
        ws.onerror = () => {};
      }
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "Erro desconhecido";
      setLastError(`Falha na compra (manual): ${String(detail)}`);
      toast({ title: "Falha na compra", description: String(detail), variant: "destructive" });
    }
  };

  const buyAdvanced = async (payload, setErr) => {
    try {
      console.log(`🚀 Tentando compra automática:`, payload);
      const res = await axios.post(`${API}/deriv/buy`, payload);
      const cid = res.data.contract_id;
      console.log(`✅ Compra realizada com sucesso: Contrato #${cid}`);
      toast({ title: "Compra enviada (auto)", description: `Contrato #${cid || "-"}` });
      
      if (cid) {
        try { if (contractWsRef.current) contractWsRef.current.close(); } catch {}
        const url = wsContractUrl(cid);
        const ws = new WebSocket(url);
        contractWsRef.current = ws;
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "contract") {
              setOpenContract(msg);
            }
          } catch {}
        };
        ws.onerror = (err) => {
          console.warn(`⚠️ Erro no WebSocket do contrato ${cid}:`, err);
        };
      }
      
      // Limpar erro anterior se compra foi bem-sucedida
      if (setErr) setErr(null);
      
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "Erro desconhecido";
      const more = e?.response?.data ? JSON.stringify(e.response.data) : "";
      const msg = `Falha na compra (auto): ${detail}\n${more}`;
      
      console.error(`❌ Erro na compra automática:`, {
        detail,
        payload,
        response: e?.response?.data
      });
      
      // Reportar erro mas não para o sistema
      if (setErr) setErr(msg);
      toast({ title: "Falha na compra (auto)", description: String(detail), variant: "destructive" });
    }
  };

  useEffect(() => {
    // warmup backend
    axios.get(`${API}/deriv/status`).catch(() => {});
  }, []);
  // Carregar contracts_for (basic) dos símbolos atuais e ajustar UI CALL/PUT
  useEffect(() => {
    const fetchContracts = async () => {
      const entries = await Promise.all(symbols.map(async (s) => {
        try {
          const { data } = await axios.get(`${API}/deriv/contracts_for/${s}?product_type=basic`);
          return [s, data];
        } catch (err) {
          return [s, null];
        }
      }));
      const map = Object.fromEntries(entries);
      setContractsFor(map);
      // Ajuste default de unidade
      const first = symbols[0];
      const units = map[first]?.duration_units || [];
      if (units.length) setDurationUnit(units.includes("t") ? "t" : units[0]);
    };
    fetchContracts();
  }, [symbols.join(",")]);

  return (
    <ToastProvider>
      {/* Desabilitar botões se tipo de contrato não existir para símbolo */}
      <div className="min-h-screen bg-app text-slate-50">
        <div className="container mx-auto px-6 py-8">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">TypeA – Trading</h1>
              <Card className="mt-6">
                <CardHeader>
                  <CardTitle>Índices Volatility (1s)</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-3">
                  {["1HZ10V","1HZ25V","1HZ50V","1HZ75V","1HZ100V"].map((sym) => (
                    <Button key={sym} variant={symbols.includes(sym) ? "default" : "secondary"} onClick={() => {
                      setSymbols((prev) => prev.includes(sym) ? prev.filter((x) => x !== sym) : [...prev, sym]);
                    }}>{sym}</Button>
                  ))}
                </CardContent>
              </Card>

              <Card className="mt-6">
                <CardHeader>
                  <CardTitle>Índices Volatility</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-3">
                  {["R_10","R_25","R_50","R_75","R_100"].map((sym) => (
                    <Button key={sym} variant={symbols.includes(sym) ? "default" : "secondary"} onClick={() => {
                      setSymbols((prev) => prev.includes(sym) ? prev.filter((x) => x !== sym) : [...prev, sym]);
                    }}>{sym}</Button>
                  ))}
                </CardContent>
              </Card>

              <div className="mt-1"><HeaderStatus status={connected} /></div>
            </div>
            <div className="flex gap-3 items-center">
              <div className="flex items-center gap-2">
                <Input className="w-24" type="number" value={stake} onChange={(e) => setStake(e.target.value)} placeholder="Stake" />
                <Input className="w-20" type="number" value={duration} onChange={(e) => setDuration(e.target.value)} placeholder="Dur." />
                <Select value={durationUnit} onValueChange={setDurationUnit}>
                  <SelectTrigger className="w-24"><SelectValue placeholder="Unid."/></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="t">Ticks</SelectItem>
                    <SelectItem value="s">Segundos</SelectItem>
                    <SelectItem value="m">Minutos</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          <Tabs defaultValue="ao-vivo" className="tabs-root">
            <TabsList className="tabs-list">
              <TabsTrigger value="ao-vivo"><ActivitySquare size={16}/> Oportunidades ao vivo</TabsTrigger>
              <TabsTrigger value="derived">Derived indices</TabsTrigger>
              <TabsTrigger value="auto">Automação</TabsTrigger>
            </TabsList>
              <div className="ml-4"><span className="text-xs opacity-70">Modelo:</span></div>

            <TabsContent value="ao-vivo" className="mt-6 grid md:grid-cols-3 gap-4">
              {symbols.map((s) => (
                <LiveCard key={s} symbol={s} tick={ticks[s]} onBuy={buy} contracts={contractsFor[s]} />
              ))}
            </TabsContent>

            <TabsContent value="derived" className="mt-6">
              <Card>
                <CardHeader>
                  <CardTitle>Selecionar índices sintéticos</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-3">
                  {derivedSymbols.map((d) => (
                    <Button key={d.value} variant={symbols.includes(d.value) ? "default" : "secondary"} onClick={() => {
                      setSymbols((prev) => prev.includes(d.value) ? prev.filter((x) => x !== d.value) : [...prev, d.value]);
                    }}>{d.label}</Button>
                  ))}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="auto" className="mt-6">
              {/* Painel de ML */}
              <MlPanel />
              {/* Painel da Estratégia com métricas em tempo real */}
              <div className="flex items-center gap-4 mb-2">
                <MlIndicator active={statusMl} />
              </div>
              <StrategyPanel onMlActiveChange={(v)=>setStatusMl(v)} />
              <AutomacaoPanel buyAdvanced={buyAdvanced} stake={stake} duration={duration} durationUnit={durationUnit} />
              {lastError && (
                <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 text-red-200 p-3 text-sm">
                  <div className="font-medium">Erro global</div>
                  <div className="mt-1 whitespace-pre-wrap break-words">{String(lastError)}</div>
                </div>
              )}
              <ContractPanel contract={openContract} />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </ToastProvider>
  );
}