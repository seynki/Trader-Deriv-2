import React, { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Slider } from './ui/slider';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { AlertCircle, TrendingUp, TrendingDown, Settings, Activity, BarChart3 } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8001/api';

const RiverThresholdPanel = () => {
  // Estados principais
  const [config, setConfig] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [loading, setLoading] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [backtesting, setBacktesting] = useState(false);
  const [backtestResults, setBacktestResults] = useState(null);
  
  // Estados do controle
  const [tempThreshold, setTempThreshold] = useState(0.53);
  const [autoRefresh, setAutoRefresh] = useState(true);
  
  // Configurações de backtesting
  const [backtestConfig, setBacktestConfig] = useState({
    symbol: 'R_10',
    timeframe: '1m',
    lookback_candles: 1000,
    custom_thresholds: '0.50,0.53,0.55,0.60,0.65,0.70,0.75,0.80'
  });

  // Fetch da configuração atual
  const fetchConfig = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/strategy/river/config`);
      setConfig(data);
      setTempThreshold(data.river_threshold);
    } catch (error) {
      console.error('Erro ao obter config River:', error);
    }
  }, []);

  // Fetch da performance atual
  const fetchPerformance = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/strategy/river/performance`);
      setPerformance(data);
    } catch (error) {
      console.error('Erro ao obter performance River:', error);
    }
  }, []);

  // Atualizar threshold
  const updateThreshold = async (newThreshold) => {
    if (!newThreshold || newThreshold < 0.5 || newThreshold > 0.95) return;
    
    setUpdating(true);
    try {
      const { data } = await axios.post(`${API}/strategy/river/config`, {
        river_threshold: newThreshold
      });
      
      setConfig(prev => ({ ...prev, river_threshold: newThreshold }));
      console.log('Threshold atualizado:', data.message);
      
      // Refresh performance após mudança
      setTimeout(fetchPerformance, 1000);
    } catch (error) {
      console.error('Erro ao atualizar threshold:', error);
    }
    setUpdating(false);
  };

  // Executar backtesting
  const runBacktest = async () => {
    setBacktesting(true);
    try {
      const thresholds = backtestConfig.custom_thresholds
        .split(',')
        .map(t => parseFloat(t.trim()))
        .filter(t => !isNaN(t) && t >= 0.5 && t <= 0.95);

      const { data } = await axios.post(`${API}/strategy/river/backtest`, {
        symbol: backtestConfig.symbol,
        timeframe: backtestConfig.timeframe,
        lookback_candles: parseInt(backtestConfig.lookback_candles),
        thresholds: thresholds
      });
      
      setBacktestResults(data);
    } catch (error) {
      console.error('Erro no backtesting:', error);
      setBacktestResults({ error: error.response?.data?.detail || error.message });
    }
    setBacktesting(false);
  };

  // Auto refresh
  useEffect(() => {
    fetchConfig();
    fetchPerformance();
    
    if (autoRefresh) {
      const interval = setInterval(() => {
        fetchConfig();
        fetchPerformance();
      }, 5000);
      return () => clearInterval(interval);
    }
  }, [autoRefresh, fetchConfig, fetchPerformance]);

  if (!config) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="flex items-center justify-center">
            <Activity className="mr-2 h-4 w-4 animate-spin" />
            Carregando configuração River...
          </div>
        </CardContent>
      </Card>
    );
  }

  const currentWinRate = performance?.strategy_performance?.win_rate || 0;
  const riverAccuracy = performance?.river_model?.accuracy || 0;
  const totalTrades = performance?.strategy_performance?.total_trades || 0;

  return (
    <div className="space-y-6">
      {/* Header com Status */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            Controle River Threshold
            <Badge variant={config.is_running ? "default" : "outline"}>
              {config.is_running ? "Ativo" : "Parado"}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-600">
                {(config.river_threshold * 100).toFixed(1)}%
              </div>
              <div className="text-sm text-gray-500">Threshold Atual</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-600">
                {(currentWinRate).toFixed(1)}%
              </div>
              <div className="text-sm text-gray-500">Win Rate Estratégia</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-purple-600">
                {(riverAccuracy * 100).toFixed(1)}%
              </div>
              <div className="text-sm text-gray-500">Acurácia River</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Controle de Threshold */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5" />
            Ajuste em Tempo Real
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Slider Principal */}
          <div className="space-y-4">
            <Label className="text-base">
              River Threshold: {(tempThreshold * 100).toFixed(1)}%
            </Label>
            <Slider
              value={[tempThreshold]}
              onValueChange={(value) => setTempThreshold(value[0])}
              min={0.5}
              max={0.95}
              step={0.01}
              className="w-full"
            />
            <div className="flex justify-between text-xs text-gray-500">
              <span>50% (Menos seletivo)</span>
              <span>95% (Mais seletivo)</span>
            </div>
          </div>

          {/* Explicação do Threshold */}
          <div className="bg-blue-50 p-4 rounded-lg">
            <div className="flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm">
                <p className="font-semibold text-blue-800 mb-1">Como funciona:</p>
                <p className="text-blue-700">
                  • <strong>prob_up ≥ {(tempThreshold * 100).toFixed(1)}%</strong>: Sinal CALL (compra) <br/>
                  • <strong>prob_up ≤ {((1 - tempThreshold) * 100).toFixed(1)}%</strong>: Sinal PUT (venda) <br/>
                  • Entre {((1 - tempThreshold) * 100).toFixed(1)}% e {(tempThreshold * 100).toFixed(1)}%: Nenhum sinal (mais seletivo)
                </p>
              </div>
            </div>
          </div>

          {/* Botões de Ação */}
          <div className="flex gap-3">
            <Button 
              onClick={() => updateThreshold(tempThreshold)}
              disabled={updating || Math.abs(tempThreshold - config.river_threshold) < 0.001}
              className="flex-1"
            >
              {updating ? (
                <>
                  <Activity className="mr-2 h-4 w-4 animate-spin" />
                  Atualizando...
                </>
              ) : (
                'Aplicar Threshold'
              )}
            </Button>
            <Button 
              variant="outline" 
              onClick={() => setTempThreshold(0.53)}
            >
              Reset (53%)
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Backtesting */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            Backtesting & Otimização
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Configurações de Backtesting */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label>Símbolo</Label>
              <select 
                value={backtestConfig.symbol}
                onChange={(e) => setBacktestConfig(prev => ({ ...prev, symbol: e.target.value }))}
                className="w-full mt-1 p-2 border rounded"
              >
                <option value="R_100">R_100</option>
                <option value="R_75">R_75</option>
                <option value="R_50">R_50</option>
                <option value="R_25">R_25</option>
                <option value="R_10">R_10</option>
              </select>
            </div>
            <div>
              <Label>Timeframe</Label>
              <select 
                value={backtestConfig.timeframe}
                onChange={(e) => setBacktestConfig(prev => ({ ...prev, timeframe: e.target.value }))}
                className="w-full mt-1 p-2 border rounded"
              >
                <option value="1m">1 minuto</option>
                <option value="3m">3 minutos</option>
                <option value="5m">5 minutos</option>
              </select>
            </div>
            <div>
              <Label>Candles Históricos</Label>
              <Input
                type="number"
                value={backtestConfig.lookback_candles}
                onChange={(e) => setBacktestConfig(prev => ({ ...prev, lookback_candles: e.target.value }))}
                min="100"
                max="5000"
              />
            </div>
            <div>
              <Label>Thresholds para Testar</Label>
              <Input
                value={backtestConfig.custom_thresholds}
                onChange={(e) => setBacktestConfig(prev => ({ ...prev, custom_thresholds: e.target.value }))}
                placeholder="0.50,0.53,0.55,0.60,0.65,0.70,0.75,0.80"
              />
            </div>
          </div>

          <Button 
            onClick={runBacktest}
            disabled={backtesting}
            className="w-full"
          >
            {backtesting ? (
              <>
                <Activity className="mr-2 h-4 w-4 animate-spin" />
                Executando Backtesting...
              </>
            ) : (
              <>
                <BarChart3 className="mr-2 h-4 w-4" />
                Executar Backtesting
              </>
            )}
          </Button>

          {/* Resultados do Backtesting */}
          {backtestResults && !backtestResults.error && (
            <div className="space-y-4 mt-6">
              <div className="bg-green-50 p-4 rounded-lg">
                <h4 className="font-semibold text-green-800 mb-2">✅ Recomendação</h4>
                <p className="text-green-700">
                  <strong>Melhor Threshold:</strong> {(backtestResults.best_threshold * 100).toFixed(1)}% <br/>
                  <strong>Melhoria Esperada:</strong> {backtestResults.recommendation?.expected_improvement} <br/>
                  <strong>Explicação:</strong> {backtestResults.recommendation?.rationale}
                </p>
                {backtestResults.best_threshold !== config.river_threshold && (
                  <Button
                    size="sm"
                    className="mt-2"
                    onClick={() => {
                      setTempThreshold(backtestResults.best_threshold);
                      updateThreshold(backtestResults.best_threshold);
                    }}
                  >
                    Aplicar Threshold Recomendado
                  </Button>
                )}
              </div>

              {/* Gráfico de Resultados */}
              {backtestResults.results?.length > 0 && (
                <div className="bg-white p-4 border rounded-lg">
                  <h4 className="font-semibold mb-4">Performance por Threshold</h4>
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={backtestResults.results}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis 
                          dataKey="threshold" 
                          tickFormatter={(value) => `${(value * 100).toFixed(0)}%`}
                        />
                        <YAxis 
                          yAxisId="winRate"
                          domain={[0, 1]}
                          tickFormatter={(value) => `${(value * 100).toFixed(0)}%`}
                        />
                        <YAxis 
                          yAxisId="trades" 
                          orientation="right"
                          domain={[0, 'dataMax']}
                        />
                        <Tooltip 
                          formatter={(value, name) => {
                            if (name === 'win_rate') return [`${(value * 100).toFixed(1)}%`, 'Win Rate'];
                            if (name === 'total_trades') return [value, 'Total Trades'];
                            if (name === 'expected_value') return [value.toFixed(3), 'Expected Value'];
                            return [value, name];
                          }}
                          labelFormatter={(value) => `Threshold: ${(value * 100).toFixed(1)}%`}
                        />
                        <Line 
                          yAxisId="winRate"
                          type="monotone" 
                          dataKey="win_rate" 
                          stroke="#10b981" 
                          strokeWidth={2}
                          name="win_rate"
                        />
                        <Line 
                          yAxisId="trades"
                          type="monotone" 
                          dataKey="total_trades" 
                          stroke="#3b82f6" 
                          strokeWidth={2}
                          name="total_trades"
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* Tabela de Resultados Detalhados */}
              <div className="bg-white border rounded-lg overflow-hidden">
                <div className="px-4 py-2 bg-gray-50 border-b">
                  <h4 className="font-semibold">Resultados Detalhados</h4>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-3 py-2 text-left">Threshold</th>
                        <th className="px-3 py-2 text-left">Win Rate</th>
                        <th className="px-3 py-2 text-left">Total Trades</th>
                        <th className="px-3 py-2 text-left">Trades/Dia</th>
                        <th className="px-3 py-2 text-left">Expected Value</th>
                        <th className="px-3 py-2 text-left">Max Drawdown</th>
                      </tr>
                    </thead>
                    <tbody>
                      {backtestResults.results?.map((result, idx) => (
                        <tr key={idx} className={result.threshold === backtestResults.best_threshold ? "bg-green-50" : ""}>
                          <td className="px-3 py-2 font-mono">
                            {(result.threshold * 100).toFixed(1)}%
                            {result.threshold === backtestResults.best_threshold && (
                              <Badge size="sm" className="ml-1">Melhor</Badge>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            <span className={result.win_rate > 0.6 ? "text-green-600 font-semibold" : result.win_rate > 0.4 ? "text-yellow-600" : "text-red-600"}>
                              {(result.win_rate * 100).toFixed(1)}%
                            </span>
                          </td>
                          <td className="px-3 py-2">{result.total_trades}</td>
                          <td className="px-3 py-2">{result.avg_trades_per_day.toFixed(1)}</td>
                          <td className="px-3 py-2">
                            <span className={result.expected_value > 0 ? "text-green-600" : "text-red-600"}>
                              {result.expected_value.toFixed(3)}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-red-600">
                            {result.max_drawdown.toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {backtestResults?.error && (
            <div className="bg-red-50 p-4 rounded-lg">
              <div className="flex items-center gap-2 text-red-800">
                <AlertCircle className="h-4 w-4" />
                <span className="font-semibold">Erro no Backtesting</span>
              </div>
              <p className="text-red-700 mt-1">{backtestResults.error}</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Performance em Tempo Real */}
      {performance && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5" />
              Performance River em Tempo Real
              <Badge variant="outline" className="ml-auto">
                Atualiza a cada 5s
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* River Model Stats */}
              <div>
                <h4 className="font-semibold mb-3">Modelo River</h4>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <span>Samples Treinados:</span>
                    <span className="font-mono">{performance.river_model?.samples || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Acurácia:</span>
                    <span className="font-mono text-green-600">
                      {((performance.river_model?.accuracy || 0) * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span>Log Loss:</span>
                    <span className="font-mono">
                      {(performance.river_model?.logloss || 0).toFixed(4)}
                    </span>
                  </div>
                </div>
              </div>

              {/* Strategy Stats */}
              <div>
                <h4 className="font-semibold mb-3">Performance Estratégia</h4>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <span>Total Trades:</span>
                    <span className="font-mono">{performance.strategy_performance?.total_trades || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Wins / Losses:</span>
                    <span className="font-mono">
                      <span className="text-green-600">{performance.strategy_performance?.wins || 0}</span>
                      {' / '}
                      <span className="text-red-600">{performance.strategy_performance?.losses || 0}</span>
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span>PnL Diário:</span>
                    <span className={`font-mono ${(performance.strategy_performance?.daily_pnl || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {(performance.strategy_performance?.daily_pnl || 0).toFixed(2)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span>Último Sinal:</span>
                    <span className="font-mono">
                      {performance.last_signal || 'Nenhum'}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default RiverThresholdPanel;