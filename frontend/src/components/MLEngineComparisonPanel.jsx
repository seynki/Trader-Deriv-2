import React, { useState, useEffect } from 'react';
import { Card, CardHeader, CardContent, CardTitle } from './ui/card';
import { Button } from './ui/button';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from './ui/select';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { Switch } from './ui/switch';
import { useToast } from '../hooks/use-toast';
import { Play, Square, Brain, TrendingUp, TrendingDown, Activity, Zap } from 'lucide-react';
import axios from 'axios';

function backendBase() {
  const env = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/+$/, "");
  try {
    const u = new URL(env);
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

const MLEngineComparisonPanel = () => {
  const [mlEngineStatus, setMLEngineStatus] = useState(null);
  const [riverStatus, setRiverStatus] = useState(null);
  const [isTraining, setIsTraining] = useState(false);
  const [predictions, setPredictions] = useState({ ml_engine: null, river: null });
  const [settings, setSettings] = useState({
    symbol: 'R_100',
    timeframe: '1m',
    count: 500,
    horizon: 3,
    seq_len: 32,
    min_conf: 0.2,
    bankroll: 1000
  });
  const [comparisonMode, setComparisonMode] = useState(false);
  const [loading, setLoading] = useState(false);
  
  const { toast } = useToast();

  // Fetch status of both systems
  const fetchStatus = async () => {
    try {
      const [mlEngineRes, riverRes] = await Promise.all([
        axios.get(`${API}/ml/engine/status`),
        axios.get(`${API}/ml/river/status`)
      ]);
      
      setMLEngineStatus(mlEngineRes.data);
      setRiverStatus(riverRes.data);
    } catch (error) {
      console.error('Error fetching status:', error);
    }
  };

  // Train ML Engine
  const trainMLEngine = async () => {
    setIsTraining(true);
    try {
      const response = await axios.post(`${API}/ml/engine/train`, {
        symbol: settings.symbol,
        timeframe: settings.timeframe,
        count: settings.count,
        horizon: settings.horizon,
        seq_len: settings.seq_len
      });

      if (response.data.success) {
        toast({
          title: "✅ ML Engine Treinado",
          description: `Transformer e LGB treinados com ${response.data.candles_used} candles e ${response.data.features_count} features`,
        });
        fetchStatus();
      }
    } catch (error) {
      toast({
        title: "❌ Erro no Treinamento",
        description: error.response?.data?.detail || error.message,
        variant: "destructive"
      });
    } finally {
      setIsTraining(false);
    }
  };

  // Train River model
  const trainRiver = async () => {
    try {
      // Create sample CSV for River training
      const sampleCSV = `datetime,open,high,low,close,volume
2024-01-01T00:00:00Z,100.0,100.5,99.8,100.2,1000
2024-01-01T00:01:00Z,100.2,100.7,100.0,100.4,1100
2024-01-01T00:02:00Z,100.4,100.8,100.1,100.6,900
2024-01-01T00:03:00Z,100.6,101.0,100.3,100.8,1200
2024-01-01T00:04:00Z,100.8,101.2,100.5,101.0,800`;

      const response = await axios.post(`${API}/ml/river/train_csv`, {
        csv_text: sampleCSV
      });

      toast({
        title: "✅ River Treinado",
        description: `Modelo online treinado com ${response.data.samples} amostras`,
      });
      fetchStatus();
    } catch (error) {
      toast({
        title: "❌ Erro no Treinamento River",
        description: error.response?.data?.detail || error.message,
        variant: "destructive"
      });
    }
  };

  // Compare predictions
  const comparePredictions = async () => {
    setLoading(true);
    try {
      const promises = [];

      // ML Engine prediction
      if (mlEngineStatus?.models_trained) {
        promises.push(
          axios.post(`${API}/ml/engine/predict`, {
            symbol: settings.symbol,
            count: 100
          }).then(res => ({ type: 'ml_engine', data: res.data }))
        );
      }

      // River prediction (need to create a sample candle)
      if (riverStatus?.initialized) {
        promises.push(
          axios.post(`${API}/ml/river/predict`, {
            datetime: new Date().toISOString(),
            open: 1000,
            high: 1005,
            low: 995,
            close: 1002,
            volume: 1000
          }).then(res => ({ type: 'river', data: res.data }))
        );
      }

      const results = await Promise.all(promises);
      const newPredictions = { ml_engine: null, river: null };
      
      results.forEach(result => {
        newPredictions[result.type] = result.data;
      });
      
      setPredictions(newPredictions);
      
      toast({
        title: "🔍 Predições Comparadas",
        description: "Predições de ambos os sistemas atualizadas"
      });
      
    } catch (error) {
      toast({
        title: "❌ Erro na Comparação",
        description: error.response?.data?.detail || error.message,
        variant: "destructive"
      });
    } finally {
      setLoading(false);
    }
  };

  // Paper trade comparison
  const paperTradeComparison = async () => {
    if (!comparisonMode) return;
    
    try {
      const promises = [];

      // ML Engine decision
      if (mlEngineStatus?.models_trained) {
        promises.push(
          axios.post(`${API}/ml/engine/decide_trade`, {
            symbol: settings.symbol,
            count: 100,
            dry_run: true,
            min_conf: settings.min_conf,
            bankroll: settings.bankroll
          }).then(res => ({ type: 'ml_engine', data: res.data }))
        );
      }

      // River decision
      if (riverStatus?.initialized) {
        promises.push(
          axios.post(`${API}/ml/river/decide_trade`, {
            symbol: settings.symbol,
            dry_run: true,
            candle: {
              datetime: new Date().toISOString(),
              open: 1000,
              high: 1005,
              low: 995,
              close: 1002,
              volume: 1000
            }
          }).then(res => ({ type: 'river', data: res.data }))
        );
      }

      const results = await Promise.all(promises);
      
      // Process and display trade decisions
      results.forEach(result => {
        const system = result.type === 'ml_engine' ? 'ML Engine' : 'River';
        const decision = result.data.decision || result.data;
        
        toast({
          title: `🎯 ${system} Decision`,
          description: `${decision.decision || decision.direction} - Conf: ${(decision.confidence || 0).toFixed(3)} - Stake: $${(decision.recommended_stake || decision.stake || 0).toFixed(2)}`
        });
      });
      
    } catch (error) {
      toast({
        title: "❌ Erro no Paper Trading",
        description: error.response?.data?.detail || error.message,
        variant: "destructive"
      });
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 10000); // Update every 10 seconds
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (comparisonMode) {
      const interval = setInterval(paperTradeComparison, 30000); // Compare every 30 seconds
      return () => clearInterval(interval);
    }
  }, [comparisonMode, mlEngineStatus, riverStatus]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <Brain className="h-6 w-6" />
          ML Engine vs River Comparison
        </h2>
        <div className="flex items-center gap-2">
          <span className="text-sm">Modo Comparação:</span>
          <Switch
            checked={comparisonMode}
            onCheckedChange={setComparisonMode}
          />
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid md:grid-cols-2 gap-4">
        {/* ML Engine Status */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Zap className="h-5 w-5 text-blue-500" />
              ML Engine (Transformer + LGB)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center">
              <span>Status:</span>
              <Badge variant={mlEngineStatus?.initialized ? "default" : "secondary"}>
                {mlEngineStatus?.initialized ? "Inicializado" : "Não Inicializado"}
              </Badge>
            </div>
            <div className="flex justify-between items-center">
              <span>Modelos Treinados:</span>
              <Badge variant={mlEngineStatus?.models_trained ? "default" : "outline"}>
                {mlEngineStatus?.models_trained ? "Sim" : "Não"}
              </Badge>
            </div>
            {mlEngineStatus?.models_trained && (
              <>
                <div className="flex justify-between items-center">
                  <span>Transformer:</span>
                  <Badge variant={mlEngineStatus?.transformer_available ? "default" : "outline"}>
                    {mlEngineStatus?.transformer_available ? "✓" : "✗"}
                  </Badge>
                </div>
                <div className="flex justify-between items-center">
                  <span>LightGBM:</span>
                  <Badge variant={mlEngineStatus?.lgb_available ? "default" : "outline"}>
                    {mlEngineStatus?.lgb_available ? "✓" : "✗"}
                  </Badge>
                </div>
                <div className="flex justify-between items-center">
                  <span>Features:</span>
                  <Badge variant="outline">{mlEngineStatus?.features_count || 0}</Badge>
                </div>
              </>
            )}
            <Button 
              onClick={trainMLEngine} 
              disabled={isTraining}
              className="w-full"
            >
              {isTraining ? "Treinando..." : "Treinar ML Engine"}
            </Button>
          </CardContent>
        </Card>

        {/* River Status */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-green-500" />
              River (Online Learning)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center">
              <span>Status:</span>
              <Badge variant={riverStatus?.initialized ? "default" : "secondary"}>
                {riverStatus?.initialized ? "Inicializado" : "Não Inicializado"}
              </Badge>
            </div>
            <div className="flex justify-between items-center">
              <span>Amostras:</span>
              <Badge variant="outline">{riverStatus?.samples || 0}</Badge>
            </div>
            {riverStatus?.acc !== null && (
              <div className="flex justify-between items-center">
                <span>Acurácia:</span>
                <Badge variant="outline">{(riverStatus?.acc * 100 || 0).toFixed(1)}%</Badge>
              </div>
            )}
            {riverStatus?.logloss !== null && (
              <div className="flex justify-between items-center">
                <span>Log Loss:</span>
                <Badge variant="outline">{riverStatus?.logloss?.toFixed(3) || 'N/A'}</Badge>
              </div>
            )}
            <Button 
              onClick={trainRiver}
              className="w-full"
            >
              Treinar River
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Settings */}
      <Card>
        <CardHeader>
          <CardTitle>Configurações de Teste</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid md:grid-cols-3 gap-4">
            <div>
              <label className="text-sm font-medium">Símbolo</label>
              <Select value={settings.symbol} onValueChange={(value) => setSettings({...settings, symbol: value})}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="R_10">R_10</SelectItem>
                  <SelectItem value="R_25">R_25</SelectItem>
                  <SelectItem value="R_50">R_50</SelectItem>
                  <SelectItem value="R_75">R_75</SelectItem>
                  <SelectItem value="R_100">R_100</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-sm font-medium">Timeframe</label>
              <Select value={settings.timeframe} onValueChange={(value) => setSettings({...settings, timeframe: value})}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1m">1 minuto</SelectItem>
                  <SelectItem value="5m">5 minutos</SelectItem>
                  <SelectItem value="15m">15 minutos</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-sm font-medium">Candles para Treino</label>
              <Input 
                type="number" 
                value={settings.count}
                onChange={(e) => setSettings({...settings, count: parseInt(e.target.value)})}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Horizonte</label>
              <Input 
                type="number" 
                value={settings.horizon}
                onChange={(e) => setSettings({...settings, horizon: parseInt(e.target.value)})}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Seq Length</label>
              <Input 
                type="number" 
                value={settings.seq_len}
                onChange={(e) => setSettings({...settings, seq_len: parseInt(e.target.value)})}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Min Confiança</label>
              <Input 
                type="number" 
                step="0.1"
                value={settings.min_conf}
                onChange={(e) => setSettings({...settings, min_conf: parseFloat(e.target.value)})}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Comparison Actions */}
      <div className="grid md:grid-cols-2 gap-4">
        <Button 
          onClick={comparePredictions}
          disabled={loading || (!mlEngineStatus?.models_trained && !riverStatus?.initialized)}
          className="flex items-center gap-2"
        >
          <TrendingUp className="h-4 w-4" />
          {loading ? "Comparando..." : "Comparar Predições"}
        </Button>
        
        <Button 
          onClick={paperTradeComparison}
          disabled={!comparisonMode || (!mlEngineStatus?.models_trained && !riverStatus?.initialized)}
          className="flex items-center gap-2"
          variant="outline"
        >
          <Play className="h-4 w-4" />
          Paper Trade Teste
        </Button>
      </div>

      {/* Predictions Comparison */}
      {(predictions.ml_engine || predictions.river) && (
        <div className="grid md:grid-cols-2 gap-4">
          {predictions.ml_engine && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Zap className="h-4 w-4 text-blue-500" />
                  ML Engine Prediction
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <span>Probabilidade:</span>
                    <Badge>{(predictions.ml_engine.prediction?.probability * 100 || 0).toFixed(1)}%</Badge>
                  </div>
                  <div className="flex justify-between">
                    <span>Transformer:</span>
                    <Badge variant="outline">{(predictions.ml_engine.prediction?.prob_transformer * 100 || 0).toFixed(1)}%</Badge>
                  </div>
                  <div className="flex justify-between">
                    <span>LightGBM:</span>
                    <Badge variant="outline">{(predictions.ml_engine.prediction?.prob_lgb * 100 || 0).toFixed(1)}%</Badge>
                  </div>
                  <div className="flex justify-between">
                    <span>Confiança:</span>
                    <Badge>{(predictions.ml_engine.prediction?.confidence * 100 || 0).toFixed(1)}%</Badge>
                  </div>
                  <div className="flex justify-between">
                    <span>Direção:</span>
                    <Badge variant={predictions.ml_engine.prediction?.direction === 'CALL' ? 'default' : 'destructive'}>
                      {predictions.ml_engine.prediction?.direction === 'CALL' ? (
                        <><TrendingUp className="h-3 w-3 mr-1" /> CALL</>
                      ) : (
                        <><TrendingDown className="h-3 w-3 mr-1" /> PUT</>
                      )}
                    </Badge>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {predictions.river && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-green-500" />
                  River Prediction
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <span>Prob Up:</span>
                    <Badge>{(predictions.river.prob_up * 100 || 0).toFixed(1)}%</Badge>
                  </div>
                  <div className="flex justify-between">
                    <span>Classe Predita:</span>
                    <Badge variant="outline">{predictions.river.pred_class}</Badge>
                  </div>
                  <div className="flex justify-between">
                    <span>Sinal:</span>
                    <Badge variant={predictions.river.signal === 'LONG' ? 'default' : 'destructive'}>
                      {predictions.river.signal === 'LONG' ? (
                        <><TrendingUp className="h-3 w-3 mr-1" /> LONG</>
                      ) : (
                        <><TrendingDown className="h-3 w-3 mr-1" /> SHORT</>
                      )}
                    </Badge>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {comparisonMode && (
        <Card className="border-green-200">
          <CardHeader>
            <CardTitle className="text-green-700">🔄 Modo Comparação Ativo</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-green-600">
              O sistema está comparando automaticamente as decisões de trade dos dois modelos a cada 30 segundos.
              Todas as execuções são em modo paper trading (dry_run=true).
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default MLEngineComparisonPanel;