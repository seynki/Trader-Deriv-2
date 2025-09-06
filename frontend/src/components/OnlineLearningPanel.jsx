import React, { useEffect, useState, useRef } from "react";
import axios from "axios";
import { Card, CardHeader, CardContent, CardTitle } from "./ui/card";
import { Button } from "./ui/button";
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from "./ui/select";
import { Input } from "./ui/input";
import { Badge } from "./ui/badge";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, Cell } from 'recharts';

const BACKEND_BASE = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/+$/, "");
const API = BACKEND_BASE.endsWith("/api") ? BACKEND_BASE : `${BACKEND_BASE}/api`;

export default function OnlineLearningPanel() {
  const [onlineModels, setOnlineModels] = useState([]);
  const [onlineProgress, setOnlineProgress] = useState(null);
  const [creatingModel, setCreatingModel] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");
  const [modelStatus, setModelStatus] = useState(null);
  
  // Model creation params
  const [newModelParams, setNewModelParams] = useState({
    model_id: `online_model_${Date.now()}`,
    source: "file",
    symbol: "R_100",
    timeframe: "3m",
    count: 1000,
    horizon: 3,
    threshold: 0.003,
    model_type: "sgd"
  });

  const refreshInterval = useRef(null);

  const fetchOnlineModels = async () => {
    try {
      const { data } = await axios.get(`${API}/ml/online/list`);
      setOnlineModels(data.models || []);
      return data.models;
    } catch (error) {
      console.error("Failed to fetch online models:", error);
      return [];
    }
  };

  const fetchOnlineProgress = async () => {
    try {
      const { data } = await axios.get(`${API}/ml/online/progress`);
      setOnlineProgress(data);
    } catch (error) {
      console.error("Failed to fetch online progress:", error);
    }
  };

  const fetchModelStatus = async (modelId) => {
    if (!modelId) return;
    try {
      const { data } = await axios.get(`${API}/ml/online/status/${modelId}`);
      setModelStatus(data);
    } catch (error) {
      console.error("Failed to fetch model status:", error);
      setModelStatus(null);
    }
  };

  useEffect(() => {
    fetchOnlineModels();
    fetchOnlineProgress();
    
    // Set up polling for real-time updates
    refreshInterval.current = setInterval(() => {
      fetchOnlineProgress();
      if (selectedModel) {
        fetchModelStatus(selectedModel);
      }
    }, 3000); // Update every 3 seconds

    return () => {
      if (refreshInterval.current) {
        clearInterval(refreshInterval.current);
      }
    };
  }, [selectedModel]);

  const createOnlineModel = async () => {
    setCreatingModel(true);
    try {
      const { data } = await axios.post(`${API}/ml/online/create`, null, {
        params: newModelParams
      });
      
      // Refresh models list
      const models = await fetchOnlineModels();
      if (models.length > 0) {
        setSelectedModel(newModelParams.model_id);
      }
      
      alert(`Modelo online criado com sucesso!\nFeatures: ${data.features_count}\nSamples: ${data.training_samples}`);
    } catch (error) {
      const errorMsg = error.response?.data?.detail || error.message;
      alert(`Erro ao criar modelo online: ${errorMsg}`);
    } finally {
      setCreatingModel(false);
    }
  };

  const initializeOnlineModels = async () => {
    setCreatingModel(true);
    try {
      const { data } = await axios.post(`${API}/ml/online/initialize`);
      
      // Refresh models list
      await fetchOnlineModels();
      await fetchOnlineProgress();
      
      if (data.models_created > 0) {
        alert(`${data.models_created} modelo(s) online inicializado(s) automaticamente!\nModelos: ${data.models.join(', ')}`);
      } else {
        alert("Nenhum modelo foi criado (pode precisar de mais dados históricos)");
      }
    } catch (error) {
      const errorMsg = error.response?.data?.detail || error.message;
      alert(`Erro na inicialização automática: ${errorMsg}`);
    } finally {
      setCreatingModel(false);
    }
  };

  const getTrendIcon = (trend) => {
    switch (trend) {
      case "improving": return "📈";
      case "declining": return "📉";
      default: return "➡️";
    }
  };

  const formatPerformanceHistory = (history) => {
    if (!history || history.length === 0) return [];
    
    return history.slice(-10).map((item, index) => ({
      step: index + 1,
      accuracy: (item.accuracy * 100).toFixed(1),
      precision: (item.precision * 100).toFixed(1),
      timestamp: new Date(item.timestamp).toLocaleTimeString()
    }));
  };

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>🧠 Aprendizado Online</span>
          <div className="flex items-center gap-3">
            {onlineProgress && (
              <Badge variant="secondary">
                {onlineProgress.active_models} modelo(s) ativo(s)
              </Badge>
            )}
            <Badge variant={onlineProgress?.total_updates > 0 ? "default" : "outline"}>
              {onlineProgress?.total_updates || 0} atualizações
            </Badge>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        
        {/* Model Creation Section */}
        <div className="border rounded-lg p-4 bg-slate-50 dark:bg-slate-900">
          <h3 className="font-medium mb-3">Criar Novo Modelo Online</h3>
          <div className="flex flex-wrap gap-3 items-center mb-3">
            <div className="flex items-center gap-2">
              <span className="text-sm opacity-80">ID do Modelo</span>
              <Input 
                className="w-48" 
                value={newModelParams.model_id}
                onChange={(e) => setNewModelParams(prev => ({...prev, model_id: e.target.value}))}
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm opacity-80">Fonte</span>
              <Select 
                value={newModelParams.source} 
                onValueChange={(value) => setNewModelParams(prev => ({...prev, source: value}))}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="file">CSV</SelectItem>
                  <SelectItem value="deriv">Deriv</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm opacity-80">Tipo</span>
              <Select 
                value={newModelParams.model_type} 
                onValueChange={(value) => setNewModelParams(prev => ({...prev, model_type: value}))}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="sgd">SGD</SelectItem>
                  <SelectItem value="passive_aggressive">Passive Aggressive</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-2">
              <Button 
                onClick={createOnlineModel} 
                disabled={creatingModel}
                className="bg-blue-600 hover:bg-blue-700"
              >
                {creatingModel ? "Criando..." : "Criar Modelo"}
              </Button>
              <Button 
                onClick={initializeOnlineModels} 
                disabled={creatingModel}
                className="bg-green-600 hover:bg-green-700"
              >
                {creatingModel ? "Inicializando..." : "🚀 Auto-Inicializar"}
              </Button>
            </div>
          </div>
        </div>

        {/* Model Selection and Status */}
        {onlineModels.length > 0 && (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <span className="text-sm opacity-80">Modelo Selecionado:</span>
              <Select value={selectedModel} onValueChange={setSelectedModel}>
                <SelectTrigger className="w-64">
                  <SelectValue placeholder="Selecione um modelo" />
                </SelectTrigger>
                <SelectContent>
                  {onlineModels.map(modelId => (
                    <SelectItem key={modelId} value={modelId}>{modelId}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Model Status Display */}
            {selectedModel && modelStatus && modelStatus.status === 'active' && (
              <div className="border rounded-lg p-4 bg-green-50 dark:bg-green-900/20">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-medium text-green-800 dark:text-green-200">
                    Status do Modelo: {selectedModel}
                  </h3>
                  <Badge variant="outline" className="bg-green-100 text-green-800">
                    ATIVO
                  </Badge>
                </div>
                
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                  <div className="text-center">
                    <div className="text-2xl font-bold text-green-600">
                      {modelStatus.model_info?.update_count || 0}
                    </div>
                    <div className="text-sm opacity-70">Updates</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold text-blue-600">
                      {modelStatus.model_info?.features_count || 0}
                    </div>
                    <div className="text-sm opacity-70">Features</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold text-purple-600">
                      {modelStatus.performance_history?.slice(-1)[0]?.accuracy ? 
                        (modelStatus.performance_history.slice(-1)[0].accuracy * 100).toFixed(1) + '%' : 
                        'N/A'}
                    </div>
                    <div className="text-sm opacity-70">Precisão Atual</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl font-bold text-orange-600">
                      {modelStatus.performance_history?.length || 0}
                    </div>
                    <div className="text-sm opacity-70">Avaliações</div>
                  </div>
                </div>

                {/* Performance Chart */}
                {modelStatus.performance_history && modelStatus.performance_history.length > 1 && (
                  <div className="mt-4">
                    <h4 className="font-medium mb-2">Evolução da Performance</h4>
                    <div className="h-64">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={formatPerformanceHistory(modelStatus.performance_history)}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="step" />
                          <YAxis domain={[0, 100]} />
                          <Tooltip 
                            formatter={(value, name) => [`${value}%`, name === 'accuracy' ? 'Acurácia' : 'Precisão']}
                          />
                          <Line 
                            type="monotone" 
                            dataKey="accuracy" 
                            stroke="#3B82F6" 
                            strokeWidth={2}
                            name="accuracy"
                            dot={{ r: 4 }}
                          />
                          <Line 
                            type="monotone" 
                            dataKey="precision" 
                            stroke="#EF4444" 
                            strokeWidth={2}
                            name="precision"
                            dot={{ r: 4 }}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Overall Progress Display */}
        {onlineProgress && onlineProgress.models_detail && onlineProgress.models_detail.length > 0 && (
          <div className="border rounded-lg p-4">
            <h3 className="font-medium mb-3">Visão Geral dos Modelos</h3>
            <div className="space-y-3">
              {onlineProgress.models_detail.map((model, index) => (
                <div key={model.model_id} className="flex items-center justify-between p-3 bg-slate-50 dark:bg-slate-800 rounded">
                  <div className="flex items-center gap-3">
                    <div className="text-sm font-medium">{model.model_id}</div>
                    <Badge variant="outline" className="text-xs">
                      {model.features_count} features
                    </Badge>
                  </div>
                  <div className="flex items-center gap-4 text-sm">
                    <div className="text-center">
                      <div className="font-bold text-green-600">{model.update_count}</div>
                      <div className="opacity-70">Updates</div>
                    </div>
                    <div className="text-center">
                      <div className="font-bold text-blue-600">
                        {(model.current_accuracy * 100).toFixed(1)}%
                      </div>
                      <div className="opacity-70">Precisão</div>
                    </div>
                    <div className="text-center">
                      <div className="text-lg">
                        {getTrendIcon(model.improvement_trend)}
                      </div>
                      <div className="opacity-70 capitalize">{model.improvement_trend}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* No Models Message */}
        {onlineModels.length === 0 && (
          <div className="text-center py-8 text-slate-500">
            <div className="text-4xl mb-2">🤖</div>
            <div className="text-lg mb-2">Nenhum modelo online ativo</div>
            <div className="text-sm">Crie um modelo online para começar o aprendizado automático</div>
          </div>
        )}

        <div className="text-xs opacity-70 border-t pt-3">
          <div className="mb-1">
            <strong>💡 Como funciona o Aprendizado Online:</strong>
          </div>
          <ul className="list-disc list-inside space-y-1 ml-2">
            <li>O modelo aprende automaticamente com cada trade executado</li>
            <li>A performance é avaliada periodicamente e mostrada no gráfico</li>
            <li>Modelos SGD são rápidos para atualizações incrementais</li>
            <li>Updates acontecem a cada 5 trades para estabilidade</li>
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}