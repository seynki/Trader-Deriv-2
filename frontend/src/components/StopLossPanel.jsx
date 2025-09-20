import React, { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Slider } from './ui/slider';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { 
  AlertTriangle, 
  Shield, 
  Activity, 
  Settings, 
  PlayCircle,
  StopCircle,
  TrendingDown,
  CheckCircle,
  XCircle,
  Timer,
  DollarSign
} from 'lucide-react';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8001/api';

const StopLossPanel = () => {
  // Estados principais
  const [stopLossStatus, setStopLossStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  
  // Estados de configuração temporária
  const [tempConfig, setTempConfig] = useState({
    enable_dynamic_stop_loss: true,
    stop_loss_percentage: 0.5,
    stop_loss_check_interval: 2
  });

  // Fetch do status do stop loss
  const fetchStopLossStatus = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/strategy/stop_loss/status`);
      setStopLossStatus(data);
      
      // Atualizar configuração temporária
      setTempConfig({
        enable_dynamic_stop_loss: data.dynamic_stop_loss?.enabled || false,
        stop_loss_percentage: data.dynamic_stop_loss?.percentage || 0.5,
        stop_loss_check_interval: data.dynamic_stop_loss?.check_interval || 2
      });
    } catch (error) {
      console.error('Erro ao obter status stop loss:', error);
    }
    setLoading(false);
  }, []);

  // Aplicar configurações
  const applyConfig = async () => {
    setUpdating(true);
    try {
      const { data } = await axios.post(`${API}/strategy/optimize/apply`, tempConfig);
      console.log('Configuração aplicada:', data.message);
      
      // Refresh do status
      await fetchStopLossStatus();
    } catch (error) {
      console.error('Erro ao aplicar configuração:', error);
    }
    setUpdating(false);
  };

  // Testar sistema de stop loss
  const testStopLoss = async () => {
    setTesting(true);
    try {
      const { data } = await axios.post(`${API}/strategy/stop_loss/test`);
      setTestResult(data);
    } catch (error) {
      console.error('Erro ao testar stop loss:', error);
      setTestResult({ error: error.response?.data?.detail || error.message });
    }
    setTesting(false);
  };

  // Auto refresh
  useEffect(() => {
    fetchStopLossStatus();
    
    // Refresh a cada 10 segundos
    const interval = setInterval(fetchStopLossStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchStopLossStatus]);

  if (!stopLossStatus && loading) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="flex items-center justify-center">
            <Activity className="mr-2 h-4 w-4 animate-spin" />
            Carregando status do Stop Loss...
          </div>
        </CardContent>
      </Card>
    );
  }

  const dynamicStopLoss = stopLossStatus?.dynamic_stop_loss || {};
  const technicalStopLoss = stopLossStatus?.technical_stop_loss || {};
  const activeContracts = stopLossStatus?.active_contracts_details || [];

  return (
    <div className="space-y-6">
      {/* Status Geral */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5" />
            Sistema de Stop Loss
            <Badge variant={dynamicStopLoss.enabled ? "default" : "outline"}>
              {dynamicStopLoss.enabled ? "Ativo" : "Inativo"}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="text-center">
              <div className="text-3xl font-bold text-red-600">
                {dynamicStopLoss.percentage_display || "50%"}
              </div>
              <div className="text-sm text-gray-500">Limite de Perda</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-blue-600">
                {dynamicStopLoss.check_interval || 2}s
              </div>
              <div className="text-sm text-gray-500">Intervalo de Verificação</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-purple-600">
                {dynamicStopLoss.active_contracts || 0}
              </div>
              <div className="text-sm text-gray-500">Contratos Monitorados</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Configuração do Stop Loss Dinâmico */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            Stop Loss Dinâmico (50% por Trade)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Toggle para habilitar/desabilitar */}
          <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
            <div>
              <div className="font-medium">Habilitar Stop Loss Dinâmico</div>
              <div className="text-sm text-gray-500">
                Monitora cada trade individual e vende automaticamente quando atinge o limite de perda
              </div>
            </div>
            <Button
              variant={tempConfig.enable_dynamic_stop_loss ? "default" : "outline"}
              onClick={() => setTempConfig(prev => ({ ...prev, enable_dynamic_stop_loss: !prev.enable_dynamic_stop_loss }))}
            >
              {tempConfig.enable_dynamic_stop_loss ? (
                <>
                  <CheckCircle className="mr-2 h-4 w-4" />
                  Habilitado
                </>
              ) : (
                <>
                  <XCircle className="mr-2 h-4 w-4" />
                  Desabilitado
                </>
              )}
            </Button>
          </div>

          {/* Configuração de Percentual */}
          <div className="space-y-4">
            <Label className="text-base">
              Percentual de Stop Loss: {(tempConfig.stop_loss_percentage * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[tempConfig.stop_loss_percentage]}
              onValueChange={(value) => setTempConfig(prev => ({ ...prev, stop_loss_percentage: value[0] }))}
              min={0.1}
              max={1.0}
              step={0.05}
              className="w-full"
            />
            <div className="flex justify-between text-xs text-gray-500">
              <span>10% (Agressivo)</span>
              <span>100% (Sem Stop Loss)</span>
            </div>
          </div>

          {/* Configuração de Intervalo */}
          <div className="space-y-2">
            <Label>Intervalo de Verificação (segundos)</Label>
            <Input
              type="number"
              value={tempConfig.stop_loss_check_interval}
              onChange={(e) => setTempConfig(prev => ({ ...prev, stop_loss_check_interval: parseInt(e.target.value) || 2 }))}
              min="1"
              max="30"
              className="w-full"
            />
            <div className="text-xs text-gray-500">
              Frequência com que o sistema verifica os contratos (1-30 segundos)
            </div>
          </div>

          {/* Explicação */}
          <div className="bg-yellow-50 p-4 rounded-lg">
            <div className="flex items-start gap-2">
              <AlertTriangle className="h-4 w-4 text-yellow-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm">
                <p className="font-semibold text-yellow-800 mb-1">Como funciona:</p>
                <p className="text-yellow-700">
                  Se um trade atingir <strong>{(tempConfig.stop_loss_percentage * 100).toFixed(0)}% de perda</strong> do valor investido, 
                  o sistema automaticamente venderá o contrato para limitar a perda. 
                  Por exemplo: se investir $1, o sistema vende quando a perda chegar a $-{tempConfig.stop_loss_percentage.toFixed(2)}.
                </p>
              </div>
            </div>
          </div>

          {/* Botões de Ação */}
          <div className="flex gap-3">
            <Button 
              onClick={applyConfig}
              disabled={updating}
              className="flex-1"
            >
              {updating ? (
                <>
                  <Activity className="mr-2 h-4 w-4 animate-spin" />
                  Aplicando...
                </>
              ) : (
                <>
                  <Settings className="mr-2 h-4 w-4" />
                  Aplicar Configuração
                </>
              )}
            </Button>
            <Button 
              variant="outline" 
              onClick={() => setTempConfig({
                enable_dynamic_stop_loss: true,
                stop_loss_percentage: 0.5,
                stop_loss_check_interval: 2
              })}
            >
              Reset Padrão
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Stop Loss Técnico */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <TrendingDown className="h-5 w-5" />
            Stop Loss Técnico (Indicadores)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded">
              <div>
                <div className="font-medium">Sistema Técnico</div>
                <div className="text-sm text-gray-500">ADX, RSI, MACD</div>
              </div>
              <Badge variant={technicalStopLoss.enabled ? "default" : "outline"}>
                {technicalStopLoss.enabled ? "Ativo" : "Inativo"}
              </Badge>
            </div>
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded">
              <div>
                <div className="font-medium">Divergência MACD</div>
                <div className="text-sm text-gray-500">Detecta reversões</div>
              </div>
              <Badge variant={technicalStopLoss.macd_divergence ? "default" : "outline"}>
                {technicalStopLoss.macd_divergence ? "Ativo" : "Inativo"}
              </Badge>
            </div>
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded">
              <div>
                <div className="font-medium">RSI Overextended</div>
                <div className="text-sm text-gray-500">Sobrecompra/venda</div>
              </div>
              <Badge variant={technicalStopLoss.rsi_overextended ? "default" : "outline"}>
                {technicalStopLoss.rsi_overextended ? "Ativo" : "Inativo"}
              </Badge>
            </div>
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded">
              <div>
                <div className="font-medium">Perdas Consecutivas</div>
                <div className="text-sm text-gray-500">Cooldown automático</div>
              </div>
              <Badge variant={technicalStopLoss.consecutive_losses > 0 ? "destructive" : "outline"}>
                {technicalStopLoss.consecutive_losses || 0}
              </Badge>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Contratos Ativos */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Contratos Sendo Monitorados
            <Badge variant="outline">{activeContracts.length}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {activeContracts.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              <StopCircle className="h-12 w-12 mx-auto mb-2 opacity-50" />
              <p>Nenhum contrato ativo sendo monitorado</p>
              <p className="text-sm">Os contratos aparecerão aqui quando trades forem executados</p>
            </div>
          ) : (
            <div className="space-y-3">
              {activeContracts.map((contract, index) => (
                <div key={contract.contract_id} className="p-4 border rounded-lg bg-gray-50">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <PlayCircle className="h-4 w-4 text-green-600" />
                      <span className="font-medium">Contrato {contract.contract_id}</span>
                      <Badge variant="outline">{contract.symbol}</Badge>
                      <Badge variant={contract.direction === 'CALL' ? 'default' : 'destructive'}>
                        {contract.direction}
                      </Badge>
                    </div>
                    <div className="text-sm text-gray-500">
                      Stake: ${contract.stake}
                    </div>
                  </div>
                  <div className="text-xs text-gray-500">
                    Criado: {new Date(contract.created_at * 1000).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Teste do Sistema */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CheckCircle className="h-5 w-5" />
            Testar Sistema de Stop Loss
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-blue-50 p-4 rounded-lg">
            <div className="flex items-start gap-2">
              <CheckCircle className="h-4 w-4 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm">
                <p className="font-semibold text-blue-800 mb-1">Teste de Simulação:</p>
                <p className="text-blue-700">
                  Este teste simula um contrato com 60% de perda para verificar se o sistema 
                  de stop loss está funcionando corretamente com o limite atual de {(tempConfig.stop_loss_percentage * 100).toFixed(0)}%.
                </p>
              </div>
            </div>
          </div>

          <Button 
            onClick={testStopLoss}
            disabled={testing}
            className="w-full"
          >
            {testing ? (
              <>
                <Activity className="mr-2 h-4 w-4 animate-spin" />
                Testando Sistema...
              </>
            ) : (
              <>
                <CheckCircle className="mr-2 h-4 w-4" />
                Testar Stop Loss
              </>
            )}
          </Button>

          {/* Resultado do Teste */}
          {testResult && (
            <div className={`p-4 rounded-lg ${
              testResult.error ? 'bg-red-50 border border-red-200' : 
              testResult.would_trigger_stop_loss ? 'bg-green-50 border border-green-200' : 
              'bg-yellow-50 border border-yellow-200'
            }`}>
              <div className="flex items-start gap-2">
                {testResult.error ? (
                  <XCircle className="h-4 w-4 text-red-600 flex-shrink-0 mt-0.5" />
                ) : testResult.would_trigger_stop_loss ? (
                  <CheckCircle className="h-4 w-4 text-green-600 flex-shrink-0 mt-0.5" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-yellow-600 flex-shrink-0 mt-0.5" />
                )}
                <div className="text-sm">
                  <p className={`font-semibold mb-1 ${
                    testResult.error ? 'text-red-800' : 
                    testResult.would_trigger_stop_loss ? 'text-green-800' : 
                    'text-yellow-800'
                  }`}>
                    {testResult.error ? 'Erro no Teste' : 
                     testResult.would_trigger_stop_loss ? 'Sistema OK' : 
                     'Sistema Precisa de Ajuste'}
                  </p>
                  <p className={`${
                    testResult.error ? 'text-red-700' : 
                    testResult.would_trigger_stop_loss ? 'text-green-700' : 
                    'text-yellow-700'
                  }`}>
                    {testResult.error || testResult.message}
                  </p>
                  
                  {!testResult.error && (
                    <div className="mt-2 grid grid-cols-2 gap-4 text-xs">
                      <div>
                        <span className="text-gray-600">Perda Simulada:</span>
                        <span className="ml-1 font-mono">${testResult.simulated_loss}</span>
                      </div>
                      <div>
                        <span className="text-gray-600">Limite Stop:</span>
                        <span className="ml-1 font-mono">${testResult.loss_limit}</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default StopLossPanel;