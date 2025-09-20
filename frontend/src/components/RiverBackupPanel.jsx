import React, { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { 
  AlertCircle, 
  Download, 
  Upload, 
  History, 
  Activity, 
  CheckCircle, 
  XCircle,
  HardDrive,
  RefreshCw,
  Archive
} from 'lucide-react';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8001/api';

const RiverBackupPanel = () => {
  // Estados principais
  const [riverStatus, setRiverStatus] = useState(null);
  const [backups, setBackups] = useState([]);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [creatingBackup, setCreatingBackup] = useState(false);
  
  // Fetch do status do River
  const fetchRiverStatus = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/ml/river/status`);
      setRiverStatus(data);
    } catch (error) {
      console.error('Erro ao obter status River:', error);
    }
  }, []);

  // Fetch dos backups disponíveis
  const fetchBackups = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/ml/river/backups`);
      setBackups(data.backups || []);
    } catch (error) {
      console.error('Erro ao obter backups River:', error);
      setBackups([]);
    }
    setLoading(false);
  }, []);

  // Criar backup forçado
  const createBackup = async () => {
    setCreatingBackup(true);
    try {
      const { data } = await axios.post(`${API}/ml/river/force_backup`);
      console.log('Backup criado:', data.message);
      
      // Refresh da lista de backups
      await fetchBackups();
      await fetchRiverStatus();
    } catch (error) {
      console.error('Erro ao criar backup:', error);
    }
    setCreatingBackup(false);
  };

  // Restaurar backup
  const restoreBackup = async (backupFilename) => {
    if (!window.confirm(`Tem certeza que deseja restaurar o backup "${backupFilename}"? O modelo atual será substituído.`)) {
      return;
    }

    setRestoring(true);
    try {
      const { data } = await axios.post(`${API}/ml/river/restore`, {
        backup_filename: backupFilename
      });
      
      console.log('Backup restaurado:', data.message);
      alert(`✅ Backup restaurado com sucesso!\n\nAmostras: ${data.restored_samples}\nAcurácia: ${(data.restored_accuracy * 100).toFixed(2)}%`);
      
      // Refresh dos dados
      await fetchRiverStatus();
      await fetchBackups();
    } catch (error) {
      console.error('Erro ao restaurar backup:', error);
      alert(`❌ Erro ao restaurar backup: ${error.response?.data?.detail || error.message}`);
    }
    setRestoring(false);
  };

  // Auto refresh
  useEffect(() => {
    fetchRiverStatus();
    fetchBackups();
    
    // Refresh a cada 30 segundos
    const interval = setInterval(() => {
      fetchRiverStatus();
    }, 30000);
    
    return () => clearInterval(interval);
  }, [fetchRiverStatus, fetchBackups]);

  if (!riverStatus && !loading) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="flex items-center justify-center">
            <Activity className="mr-2 h-4 w-4 animate-spin" />
            Carregando status River...
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* Status Atual do River */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HardDrive className="h-5 w-5" />
            Status River Machine Learning
            <Badge variant={riverStatus?.initialized ? "default" : "outline"}>
              {riverStatus?.initialized ? "Ativo" : "Inativo"}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="text-center">
              <div className="text-3xl font-bold text-blue-600">
                {riverStatus?.samples || 0}
              </div>
              <div className="text-sm text-gray-500">Amostras Treinadas</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-green-600">
                {riverStatus?.acc ? (riverStatus.acc * 100).toFixed(1) : 0}%
              </div>
              <div className="text-sm text-gray-500">Acurácia</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-purple-600">
                {riverStatus?.logloss ? riverStatus.logloss.toFixed(3) : 0}
              </div>
              <div className="text-sm text-gray-500">Log Loss</div>
            </div>
          </div>
          
          {/* Informações adicionais */}
          <div className="mt-4 p-4 bg-blue-50 rounded-lg">
            <div className="flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm">
                <p className="font-semibold text-blue-800 mb-1">Sistema River Upd:</p>
                <p className="text-blue-700">
                  O River é um sistema de aprendizado online que se adapta continuamente com novos dados de mercado. 
                  Cada trade realizado adiciona uma nova amostra ao modelo, melhorando sua precisão ao longo do tempo.
                </p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Gerenciamento de Backups */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Archive className="h-5 w-5" />
            Gerenciamento de Backups
            <Button
              variant="outline"
              size="sm"
              onClick={fetchBackups}
              disabled={loading}
              className="ml-auto"
            >
              {loading ? (
                <Activity className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Criar novo backup */}
          <div className="flex gap-3">
            <Button 
              onClick={createBackup}
              disabled={creatingBackup}
              className="flex-1"
            >
              {creatingBackup ? (
                <>
                  <Activity className="mr-2 h-4 w-4 animate-spin" />
                  Criando Backup...
                </>
              ) : (
                <>
                  <Download className="mr-2 h-4 w-4" />
                  Criar Backup Agora
                </>
              )}
            </Button>
          </div>

          {/* Lista de backups */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="font-semibold">Backups Disponíveis ({backups.length})</h4>
              {backups.length > 0 && (
                <Badge variant="outline">{backups.reduce((total, backup) => total + backup.size_mb, 0).toFixed(1)} MB</Badge>
              )}
            </div>

            {backups.length === 0 ? (
              <div className="text-center py-8 text-gray-500">
                <Archive className="h-12 w-12 mx-auto mb-2 opacity-50" />
                <p>Nenhum backup encontrado</p>
                <p className="text-sm">Crie seu primeiro backup para preservar o progresso</p>
              </div>
            ) : (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {backups.map((backup, index) => (
                  <div 
                    key={backup.file} 
                    className={`p-4 border rounded-lg ${
                      backup.samples >= 284 ? 'border-green-200 bg-green-50' : 
                      backup.samples >= 200 ? 'border-yellow-200 bg-yellow-50' : 
                      'border-gray-200 bg-gray-50'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <History className="h-4 w-4" />
                        <span className="font-medium">{backup.date}</span>
                        {backup.samples >= 284 && (
                          <Badge variant="default" className="bg-green-600">
                            Evoluído
                          </Badge>
                        )}
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => restoreBackup(backup.file)}
                        disabled={restoring}
                      >
                        {restoring ? (
                          <Activity className="h-4 w-4 animate-spin" />
                        ) : (
                          <>
                            <Upload className="mr-1 h-3 w-3" />
                            Restaurar
                          </>
                        )}
                      </Button>
                    </div>
                    
                    <div className="grid grid-cols-3 gap-4 text-sm">
                      <div>
                        <span className="text-gray-500">Amostras:</span>
                        <span className={`ml-1 font-mono font-semibold ${
                          backup.samples >= 284 ? 'text-green-600' :
                          backup.samples >= 200 ? 'text-yellow-600' :
                          'text-gray-600'
                        }`}>
                          {backup.samples}
                        </span>
                      </div>
                      <div>
                        <span className="text-gray-500">Tamanho:</span>
                        <span className="ml-1 font-mono">{backup.size_mb} MB</span>
                      </div>
                      <div>
                        <span className="text-gray-500">Arquivo:</span>
                        <span className="ml-1 font-mono text-xs">{backup.file}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Informações sobre recuperação */}
          <div className="bg-yellow-50 p-4 rounded-lg">
            <div className="flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-yellow-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm">
                <p className="font-semibold text-yellow-800 mb-1">💡 Dica para Recuperar River 284:</p>
                <p className="text-yellow-700">
                  Procure por backups com <strong>284+ amostras</strong> (marcados como "Evoluído"). 
                  Se não encontrar nenhum, o progresso pode ter sido perdido. 
                  O sistema agora cria backups automáticos a cada atualização para evitar perdas futuras.
                </p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Sistema de Backup Automático */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CheckCircle className="h-5 w-5 text-green-600" />
            Sistema de Backup Automático
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex items-center gap-3">
                <CheckCircle className="h-5 w-5 text-green-600" />
                <div>
                  <div className="font-medium">Backup Automático</div>
                  <div className="text-sm text-gray-500">A cada save do modelo</div>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <CheckCircle className="h-5 w-5 text-green-600" />
                <div>
                  <div className="font-medium">Histórico Preservado</div>
                  <div className="text-sm text-gray-500">Últimos 10 backups mantidos</div>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <CheckCircle className="h-5 w-5 text-green-600" />
                <div>
                  <div className="font-medium">Treino Contínuo</div>
                  <div className="text-sm text-gray-500">Amostras salvas automaticamente</div>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <CheckCircle className="h-5 w-5 text-green-600" />
                <div>
                  <div className="font-medium">Recuperação Rápida</div>
                  <div className="text-sm text-gray-500">Restauração com 1 clique</div>
                </div>
              </div>
            </div>

            <div className="bg-green-50 p-4 rounded-lg">
              <div className="flex items-start gap-2">
                <CheckCircle className="h-4 w-4 text-green-600 flex-shrink-0 mt-0.5" />
                <div className="text-sm">
                  <p className="font-semibold text-green-800 mb-1">✅ Sistema Configurado:</p>
                  <p className="text-green-700">
                    A partir de agora, todo progresso do River será preservado automaticamente. 
                    Cada vez que o modelo é atualizado com novos trades, um backup é criado. 
                    Nunca mais você perderá o progresso de treinamento!
                  </p>
                </div>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default RiverBackupPanel;