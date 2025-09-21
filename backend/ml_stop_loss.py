"""
🤖 STOP LOSS INTELIGENTE COM MACHINE LEARNING
Sistema que usa ML para prever se uma trade perdedora tem chances de recuperação
"""

import logging
import json
import pickle
import os
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import numpy as np
from river import linear_model, metrics, preprocessing, compose
import talib
import time

logger = logging.getLogger(__name__)

class MLStopLossPredictor:
    """
    Preditor ML para decisões inteligentes de stop loss
    Usa online learning (River) para adaptação contínua
    """
    
    def __init__(self, model_path: str = "/app/backend/ml_models/stop_loss_predictor.pkl"):
        self.model_path = model_path
        self.model = None
        self.scaler = None
        self.accuracy = metrics.Accuracy()
        self.samples_processed = 0
        
        # Histórico de contratos para aprendizado
        self.contract_history: Dict[int, Dict[str, Any]] = {}
        
        # Configurações do modelo
        self.recovery_threshold = 0.65  # >65% prob de recuperação = aguardar
        self.loss_threshold = 0.70      # >70% prob de perda = vender
        self.max_loss_limit = 0.80      # Limite máximo absoluto (80%)
        
        self._initialize_model()
        
    def _initialize_model(self):
        """Inicializa modelo River com pipeline de preprocessing"""
        try:
            # Tentar carregar modelo existente
            if os.path.exists(self.model_path):
                with open(self.model_path, 'rb') as f:
                    saved_data = pickle.load(f)
                    self.model = saved_data['model']
                    self.scaler = saved_data['scaler'] 
                    self.accuracy = saved_data.get('accuracy', metrics.Accuracy())
                    self.samples_processed = saved_data.get('samples', 0)
                    logger.info(f"🤖 Modelo ML Stop Loss carregado: {self.samples_processed} amostras")
            else:
                logger.info("🤖 Criando novo modelo ML Stop Loss")
                
        except Exception as e:
            logger.warning(f"Erro carregando modelo: {e}. Criando novo...")
            
        # Criar/recriar modelo se necessário
        if self.model is None:
            self.model = compose.Pipeline(
                preprocessing.StandardScaler(),
                linear_model.LogisticRegression(
                    optimizer='sgd',
                    l2=0.01,
                    intercept_lr=0.1
                )
            )
            self.scaler = preprocessing.StandardScaler()
            logger.info("🤖 Novo modelo ML Stop Loss criado")
    
    def extract_features(self, 
                        contract_id: int,
                        current_profit: float, 
                        stake: float,
                        start_time: int,
                        candles: List[Dict[str, Any]] = None,
                        symbol: str = "R_100") -> Dict[str, float]:
        """
        Extrai features para predição ML
        """
        try:
            current_time = int(time.time())
            elapsed_minutes = (current_time - start_time) / 60.0
            profit_percentage = (current_profit / stake) * 100 if stake > 0 else 0
            
            features = {
                # === FEATURES BÁSICAS ===
                'profit_percentage': profit_percentage,
                'elapsed_minutes': elapsed_minutes,
                'stake_size': stake,
                'profit_absolute': current_profit,
                
                # === FEATURES TEMPORAIS ===
                'hour_of_day': datetime.now().hour,
                'day_of_week': datetime.now().weekday(),
                'is_weekend': 1.0 if datetime.now().weekday() >= 5 else 0.0,
                
                # === FEATURES DE VOLATILIDADE ===
                'volatility_level': self._get_volatility_level(symbol),
                'profit_velocity': self._calculate_profit_velocity(contract_id, current_profit),
            }
            
            # === FEATURES TÉCNICAS (se candles disponíveis) ===
            if candles and len(candles) >= 20:
                tech_features = self._extract_technical_features(candles)
                features.update(tech_features)
            else:
                # Features técnicas default
                features.update({
                    'rsi': 50.0, 'macd': 0.0, 'bb_position': 0.5,
                    'momentum_5': 0.0, 'momentum_10': 0.0,
                    'volatility_20': 0.01, 'sma_slope': 0.0
                })
            
            return features
            
        except Exception as e:
            logger.error(f"Erro extraindo features: {e}")
            # Retornar features mínimas em caso de erro
            return {
                'profit_percentage': (current_profit / stake) * 100 if stake > 0 else 0,
                'elapsed_minutes': (int(time.time()) - start_time) / 60.0,
                'rsi': 50.0, 'macd': 0.0, 'bb_position': 0.5
            }
    
    def _get_volatility_level(self, symbol: str) -> float:
        """Estima nível de volatilidade baseado no símbolo"""
        volatility_map = {
            'R_10': 0.1, 'R_25': 0.25, 'R_50': 0.5,
            'R_75': 0.75, 'R_100': 1.0,
            '1HZ10V': 0.1, '1HZ25V': 0.25, '1HZ50V': 0.5,
            '1HZ75V': 0.75, '1HZ100V': 1.0
        }
        return volatility_map.get(symbol, 0.5)
    
    def _calculate_profit_velocity(self, contract_id: int, current_profit: float) -> float:
        """Calcula velocidade de mudança do profit"""
        try:
            if contract_id not in self.contract_history:
                self.contract_history[contract_id] = {'profits': [], 'timestamps': []}
            
            history = self.contract_history[contract_id]
            current_time = time.time()
            
            # Adicionar ponto atual
            history['profits'].append(current_profit)
            history['timestamps'].append(current_time)
            
            # Manter apenas últimos 10 pontos
            if len(history['profits']) > 10:
                history['profits'] = history['profits'][-10:]
                history['timestamps'] = history['timestamps'][-10:]
            
            # Calcular velocidade (mudança por minuto)
            if len(history['profits']) >= 2:
                time_diff = (history['timestamps'][-1] - history['timestamps'][0]) / 60.0  # minutos
                profit_diff = history['profits'][-1] - history['profits'][0]
                return profit_diff / time_diff if time_diff > 0 else 0.0
            
            return 0.0
            
        except Exception as e:
            logger.warning(f"Erro calculando profit velocity: {e}")
            return 0.0
    
    def _extract_technical_features(self, candles: List[Dict[str, Any]]) -> Dict[str, float]:
        """Extrai indicadores técnicos das candles"""
        try:
            # Converter para arrays numpy
            closes = np.array([float(c.get('close', 0)) for c in candles[-20:]])
            highs = np.array([float(c.get('high', 0)) for c in candles[-20:]])
            lows = np.array([float(c.get('low', 0)) for c in candles[-20:]])
            volumes = np.array([float(c.get('volume', 1)) for c in candles[-20:]])
            
            if len(closes) < 14:
                return {'rsi': 50.0, 'macd': 0.0, 'bb_position': 0.5}
            
            # RSI
            rsi = talib.RSI(closes, timeperiod=14)[-1] if len(closes) >= 14 else 50.0
            
            # MACD
            macd_line, macd_signal, macd_hist = talib.MACD(closes)
            macd = macd_hist[-1] if not np.isnan(macd_hist[-1]) else 0.0
            
            # Bollinger Bands position
            bb_upper, bb_middle, bb_lower = talib.BBANDS(closes)
            bb_position = ((closes[-1] - bb_lower[-1]) / (bb_upper[-1] - bb_lower[-1])) if (bb_upper[-1] - bb_lower[-1]) > 0 else 0.5
            
            # Momentum
            momentum_5 = (closes[-1] - closes[-6]) / closes[-6] if len(closes) > 5 else 0.0
            momentum_10 = (closes[-1] - closes[-11]) / closes[-11] if len(closes) > 10 else 0.0
            
            # Volatilidade
            volatility = np.std(closes[-10:]) / np.mean(closes[-10:]) if len(closes) >= 10 else 0.01
            
            # SMA slope
            sma = talib.SMA(closes, timeperiod=5)
            sma_slope = (sma[-1] - sma[-3]) / sma[-3] if len(sma) > 2 and not np.isnan(sma[-1]) else 0.0
            
            return {
                'rsi': float(rsi) if not np.isnan(rsi) else 50.0,
                'macd': float(macd) if not np.isnan(macd) else 0.0,
                'bb_position': float(bb_position) if not np.isnan(bb_position) else 0.5,
                'momentum_5': float(momentum_5) * 100,  # Em porcentagem
                'momentum_10': float(momentum_10) * 100,
                'volatility_20': float(volatility),
                'sma_slope': float(sma_slope) * 100
            }
            
        except Exception as e:
            logger.warning(f"Erro calculando indicadores técnicos: {e}")
            return {'rsi': 50.0, 'macd': 0.0, 'bb_position': 0.5, 'momentum_5': 0.0, 'momentum_10': 0.0, 'volatility_20': 0.01, 'sma_slope': 0.0}
    
    def predict_recovery_probability(self, 
                                   contract_id: int,
                                   current_profit: float,
                                   stake: float, 
                                   start_time: int,
                                   candles: List[Dict[str, Any]] = None,
                                   symbol: str = "R_100") -> Tuple[float, Dict[str, Any]]:
        """
        Prediz probabilidade de recuperação da trade
        
        Returns:
            Tuple[float, Dict]: (probabilidade_recuperacao, detalhes)
        """
        try:
            # Extrair features
            features = self.extract_features(contract_id, current_profit, stake, start_time, candles, symbol)
            
            # Fazer predição
            if self.samples_processed > 10:  # Só usar ML se tiver dados suficientes
                prob_recovery = self.model.predict_proba_one(features).get(1, 0.5)
            else:
                # Heurística simples para cold start
                profit_pct = features['profit_percentage']
                elapsed = features['elapsed_minutes']
                
                # Mais tempo = menos chance de recuperação
                # Menos perda = mais chance de recuperação
                prob_recovery = max(0.1, min(0.9, 0.5 + (profit_pct / 100) - (elapsed / 60)))
            
            details = {
                'contract_id': contract_id,
                'features_used': len(features),
                'features': features,
                'model_samples': self.samples_processed,
                'model_accuracy': float(self.accuracy.get()) if self.samples_processed > 0 else None,
                'prediction_source': 'ML' if self.samples_processed > 10 else 'heuristic'
            }
            
            return float(prob_recovery), details
            
        except Exception as e:
            logger.error(f"Erro na predição ML: {e}")
            # Fallback para heurística simples
            profit_pct = (current_profit / stake) * 100 if stake > 0 else -50
            prob_recovery = max(0.1, min(0.9, 0.5 + (profit_pct / 100)))
            
            return float(prob_recovery), {
                'error': str(e),
                'prediction_source': 'fallback',
                'profit_percentage': profit_pct
            }
    
    def should_stop_loss(self,
                        contract_id: int, 
                        current_profit: float,
                        stake: float,
                        start_time: int,
                        candles: List[Dict[str, Any]] = None,
                        symbol: str = "R_100") -> Tuple[bool, str, Dict[str, Any]]:
        """
        Decisão inteligente de stop loss usando ML
        
        Returns:
            Tuple[bool, str, Dict]: (deve_vender, razao, detalhes)
        """
        try:
            # Limite absoluto de segurança
            loss_percentage = abs(current_profit / stake) if stake > 0 else 0
            if loss_percentage >= self.max_loss_limit:
                return True, f"🚨 LIMITE MÁXIMO: {loss_percentage:.1%} >= {self.max_loss_limit:.1%}", {
                    'trigger': 'max_loss_limit',
                    'loss_percentage': loss_percentage
                }
            
            # Predição ML
            prob_recovery, details = self.predict_recovery_probability(
                contract_id, current_profit, stake, start_time, candles, symbol
            )
            
            # Lógica de decisão
            if prob_recovery >= self.recovery_threshold:
                decision = False
                reason = f"🤖 ML AGUARDAR: {prob_recovery:.1%} chance de recuperação (>{self.recovery_threshold:.1%})"
            elif prob_recovery <= (1 - self.loss_threshold):
                decision = True  
                reason = f"🤖 ML VENDER: {1-prob_recovery:.1%} chance de perda contínua (>{self.loss_threshold:.1%})"
            else:
                # Zona incerta - usar regra tradicional
                decision = loss_percentage >= 0.5  # 50% como antes
                reason = f"🤖 ML INCERTO: {prob_recovery:.1%} recuperação - usando regra tradicional (50%)"
            
            # Adicionar detalhes
            details.update({
                'decision': decision,
                'reason': reason,
                'prob_recovery': prob_recovery,
                'loss_percentage': loss_percentage,
                'thresholds': {
                    'recovery': self.recovery_threshold,
                    'loss': self.loss_threshold,
                    'max_loss': self.max_loss_limit
                }
            })
            
            return decision, reason, details
            
        except Exception as e:
            logger.error(f"Erro na decisão de stop loss: {e}")
            # Fallback para lógica tradicional
            loss_pct = abs(current_profit / stake) if stake > 0 else 0
            should_sell = loss_pct >= 0.5
            reason = f"❌ ERRO ML - usando regra tradicional: {loss_pct:.1%}"
            
            return should_sell, reason, {'error': str(e), 'fallback': True}
    
    def learn_from_outcome(self,
                          contract_id: int,
                          features_at_decision: Dict[str, float],
                          decision_made: bool,
                          final_profit: float,
                          stake: float):
        """
        Aprende com o resultado final da trade para melhorar futuras predições
        """
        try:
            # Determinar se houve recuperação
            final_profit_pct = (final_profit / stake) * 100 if stake > 0 else -100
            profit_at_decision = features_at_decision.get('profit_percentage', -50)
            
            # Se estava perdendo na decisão, houve recuperação?
            if profit_at_decision < -5:  # Estava perdendo mais que 5%
                recovered = final_profit_pct > profit_at_decision + 10  # Recuperou pelo menos 10 pontos
            else:
                recovered = final_profit_pct > 0  # Terminou positivo
            
            # Treinar modelo
            target = 1 if recovered else 0
            self.model.learn_one(features_at_decision, target)
            
            # Atualizar métricas
            predicted_prob = self.model.predict_proba_one(features_at_decision).get(1, 0.5)
            predicted_class = 1 if predicted_prob > 0.5 else 0
            self.accuracy.update(target, predicted_class)
            self.samples_processed += 1
            
            # Log do aprendizado
            logger.info(f"🧠 ML APRENDEU: Contract {contract_id} - Profit {profit_at_decision:.1f}% → {final_profit_pct:.1f}% "
                       f"| Recuperou: {recovered} | Accuracy: {self.accuracy.get():.3f} | Samples: {self.samples_processed}")
            
            # Salvar modelo a cada 10 amostras
            if self.samples_processed % 10 == 0:
                self._save_model()
                
            # Limpar histórico do contrato
            self.contract_history.pop(contract_id, None)
            
        except Exception as e:
            logger.error(f"Erro no aprendizado: {e}")
    
    def _save_model(self):
        """Salva modelo no disco"""
        try:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            
            save_data = {
                'model': self.model,
                'scaler': self.scaler,
                'accuracy': self.accuracy,
                'samples': self.samples_processed,
                'created_at': datetime.now().isoformat()
            }
            
            with open(self.model_path, 'wb') as f:
                pickle.dump(save_data, f)
                
            logger.info(f"🤖 Modelo ML Stop Loss salvo: {self.samples_processed} amostras, accuracy: {self.accuracy.get():.3f}")
            
        except Exception as e:
            logger.error(f"Erro salvando modelo: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Retorna status do modelo ML"""
        return {
            'initialized': self.model is not None,
            'samples_processed': self.samples_processed,
            'accuracy': float(self.accuracy.get()) if self.samples_processed > 0 else None,
            'active_contracts': len(self.contract_history),
            'thresholds': {
                'recovery_threshold': self.recovery_threshold,
                'loss_threshold': self.loss_threshold,  
                'max_loss_limit': self.max_loss_limit
            },
            'model_path': self.model_path
        }
    
    def update_config(self, config: Dict[str, Any]) -> bool:
        """Atualiza configurações do modelo"""
        try:
            if 'recovery_threshold' in config:
                self.recovery_threshold = float(config['recovery_threshold'])
            if 'loss_threshold' in config:
                self.loss_threshold = float(config['loss_threshold'])
            if 'max_loss_limit' in config:
                self.max_loss_limit = float(config['max_loss_limit'])
                
            logger.info(f"🤖 Configurações ML atualizadas: recovery={self.recovery_threshold:.1%}, "
                       f"loss={self.loss_threshold:.1%}, max={self.max_loss_limit:.1%}")
            return True
            
        except Exception as e:
            logger.error(f"Erro atualizando config ML: {e}")
            return False