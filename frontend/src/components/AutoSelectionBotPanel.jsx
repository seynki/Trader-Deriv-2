import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Switch } from './ui/switch';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Alert, AlertDescription } from './ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Progress } from './ui/progress';
import { Slider } from './ui/slider';
import { 
  PlayCircle, 
  StopCircle, 
  Activity, 
  TrendingUp, 
  DollarSign, 
  Target,
  Settings,
  BarChart3,
  RefreshCw,
  Sliders
} from 'lucide-react';

const AutoSelectionBotPanel = ({ backendUrl }) => {
  const [botStatus, setBotStatus] = useState({
    running: false,
    collecting_ticks: false,
    last_evaluation: null,
    best_combo: null,
    total_evaluations: 0,
    symbols_with_data: [],
    tick_counts: {},
    auto_execute: false,
    trades_executed: 0,
    last_trade: null
  });

  const [config, setConfig] = useState({
    symbols: ["R_100", "R_75", "R_50", "R_25", "R_10"],
    timeframes: [
      ["ticks", 1],
      ["ticks", 2],    // NOVO
      ["ticks", 5],
      ["ticks", 10],
      ["ticks", 25],   // NOVO
      ["ticks", 50],   // NOVO
      ["s", 15],
      ["s", 30],
      ["s", 60],
      ["s", 120],
      ["s", 300],
      ["m", 1],
      ["m", 2],        // NOVO
      ["m", 3],
      ["m", 5],
      ["m", 10],
      ["m", 15],       // NOVO
      ["m", 30]        // NOVO
    ],
    sim_window_seconds: 60,
    sim_trade_stake: 1.0,
    auto_execute: false,
    evaluation_interval: 5,
    min_winrate: 0.75,           // MAIS RIGOROSO (75% vs 70%)
    min_trades_sample: 8,        // MAIS RIGOROSO (8 vs 5)
    min_pnl_positive: 0.5,       // NOVO critério
    use_combined_score: true,
    conservative_mode: true,     // NOVO
    prefer_longer_timeframes: true,  // NOVO
    score_weights: {             // NOVO
      winrate: 0.5,              // Maior peso para winrate
      pnl: 0.3,
      volume: 0.1,
      timeframe: 0.1
    }
  });

  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Atualizar status a cada 3 segundos quando bot estiver rodando
  useEffect(() => {
    const interval = setInterval(() => {
      if (botStatus.running) {
        fetchBotStatus();
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [botStatus.running]);

  const fetchBotStatus = async () => {
    try {
      const response = await fetch(`${backendUrl}/api/auto-bot/status`);
      if (response.ok) {
        const data = await response.json();
        setBotStatus(data);
      }
    } catch (err) {
      console.error('Erro ao buscar status do bot:', err);
    }
  };

  const fetchResults = async () => {
    try {
      const response = await fetch(`${backendUrl}/api/auto-bot/results`);
      if (response.ok) {
        const data = await response.json();
        setResults(data);
      }
    } catch (err) {
      console.error('Erro ao buscar resultados:', err);
    }
  };

  const startBot = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${backendUrl}/api/auto-bot/start`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(config)
      });

      if (response.ok) {
        const data = await response.json();
        setBotStatus(data.status);
        fetchResults();
      } else {
        const errorData = await response.json();
        setError(errorData.detail || 'Erro ao iniciar bot');
      }
    } catch (err) {
      setError('Erro de conexão ao iniciar bot');
    } finally {
      setLoading(false);
    }
  };

  const stopBot = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${backendUrl}/api/auto-bot/stop`, {
        method: 'POST'
      });

      if (response.ok) {
        const data = await response.json();
        setBotStatus(data.status);
      } else {
        const errorData = await response.json();
        setError(errorData.detail || 'Erro ao parar bot');
      }
    } catch (err) {
      setError('Erro de conexão ao parar bot');
    } finally {
      setLoading(false);
    }
  };

  const updateConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${backendUrl}/api/auto-bot/config`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(config)
      });

      if (response.ok) {
        setError(null);
        // Atualizar status após configuração
        setTimeout(fetchBotStatus, 500);
      } else {
        const errorData = await response.json();
        setError(errorData.detail || 'Erro ao atualizar configuração');
      }
    } catch (err) {
      setError('Erro de conexão ao atualizar configuração');
    } finally {
      setLoading(false);
    }
  };

  const formatTimeframe = (tf) => {
    if (Array.isArray(tf) && tf.length === 2) {
      const [type, value] = tf;
      if (type === 'ticks') return `${value} ticks`;
      if (type === 's') return `${value}s`;
      if (type === 'm') return `${value}m`;
    }
    return String(tf);
  };

  const formatDateTime = (dateString) => {
    if (!dateString) return 'Nunca';
    return new Date(dateString).toLocaleString('pt-BR');
  };

  const getStatusColor = (running, collecting) => {
    if (running && collecting) return 'bg-green-500';
    if (running) return 'bg-yellow-500';
    return 'bg-gray-500';
  };

  return (
    <div className="space-y-4">
      {/* Header com Status */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Bot de Seleção Automática
            <div className={`w-3 h-3 rounded-full ${getStatusColor(botStatus.running, botStatus.collecting_ticks)}`} />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4 items-center">
            <Button
              onClick={botStatus.running ? stopBot : startBot}
              disabled={loading}
              variant={botStatus.running ? "destructive" : "default"}
              className="flex items-center gap-2"
            >
              {botStatus.running ? (
                <>
                  <StopCircle className="h-4 w-4" />
                  Parar Bot
                </>
              ) : (
                <>
                  <PlayCircle className="h-4 w-4" />
                  Iniciar Bot
                </>
              )}
            </Button>
            
            <Button
              onClick={fetchBotStatus}
              variant="outline"
              size="sm"
              className="flex items-center gap-2"
            >
              <RefreshCw className="h-4 w-4" />
              Atualizar
            </Button>

            <div className="flex gap-4 text-sm">
              <Badge variant={botStatus.running ? "default" : "secondary"}>
                {botStatus.running ? "Executando" : "Parado"}
              </Badge>
              <Badge variant={botStatus.collecting_ticks ? "default" : "secondary"}>
                {botStatus.collecting_ticks ? "Coletando Ticks" : "Aguardando"}
              </Badge>
              <Badge variant={botStatus.auto_execute ? "destructive" : "outline"}>
                {botStatus.auto_execute ? "Execução Real" : "Simulação"}
              </Badge>
              <Badge variant={botStatus.conservative_mode ? "default" : "outline"}>
                {botStatus.conservative_mode ? "🛡️ Conservador" : "Normal"}
              </Badge>
              <Badge variant={botStatus.prefer_longer_timeframes ? "default" : "outline"}>
                {botStatus.prefer_longer_timeframes ? "⏱️ TF Longos" : "TF Mistos"}
              </Badge>
            </div>
          </div>

          {error && (
            <Alert className="mt-4" variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      <Tabs defaultValue="status" className="w-full">
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="status">Status</TabsTrigger>
          <TabsTrigger value="results">Resultados</TabsTrigger>
          <TabsTrigger value="config">Configuração</TabsTrigger>
          <TabsTrigger value="performance">Performance</TabsTrigger>
          <TabsTrigger value="ticks">Dados</TabsTrigger>
        </TabsList>

        {/* Aba Status */}
        <TabsContent value="status" className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <Card>
              <CardContent className="p-4">
                <div className="flex items-center gap-2">
                  <BarChart3 className="h-4 w-4 text-blue-500" />
                  <div>
                    <p className="text-sm text-muted-foreground">Avaliações</p>
                    <p className="text-2xl font-bold">{botStatus.total_evaluations}</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4">
                <div className="flex items-center gap-2">
                  <Target className="h-4 w-4 text-green-500" />
                  <div>
                    <p className="text-sm text-muted-foreground">Trades Executados</p>
                    <p className="text-2xl font-bold">{botStatus.trades_executed}</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4">
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-purple-500" />
                  <div>
                    <p className="text-sm text-muted-foreground">Símbolos Ativos</p>
                    <p className="text-2xl font-bold">{botStatus.symbols_with_data.length}</p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-4">
                <div className="flex items-center gap-2">
                  <TrendingUp className="h-4 w-4 text-orange-500" />
                  <div>
                    <p className="text-sm text-muted-foreground">Última Avaliação</p>
                    <p className="text-xs font-medium">{formatDateTime(botStatus.last_evaluation)}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Melhor Combinação */}
          {botStatus.best_combo && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Melhor Combinação Atual</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                  <div>
                    <Label className="text-sm text-muted-foreground">Símbolo</Label>
                    <p className="font-bold">{botStatus.best_combo.symbol}</p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Timeframe</Label>
                    <p className="font-bold">{botStatus.best_combo.timeframe_desc}</p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Win Rate</Label>
                    <p className="font-bold text-green-600">
                      {botStatus.best_combo.winrate ? `${(botStatus.best_combo.winrate * 100).toFixed(1)}%` : 'N/A'}
                    </p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">PnL Net</Label>
                    <p className={`font-bold ${botStatus.best_combo.net >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {botStatus.best_combo.net?.toFixed(2) || '0.00'}
                    </p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Score Combinado</Label>
                    <p className="font-bold text-blue-600">
                      {botStatus.best_combo.combined_score ? (botStatus.best_combo.combined_score * 100).toFixed(1) : 'N/A'}
                    </p>
                  </div>
                </div>
                
                {/* Indicadores de Status */}
                <div className="mt-4 flex gap-2">
                  <Badge variant={botStatus.best_combo.meets_criteria ? "default" : "destructive"}>
                    {botStatus.best_combo.meets_criteria ? "✓ Critérios Atendidos" : "✗ Critérios Não Atendidos"}
                  </Badge>
                  <Badge variant="outline">
                    Trades: {botStatus.best_combo.trades || 0}
                  </Badge>
                  <Badge variant="outline">
                    Candles: {botStatus.best_combo.candles_count || 0}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Último Trade */}
          {botStatus.last_trade && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Último Trade Executado</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                  <div>
                    <Label className="text-sm text-muted-foreground">Símbolo</Label>
                    <p className="font-bold">{botStatus.last_trade.symbol}</p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Direção</Label>
                    <Badge variant={botStatus.last_trade.direction === 'CALL' ? 'default' : 'secondary'}>
                      {botStatus.last_trade.direction}
                    </Badge>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Stake</Label>
                    <p className="font-bold">${botStatus.last_trade.stake}</p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Duração Auto</Label>
                    <Badge variant="outline">
                      {botStatus.last_trade.duration}{botStatus.last_trade.duration_unit}
                    </Badge>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Timeframe</Label>
                    <p className="text-sm font-medium">{botStatus.last_trade.auto_timeframe}</p>
                  </div>
                </div>
                {botStatus.last_trade.reason && (
                  <div className="mt-2">
                    <Label className="text-sm text-muted-foreground">Motivo</Label>
                    <p className="text-sm">{botStatus.last_trade.reason}</p>
                  </div>
                )}
                <div className="mt-2">
                  <Label className="text-sm text-muted-foreground">Timestamp</Label>
                  <p className="text-sm">{formatDateTime(botStatus.last_trade.timestamp)}</p>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Aba Resultados */}
        <TabsContent value="results" className="space-y-4">
          <Button onClick={fetchResults} variant="outline" className="flex items-center gap-2">
            <RefreshCw className="h-4 w-4" />
            Atualizar Resultados
          </Button>
          
          {results && results.best_combo && (
            <Card>
              <CardHeader>
                <CardTitle>Resultado da Última Avaliação</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  <p><strong>Avaliação:</strong> {formatDateTime(results.last_evaluation)}</p>
                  <p><strong>Total de Avaliações:</strong> {results.total_evaluations}</p>
                  <p><strong>Símbolos com Dados:</strong> {results.symbols_with_data.join(', ')}</p>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Aba Performance */}
        <TabsContent value="performance" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Estatísticas de Avaliação</CardTitle>
            </CardHeader>
            <CardContent>
              {botStatus.evaluation_stats ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <div className="text-center p-3 border rounded-lg">
                    <p className="text-sm text-muted-foreground">Total de Combinações</p>
                    <p className="text-2xl font-bold">{botStatus.evaluation_stats.total_combinations}</p>
                  </div>
                  <div className="text-center p-3 border rounded-lg">
                    <p className="text-sm text-muted-foreground">Combinações Válidas</p>
                    <p className="text-2xl font-bold text-green-600">{botStatus.evaluation_stats.valid_combinations}</p>
                  </div>
                  <div className="text-center p-3 border rounded-lg">
                    <p className="text-sm text-muted-foreground">Símbolos Avaliados</p>
                    <p className="text-2xl font-bold">{botStatus.evaluation_stats.symbols_evaluated}</p>
                  </div>
                  <div className="text-center p-3 border rounded-lg">
                    <p className="text-sm text-muted-foreground">Timeframes Avaliados</p>
                    <p className="text-2xl font-bold">{botStatus.evaluation_stats.timeframes_evaluated}</p>
                  </div>
                </div>
              ) : (
                <p className="text-muted-foreground">Nenhuma avaliação realizada ainda.</p>
              )}
            </CardContent>
          </Card>

          {/* Configurações Atuais */}
          <Card>
            <CardHeader>
              <CardTitle>Configurações Ativas</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div>
                  <Label className="text-sm text-muted-foreground">Winrate Mínimo</Label>
                  <p className="font-bold text-lg">{(botStatus.min_winrate * 100).toFixed(0)}%</p>
                </div>
                <div>
                  <Label className="text-sm text-muted-foreground">Score Combinado</Label>
                  <Badge variant={botStatus.use_combined_score ? "default" : "secondary"}>
                    {botStatus.use_combined_score ? "Ativo" : "Inativo"}
                  </Badge>
                </div>
                <div>
                  <Label className="text-sm text-muted-foreground">Execução Automática</Label>
                  <Badge variant={botStatus.auto_execute ? "destructive" : "outline"}>
                    {botStatus.auto_execute ? "Ativa" : "Simulação"}
                  </Badge>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Último Trade com Métricas Detalhadas */}
          {botStatus.last_trade && botStatus.last_trade.performance_metrics && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Métricas do Último Trade</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <div>
                    <Label className="text-sm text-muted-foreground">Winrate</Label>
                    <p className="font-bold text-green-600">
                      {(botStatus.last_trade.performance_metrics.winrate * 100).toFixed(1)}%
                    </p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">PnL Net</Label>
                    <p className={`font-bold ${botStatus.last_trade.performance_metrics.net_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {botStatus.last_trade.performance_metrics.net_pnl.toFixed(2)}
                    </p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Trades Amostra</Label>
                    <p className="font-bold">{botStatus.last_trade.performance_metrics.trades_sample}</p>
                  </div>
                  <div>
                    <Label className="text-sm text-muted-foreground">Score Combinado</Label>
                    <p className="font-bold text-blue-600">
                      {(botStatus.last_trade.performance_metrics.combined_score * 100).toFixed(1)}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>
        <TabsContent value="config" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings className="h-5 w-5" />
                Configurações do Bot
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Configurações Básicas */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <Label htmlFor="sim_window">Janela de Simulação (segundos)</Label>
                  <Input
                    id="sim_window"
                    type="number"
                    value={config.sim_window_seconds}
                    onChange={(e) => setConfig({...config, sim_window_seconds: parseInt(e.target.value)})}
                  />
                </div>
                <div>
                  <Label htmlFor="sim_stake">Stake de Simulação</Label>
                  <Input
                    id="sim_stake"
                    type="number"
                    step="0.1"
                    value={config.sim_trade_stake}
                    onChange={(e) => setConfig({...config, sim_trade_stake: parseFloat(e.target.value)})}
                  />
                </div>
                <div>
                  <Label htmlFor="eval_interval">Intervalo de Avaliação (segundos)</Label>
                  <Input
                    id="eval_interval"
                    type="number"
                    value={config.evaluation_interval}
                    onChange={(e) => setConfig({...config, evaluation_interval: parseInt(e.target.value)})}
                  />
                </div>
                <div>
                  <Label htmlFor="min_trades">Mínimo de Trades na Amostra</Label>
                  <Input
                    id="min_trades"
                    type="number"
                    value={config.min_trades_sample}
                    onChange={(e) => setConfig({...config, min_trades_sample: parseInt(e.target.value)})}
                  />
                </div>
              </div>

              {/* NOVO: PnL Mínimo Positivo */}
              <div>
                <Label htmlFor="min_pnl">PnL Mínimo Positivo</Label>
                <Input
                  id="min_pnl"
                  type="number"
                  step="0.1"
                  value={config.min_pnl_positive || 0.5}
                  onChange={(e) => setConfig({...config, min_pnl_positive: parseFloat(e.target.value)})}
                />
                <p className="text-xs text-muted-foreground mt-1">
                  PnL mínimo que uma combinação deve ter para ser considerada válida
                </p>
              </div>

              {/* Winrate Mínimo com Slider */}
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Sliders className="h-4 w-4" />
                  <Label>Winrate Mínimo para Executar Trades: {(config.min_winrate * 100).toFixed(0)}%</Label>
                </div>
                <Slider
                  value={[config.min_winrate * 100]}
                  onValueChange={(value) => setConfig({...config, min_winrate: value[0] / 100})}
                  max={90}
                  min={50}
                  step={1}
                  className="w-full"
                />
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>50%</span>
                  <span>90%</span>
                </div>
              </div>

              {/* Switches */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="flex items-center space-x-2">
                  <Switch
                    id="auto_execute"
                    checked={config.auto_execute}
                    onCheckedChange={(checked) => setConfig({...config, auto_execute: checked})}
                  />
                  <Label htmlFor="auto_execute">Execução Automática de Trades</Label>
                </div>
                <div className="flex items-center space-x-2">
                  <Switch
                    id="combined_score"
                    checked={config.use_combined_score}
                    onCheckedChange={(checked) => setConfig({...config, use_combined_score: checked})}
                  />
                  <Label htmlFor="combined_score">Usar Score Combinado (Winrate + PnL + Volume)</Label>
                </div>
              </div>

              <div>
                <Label>Símbolos Ativos</Label>
                <div className="flex flex-wrap gap-2 mt-2">
                  {config.symbols.map((symbol) => (
                    <Badge key={symbol} variant="default">{symbol}</Badge>
                  ))}
                </div>
              </div>

              <div>
                <Label>Timeframes Disponíveis</Label>
                <div className="flex flex-wrap gap-2 mt-2">
                  {config.timeframes.map((tf, index) => (
                    <Badge key={index} variant="outline">{formatTimeframe(tf)}</Badge>
                  ))}
                </div>
              </div>

              <Button onClick={updateConfig} disabled={loading} className="w-full">
                Atualizar Configuração
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Aba Dados */}
        <TabsContent value="ticks" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Contadores de Ticks por Símbolo</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
                {Object.entries(botStatus.tick_counts).map(([symbol, count]) => (
                  <div key={symbol} className="text-center p-3 border rounded-lg">
                    <p className="font-bold text-lg">{symbol}</p>
                    <p className="text-2xl font-bold text-blue-600">{count}</p>
                    <p className="text-xs text-muted-foreground">ticks</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default AutoSelectionBotPanel;