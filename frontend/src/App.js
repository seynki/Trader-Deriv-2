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

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

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
    const u = new URL(BACKEND_URL);
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
  useEffect(() => {
    if (!symbols || symbols.length === 0) return;
    const url = wsTicksUrl();
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      ws.send(JSON.stringify({ symbols }));
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "tick") {
          setTicks((prev) => ({ ...prev, [msg.symbol]: msg }));
        }
      } catch {}
    };
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    return () => {
      try { ws.close(); } catch {}
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
          <Button size="sm" className="btn-buy" disabled={!(contracts?.contract_types||[]).includes("CALL")} onClick={() => onBuy(symbol, "CALL")}>Buy CALL</Button>
          <Button size="sm" variant="outline" className="btn-sell" disabled={!(contracts?.contract_types||[]).includes("PUT")} onClick={() => onBuy(symbol, "PUT")}>Buy PUT</Button>
        </div>
      </CardContent>
    </Card>
  );
}

function AutomacaoPanel() {
  const [enabled, setEnabled] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Rocket size={18}/> Automação</CardTitle>
      </CardHeader>
      <CardContent className="flex items-center justify-between">
        <div>
          <div className="font-medium">Entradas automáticas</div>
          <div className="text-xs opacity-70">Quando ativo, o sistema poderá executar as melhores ações conforme regras.</div>
        </div>
        <div className="flex items-center gap-3">
          <Switch checked={enabled} onCheckedChange={setEnabled} />
          {enabled ? <Play size={16} className="text-emerald-500"/> : <Square size={16} className="text-slate-400"/>}
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
        <div><span className="opacity-70">Início:</span> {contract.date_start ? new Date(contract.date_start * 1000).toLocaleTimeString() : '-'}</div>
        <div><span className="opacity-70">Expira:</span> {contract.date_expiry ? new Date(contract.date_expiry * 1000).toLocaleTimeString() : '-'}</div>
      </CardContent>
    </Card>
  );
}

export default function App() {
  const { toast } = useToast();
  const [symbols, setSymbols] = useState(defaultSymbols);
  const { ticks, connected } = useDerivTicks(symbols);

  const [stake, setStake] = useState(1);
  const [duration, setDuration] = useState(5);
  const [durationUnit, setDurationUnit] = useState("t");
  const [contractsFor, setContractsFor] = useState({});
  const [openContract, setOpenContract] = useState(null);
  const contractWsRef = useRef(null);

  const buy = async (symbol, contractType) => {
    try {
      const res = await axios.post(`${API}/deriv/buy`, {
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
      const detail = e?.response?.data?.detail || e.message;
      toast({ title: "Falha na compra", description: String(detail), variant: "destructive" });
    }
  };

  useEffect(() => {
    // warmup backend
    axios.get(`${API}/deriv/status`).catch(() => {});
  }, []);
  // Carregar contracts_for dos símbolos atuais e ajustar UI conforme oferta oficial
  useEffect(() => {
    const fetchContracts = async () => {
      const entries = await Promise.all(symbols.map(async (s) => {
        try {
          const { data } = await axios.get(`${API}/deriv/contracts_for/${s}`);
          return [s, data];
        } catch {
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
              <AutomacaoPanel />
              <ContractPanel contract={openContract} />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </ToastProvider>
  );
}