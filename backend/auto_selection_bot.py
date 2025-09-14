"""
Bot de seleção automática de volatility indices + timeframes (Python).
- Coleta ticks em tempo real (WebSocket público da Deriv).
- Agrega em janelas (ticks / segundos / minutos).
- Simula trades simples para avaliar performance por símbolo+timeframe.
- Seleciona o melhor e (opcional) executa a ordem real via Deriv.

Integrado ao sistema existente de trading da Deriv.
"""

import asyncio
import json
import time
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
import pandas as pd
import websockets
import logging
from datetime import datetime, timedelta
from pydantic import BaseModel
import os

# configuração de logging
logger = logging.getLogger(__name__)

# --- Configurações do bot ---
SYMBOLS = ["R_100", "R_75", "R_50", "R_25", "R_10"]  # símbolos a avaliar
# timeframes: representados como ('type','value') where type in {'ticks','s','m'}
# FILTRADOS: Removidos 1-2 ticks que causam winrate baixo
TIMEFRAMES = [
    # REMOVIDOS: ("ticks", 1) e ("ticks", 2) - muito problemáticos para winrate
    ("ticks", 5),        # janela de 5 ticks (mínimo para ticks)
    ("ticks", 10),       # janela de 10 ticks
    ("ticks", 25),       # janela de 25 ticks
    ("ticks", 50),       # janela de 50 ticks
    ("s", 30),           # 30 segundos (removido 15s muito rápido)
    ("s", 60),           # 1 minuto em segundos
    ("s", 120),          # 2 minutos em segundos
    ("s", 300),          # 5 minutos em segundos
    ("m", 1),            # 1 minuto
    ("m", 2),            # 2 minutos - FOCO CONSERVADOR
    ("m", 3),            # 3 minutos - FOCO CONSERVADOR
    ("m", 5),            # 5 minutos - FOCO CONSERVADOR
    ("m", 10),           # 10 minutos - FOCO CONSERVADOR
    ("m", 15),           # 15 minutos - FOCO CONSERVADOR
    ("m", 30),           # 30 minutos - FOCO CONSERVADOR
]

SIM_WINDOW_SECONDS = 60  # janela de histórico (em segundos) usada para simular performance
SIM_TRADE_STAKE = 1.0    # stake hipotético por simulação (apenas para ranking)

# Websocket endpoint público (Deriv/Binaryws)
DERIV_WS = f"wss://ws.derivws.com/websockets/v3?app_id={os.environ.get('DERIV_APP_ID', '1089')}"

# parâmetros da estratégia de simulação (CONSERVADOR: critérios mais rigorosos)
@dataclass
class StrategyParams:
    ma_short: int = 3
    ma_long: int = 8
    expiry_seconds: int = 10  # duração do contrato simulado (em segundos) para TICKS/segundos
    take_profit: float = 0.8  # lucro hipotético relativo p/ considerar como win (simulação)
    stop_loss: float = -1.0   # perda hipotética relativa
    direction_threshold: float = 0.0  # se MA_short - MA_long > threshold => CALL
    min_winrate: float = 0.85  # winrate mínimo ULTRA RIGOROSO (85% vs 75%)
    min_trades_sample: int = 12  # mínimo de trades na amostra ULTRA RIGOROSO (12 vs 8)
    min_pnl_positive: float = 1.0  # PnL mínimo positivo MAIS ALTO (1.0 vs 0.5)
    conservative_mode: bool = True  # modo conservador ativo
    
    # Novos parâmetros para peso por tipo de timeframe
    timeframe_weight_multipliers: dict = None
    
    def __post_init__(self):
        if self.timeframe_weight_multipliers is None:
            # Dar MUITO mais peso aos timeframes de minutos (ULTRA conservadores)
            self.timeframe_weight_multipliers = {
                "ticks": 0.1,    # Peso MUITO menor para ticks (muito arriscados)
                "s": 0.4,        # Peso baixo para segundos (arriscados)
                "m_1": 1.2,      # Peso bom para 1 minuto
                "m_2_10": 2.0,   # PESO MÁXIMO para 2-10 minutos (ULTRA conservadores)
                "m_15_30": 1.8,  # Peso muito alto para 15-30 minutos
            }

STRAT = StrategyParams()

# --- Modelos Pydantic para API ---
class AutoBotConfig(BaseModel):
    symbols: List[str] = SYMBOLS
    timeframes: List[Tuple[str, int]] = TIMEFRAMES
    sim_window_seconds: int = SIM_WINDOW_SECONDS
    sim_trade_stake: float = SIM_TRADE_STAKE
    strategy_params: dict = None
    auto_execute: bool = False  # se True, executa trades reais automaticamente
    evaluation_interval: int = 5  # intervalo de avaliação em segundos
    min_winrate: float = 0.85  # winrate mínimo ULTRA RIGOROSO (85% vs 75%)
    min_trades_sample: int = 12  # mínimo de trades na amostra ULTRA RIGOROSO (12 vs 8)
    min_pnl_positive: float = 1.0  # PnL mínimo positivo MAIS ALTO (1.0 vs 0.5)
    use_combined_score: bool = True  # usar score combinado (winrate + pnl + volume)
    conservative_mode: bool = True  # modo conservador (critérios mais rigorosos)
    prefer_longer_timeframes: bool = True  # preferir timeframes mais longos (2-10min)
    
    # Pesos para score combinado conservador
    score_weights: dict = {
        "winrate": 0.5,      # Maior peso para winrate (50% vs 40%)
        "pnl": 0.3,          # Menor peso para PnL (30% vs 40%)
        "volume": 0.1,       # Menor peso para volume (10% vs 20%)
        "timeframe": 0.1     # Novo peso para tipo de timeframe (10%)
    }

class AutoBotStatus(BaseModel):
    running: bool = False
    collecting_ticks: bool = False
    last_evaluation: Optional[datetime] = None
    best_combo: Optional[Dict[str, Any]] = None
    total_evaluations: int = 0
    symbols_with_data: List[str] = []
    tick_counts: Dict[str, int] = {}
    auto_execute: bool = False
    trades_executed: int = 0
    last_trade: Optional[Dict[str, Any]] = None
    min_winrate: float = 0.85  # winrate mínimo ULTRA rigoroso  
    min_trades_sample: int = 12  # trades mínimos ULTRA rigoroso
    min_pnl_positive: float = 1.0  # PnL mínimo positivo MAIS ALTO
    use_combined_score: bool = True
    conservative_mode: bool = True
    prefer_longer_timeframes: bool = True
    evaluation_stats: Optional[Dict[str, Any]] = None
    
    # Estatísticas de performance por tipo de timeframe
    timeframe_performance: Optional[Dict[str, Dict[str, Any]]] = None

class AutoBotResults(BaseModel):
    timestamp: datetime
    results: List[Dict[str, Any]]
    best_combo: Optional[Dict[str, Any]] = None

# --- Estruturas de dados em memória ---
# armazenar ticks por símbolo como deque de (timestamp, price)
ticks_store: Dict[str, deque] = {sym: deque(maxlen=20000) for sym in SYMBOLS}

# --- Classe principal do bot ---
class AutoSelectionBot:
    def __init__(self):
        self.config = AutoBotConfig()
        self.status = AutoBotStatus()
        self.running = False
        self.collecting_ticks = False
        self.ws_task = None
        self.evaluation_task = None
        self.deriv_api = None  # referência para executar trades reais
        
    def set_deriv_api(self, deriv_api):
        """Define referência para API da Deriv para execução de trades reais"""
        self.deriv_api = deriv_api
        
    def update_config(self, config: AutoBotConfig):
        """Atualiza configuração do bot"""
        self.config = config
        self.status.auto_execute = config.auto_execute
        self.status.min_winrate = config.min_winrate
        self.status.min_trades_sample = config.min_trades_sample
        self.status.min_pnl_positive = config.min_pnl_positive
        self.status.use_combined_score = config.use_combined_score
        self.status.conservative_mode = config.conservative_mode
        self.status.prefer_longer_timeframes = config.prefer_longer_timeframes
        
        # Atualiza parâmetros da estratégia
        global STRAT
        STRAT.min_winrate = config.min_winrate
        STRAT.min_trades_sample = config.min_trades_sample
        STRAT.min_pnl_positive = config.min_pnl_positive
        STRAT.conservative_mode = config.conservative_mode
        
        # Reinitializa ticks_store com novos símbolos se necessário
        global ticks_store
        for symbol in config.symbols:
            if symbol not in ticks_store:
                ticks_store[symbol] = deque(maxlen=20000)
                
    async def start(self):
        """Inicia o bot de seleção automática"""
        if self.running:
            raise Exception("Bot já está em execução")
            
        self.running = True
        self.status.running = True
        
        logger.info("Iniciando bot de seleção automática")
        
        # Inicia coleta de ticks
        self.ws_task = asyncio.create_task(self._collect_ticks())
        
        # Inicia loop de avaliação
        self.evaluation_task = asyncio.create_task(self._evaluation_loop())
        
        logger.info("Bot de seleção automática iniciado com sucesso")
        
    async def stop(self):
        """Para o bot de seleção automática"""
        if not self.running:
            return
            
        self.running = False
        self.status.running = False
        self.status.collecting_ticks = False
        
        logger.info("Parando bot de seleção automática")
        
        # Cancela tasks
        if self.ws_task:
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
                
        if self.evaluation_task:
            self.evaluation_task.cancel()
            try:
                await self.evaluation_task
            except asyncio.CancelledError:
                pass
                
        logger.info("Bot de seleção automática parado")
        
    def get_status(self) -> AutoBotStatus:
        """Retorna status atual do bot"""
        # Atualiza contadores de ticks
        self.status.tick_counts = {sym: len(ticks_store.get(sym, [])) for sym in self.config.symbols}
        self.status.symbols_with_data = [sym for sym, count in self.status.tick_counts.items() if count > 0]
        return self.status
        
    async def _collect_ticks(self):
        """Conecta ao WebSocket da Deriv e mantém recebendo ticks para os símbolos listados."""
        try:
            self.status.collecting_ticks = True
            logger.info(f"Conectando ao WebSocket da Deriv: {DERIV_WS}")
            
            async with websockets.connect(DERIV_WS) as ws:
                # subscreve cada símbolo
                for symbol in self.config.symbols:
                    await self._subscribe_ticks(symbol, ws)
                    logger.info(f"Subscrito ticks: {symbol}")
                    
                # loop de recebimento
                async for message in ws:
                    if not self.running:
                        break
                        
                    try:
                        data = json.loads(message)
                    except Exception as e:
                        logger.warning(f"Erro ao parsear mensagem WebSocket: {e}")
                        continue
                        
                    # mensagem de ticks geralmente tem a chave 'tick'
                    if "tick" in data:
                        await self._process_tick(data['tick'])
                        
        except Exception as e:
            logger.error(f"Erro na coleta de ticks: {e}")
        finally:
            self.status.collecting_ticks = False
            
    async def _subscribe_ticks(self, symbol: str, websocket):
        """Envia pedido de subscrição de ticks para o symbol"""
        req = {"ticks": symbol, "subscribe": 1}
        await websocket.send(json.dumps(req))
        
    async def _process_tick(self, tick_data):
        """Processa um tick recebido"""
        try:
            symbol = tick_data.get('symbol') or tick_data.get('quote') or tick_data.get('name') or tick_data.get('s')
            if not symbol:
                return
                
            # timestamp: alguns feeds trazem 'epoch' em segundos
            ts = float(tick_data.get('epoch', time.time()))
            price = float(tick_data.get('quote') or tick_data.get('ask') or tick_data.get('bid') or tick_data.get('price', 0))
            
            if symbol in ticks_store:
                ticks_store[symbol].append((ts, price))
            else:
                # caso símbolo não esteja no store (safety)
                ticks_store[symbol] = deque([(ts, price)], maxlen=20000)
                
        except Exception as e:
            logger.warning(f"Erro ao processar tick: {e}")
            
    async def _evaluation_loop(self):
        """Loop principal de avaliação e decisão"""
        try:
            while self.running:
                await asyncio.sleep(self.config.evaluation_interval)
                
                if not self.running:
                    break
                    
                try:
                    # Avalia todas as combinações
                    results = self._evaluate_all_combinations()
                    
                    if results and results.get('results'):
                        self.status.last_evaluation = datetime.utcnow()
                        self.status.total_evaluations += 1
                        self.status.best_combo = results['results'][0] if results['results'] else None
                        
                        logger.info(f"Avaliação #{self.status.total_evaluations} - Melhor: {self.status.best_combo}")
                        
                        # Se auto_execute está habilitado e temos um bom resultado
                        if self.config.auto_execute and self.status.best_combo:
                            await self._try_execute_trade()
                            
                except Exception as e:
                    logger.error(f"Erro na avaliação: {e}")
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Erro no loop de avaliação: {e}")
            
    def _evaluate_all_combinations(self) -> Dict:
        """
        Para cada símbolo e timeframe:
         - pega ticks recentes do ticks_store
         - agrega pra candles de timeframe
         - simula performance com critérios avançados
        Retorna ranking por score combinado (winrate + PnL + volume) ou net.
        """
        results = []
        cutoff_ts = time.time() - self.config.sim_window_seconds
        
        total_combinations = 0
        valid_combinations = 0
        
        for sym in self.config.symbols:
            ticks_list = list(ticks_store.get(sym, []))
            # filtrar somente ticks recentes (janela)
            ticks_recent = [(ts, p) for ts, p in ticks_list if ts >= cutoff_ts]
            
            if not ticks_recent:
                continue
                
            for tf_type, tf_val in self.config.timeframes:
                total_combinations += 1
                try:
                    candles = self._aggregate_to_candles(ticks_recent, tf_type, tf_val)
                    sim = self._simulate_simple_strategy(candles, STRAT, stake=self.config.sim_trade_stake)
                    
                    # Calcula score combinado
                    combined_score = self._calculate_combined_score(sim)
                    
                    # Adiciona métricas extras
                    result = {
                        "symbol": sym,
                        "tf_type": tf_type,
                        "tf_val": tf_val,
                        "timeframe_desc": f"{tf_type}{tf_val}",
                        "combined_score": combined_score,
                        "meets_criteria": self._meets_execution_criteria(sim),
                        "candles_count": len(candles),
                        **sim
                    }
                    
                    results.append(result)
                    
                    if sim['trades'] > 0:
                        valid_combinations += 1
                        
                except Exception as e:
                    logger.warning(f"Erro ao avaliar {sym} {tf_type}{tf_val}: {e}")
                    
        # Ordena por score combinado se configurado, senão por net
        if self.config.use_combined_score:
            results_sorted = sorted(results, key=lambda x: x['combined_score'], reverse=True)
        else:
            results_sorted = sorted(results, key=lambda x: (x['net'], x['winrate'] if x['winrate'] is not None else -1), reverse=True)
        
        # Estatísticas da avaliação COM ANÁLISE POR TIMEFRAME
        timeframe_stats = {
            "ticks": {"total": 0, "valid": 0, "meets_criteria": 0},
            "seconds": {"total": 0, "valid": 0, "meets_criteria": 0}, 
            "minutes": {"total": 0, "valid": 0, "meets_criteria": 0}
        }
        
        for result in results:
            tf_type = result.get('tf_type', 'ticks')
            category = "ticks" if tf_type == "ticks" else ("seconds" if tf_type == "s" else "minutes")
            
            timeframe_stats[category]["total"] += 1
            if result.get('trades', 0) > 0:
                timeframe_stats[category]["valid"] += 1
            if result.get('meets_criteria', False):
                timeframe_stats[category]["meets_criteria"] += 1
        
        self.status.evaluation_stats = {
            "total_combinations": total_combinations,
            "valid_combinations": valid_combinations,
            "symbols_evaluated": len(self.config.symbols),
            "timeframes_evaluated": len(self.config.timeframes),
            "conservative_mode": self.config.conservative_mode,
            "min_winrate_required": self.config.min_winrate,
            "min_trades_required": self.config.min_trades_sample,
            "min_pnl_required": self.config.min_pnl_positive,
        }
        
        # Adiciona estatísticas por tipo de timeframe
        self.status.timeframe_performance = timeframe_stats
        
        return {"results": results_sorted}
        
    def _calculate_combined_score(self, sim_result: Dict[str, Any]) -> float:
        """
        Calcula score combinado CONSERVADOR baseado em:
        - Winrate (peso 50% - maior peso para ser mais conservador)
        - PnL normalizado (peso 30% - menor peso)
        - Volume de trades normalizado (peso 10% - menor peso) 
        - Bonus por tipo de timeframe (peso 10% - prioriza timeframes 2-10min)
        """
        winrate = sim_result.get('winrate', 0) or 0
        net_pnl = sim_result.get('net', 0) or 0
        trades = sim_result.get('trades', 0) or 0
        tf_type = sim_result.get('tf_type', 'ticks')
        tf_val = sim_result.get('tf_val', 1)
        
        # Normaliza PnL (assume max possível ±10 para normalizar entre 0-1)
        pnl_normalized = max(0, min(1, (net_pnl + 10) / 20))
        
        # Normaliza volume de trades (assume max 20 trades na janela)
        volume_normalized = min(1, trades / 20)
        
        # NOVO: Calcula bonus por tipo de timeframe (favorece timeframes conservadores)
        timeframe_bonus = self._get_timeframe_weight_bonus(tf_type, tf_val)
        
        # Score combinado com pesos CONSERVADORES (mais peso para winrate)
        weights = self.config.score_weights
        combined_score = (
            winrate * weights["winrate"] +           # 50% winrate (vs 40% anterior)
            pnl_normalized * weights["pnl"] +        # 30% PnL (vs 40% anterior)
            volume_normalized * weights["volume"] +  # 10% volume (vs 20% anterior)
            timeframe_bonus * weights["timeframe"]   # 10% bonus timeframe (NOVO)
        )
        
        return combined_score
        
    def _get_timeframe_weight_bonus(self, tf_type: str, tf_val: int) -> float:
        """
        Calcula bonus de peso baseado no tipo de timeframe.
        Timeframes mais conservadores (2-10min) recebem bonus maior.
        """
        if not self.config.prefer_longer_timeframes:
            return 0.5  # Neutro se não preferir timeframes longos
            
        if tf_type == "ticks":
            # Ticks são MUITO arriscados, bonus MUITO baixo
            if tf_val <= 10:
                return 0.05  # MUITO baixo para até 10 ticks
            elif tf_val <= 25:
                return 0.1   # Muito baixo para até 25 ticks
            else:
                return 0.2   # Baixo para 50+ ticks
                
        elif tf_type == "s":
            # Segundos - bonus baixo, preferir minutos
            if tf_val <= 60:
                return 0.2  # Baixo para até 1 min
            elif tf_val <= 120:
                return 0.4  # Médio para 2 min
            else:
                return 0.5  # Médio-bom para 5+ min
                
        elif tf_type == "m":
            # Minutos - BONUS MÁXIMO (aqui está o foco!)
            if tf_val == 1:
                return 0.8  # Muito bom para 1 min
            elif 2 <= tf_val <= 5:
                return 1.0  # MÁXIMO ABSOLUTO para 2-5 min
            elif 6 <= tf_val <= 10:
                return 0.95 # Quase máximo para 6-10 min
            elif 15 <= tf_val <= 30:
                return 0.9  # Excelente para 15-30 min
            else:
                return 0.7  # Bom para outros
                
        return 0.5  # Default neutro
        
    def _meets_execution_criteria(self, sim_result: Dict[str, Any]) -> bool:
        """
        Verifica se o resultado atende aos critérios CONSERVADORES para execução de trades.
        Critérios mais rigorosos para ser mais assertivo.
        """
        winrate = sim_result.get('winrate', 0) or 0
        trades = sim_result.get('trades', 0) or 0
        net_pnl = sim_result.get('net', 0) or 0
        tf_type = sim_result.get('tf_type', 'ticks')
        tf_val = sim_result.get('tf_val', 1)
        
        # CRITÉRIOS BÁSICOS CONSERVADORES:
        basic_criteria = (
            winrate >= self.config.min_winrate and           # Winrate >= 75% (vs 70%)
            trades >= self.config.min_trades_sample and      # Trades >= 8 (vs 5) 
            net_pnl >= self.config.min_pnl_positive          # PnL >= 0.5 (NOVO critério)
        )
        
        if not basic_criteria:
            return False
            
        # CRITÉRIOS EXTRAS ULTRA CONSERVADORES se modo conservador ativo
        if self.config.conservative_mode:
            
            # Critério extra: para timeframes de ticks, exigir winrate MUITO maior
            if tf_type == "ticks":
                if tf_val <= 10:
                    if winrate < 0.90:  # 90% winrate para ticks até 10
                        return False
                elif tf_val <= 50:
                    if winrate < 0.88:  # 88% winrate para ticks até 50
                        return False
                        
            # Critério extra: timeframes de segundos também mais rigorosos
            if tf_type == "s":
                if tf_val <= 60:
                    if winrate < 0.87:  # 87% winrate para segundos curtos
                        return False
                    
            # Critério extra: MUITO mais trades para validação
            if tf_type == "ticks" and trades < 15:
                return False  # Ticks precisam de MUITOS mais trades
            if tf_type == "s" and trades < 12:
                return False  # Segundos precisam de mais trades
                
            # Critério extra: PnL por trade deve ser MUITO melhor
            pnl_per_trade = net_pnl / trades if trades > 0 else 0
            if pnl_per_trade < 0.15:  # Pelo menos 0.15 de PnL por trade (vs 0.1)
                return False
                
            # NOVO: Filtro extra para garantir consistência
            if winrate > 0 and winrate < 0.82:  # Qualquer coisa abaixo de 82% é rejeitada
                return False
                
        # BONUS: timeframes conservadores (2-10min) têm critérios ligeiramente relaxados
        if self.config.prefer_longer_timeframes and tf_type == "m" and 2 <= tf_val <= 10:
            # Para timeframes conservadores, aceitar winrate ligeiramente menor
            if winrate >= (self.config.min_winrate - 0.05) and trades >= self.config.min_trades_sample and net_pnl >= self.config.min_pnl_positive:
                return True
        
        return basic_criteria
        
    def _aggregate_to_candles(self, ticks: List[Tuple[float, float]], tf_type: str, tf_value: int) -> pd.DataFrame:
        """
        Agrupa uma lista de ticks (timestamp, price) em candles conforme timeframe.
        tf_type in {'ticks','s','m'}
        Retorna DataFrame com index = candle_start_ts e colunas ['open','high','low','close','volume']
        """
        if not ticks:
            return pd.DataFrame(columns=['open','high','low','close','volume'])

        df = pd.DataFrame(ticks, columns=['ts','price'])
        
        if tf_type == 'ticks':
            # criar candles por número de ticks
            group = np.arange(len(df)) // tf_value
            df['grp'] = group
            agg = df.groupby('grp').agg(
                open=('price','first'),
                high=('price','max'),
                low=('price','min'),
                close=('price','last'),
                ts=('ts','first'),
                volume=('price','count')
            )
            agg = agg.set_index('ts').sort_index()
            return agg[['open','high','low','close','volume']]
        else:
            # converter ts em bins de segundos
            if tf_type == 's':
                period = tf_value
            elif tf_type == 'm':
                period = tf_value * 60
            else:
                raise ValueError("tf_type inválido")
                
            # bucket by floor(ts / period) * period
            df['bucket'] = (df['ts'] // period) * period
            agg = df.groupby('bucket').agg(
                open=('price','first'),
                high=('price','max'),
                low=('price','min'),
                close=('price','last'),
                volume=('price','count')
            )
            agg.index.name = 'ts'
            agg = agg.sort_index()
            return agg[['open','high','low','close','volume']]
            
    def _compute_simple_indicators(self, candles: pd.DataFrame, ma_short: int, ma_long: int) -> pd.DataFrame:
        """Calcula indicadores simples"""
        if candles.empty:
            return candles
        c = candles.copy()
        c['close'] = c['close'].astype(float)
        c['ma_short'] = c['close'].rolling(ma_short, min_periods=1).mean()
        c['ma_long'] = c['close'].rolling(ma_long, min_periods=1).mean()
        c['ma_diff'] = c['ma_short'] - c['ma_long']
        return c

    def _simulate_simple_strategy(self, candles: pd.DataFrame, params: StrategyParams, stake=1.0) -> Dict[str, Any]:
        """
        Simula trades com regra: se ma_diff > 0 => CALL, se ma_diff < 0 => PUT.
        Usa candles para calcular taxa de acerto e lucro hipotético em uma janela curta.
        Para simplificação, assumimos que se price after expiry moved na direção, ganhamos 0.8x stake,
        caso contrário perdemos stake (exemplo de payout assume ~1:0.8). Ajuste conforme sua realidade.
        """
        if candles.empty or len(candles) < max(params.ma_long, params.ma_short)+1:
            return {"trades":0,"wins":0,"losses":0,"net":0.0,"winrate":None}
            
        df = self._compute_simple_indicators(candles, params.ma_short, params.ma_long)
        trades = 0
        wins = 0
        losses = 0
        net = 0.0
        
        # Simular: para cada candle, tomar decisão e comparar com candle 'expiry' ahead.
        expiry = max(1, int(params.expiry_seconds))  # aqui expiry em unidades de candles (aprox)
        closes = df['close'].values
        ma_diff = df['ma_diff'].values
        n = len(closes)
        
        for i in range(n - expiry):
            signal = np.sign(ma_diff[i])
            if signal == 0:
                continue
            trades += 1
            future = closes[i + expiry]
            current = closes[i]
            move = future - current
            direction = np.sign(move)
            
            if direction == signal:
                # win — assumir payout 0.8 * stake (EXEMPLO)
                wins += 1
                net += 0.8 * stake
            else:
                losses += 1
                net -= stake
                
        winrate = (wins / trades) if trades>0 else None
        return {"trades":trades,"wins":wins,"losses":losses,"net":net,"winrate":winrate}
        
    async def _try_execute_trade(self):
        """Tenta executar trade real baseado na melhor combinação COM CRITÉRIOS CONSERVADORES"""
        if not self.status.best_combo or not self.deriv_api:
            return
            
        try:
            # Verifica se o resultado atende aos critérios CONSERVADORES de execução
            best = self.status.best_combo
            
            # Usa critérios mais rigorosos - DEVE atender aos critérios conservadores
            if not best.get('meets_criteria', False):
                logger.info(f"❌ Melhor combo NÃO atende critérios conservadores: winrate={best.get('winrate', 0):.1%} (min: {self.config.min_winrate:.1%}), trades={best.get('trades', 0)} (min: {self.config.min_trades_sample}), pnl={best.get('net', 0):.2f} (min: {self.config.min_pnl_positive})")
                return
                
            # VALIDAÇÃO EXTRA CONSERVADORA: verificar se timeframe é adequado
            tf_type = best.get('tf_type', 'ticks')
            tf_val = best.get('tf_val', 1)
            winrate = best.get('winrate', 0)
            
            # Se for timeframe muito rápido (1-5 ticks), exigir winrate ainda maior
            if tf_type == "ticks" and tf_val <= 5 and winrate < 0.80:
                logger.info(f"❌ Timeframe ultra-rápido {tf_type}{tf_val} requer winrate >= 80%, atual: {winrate:.1%}")
                return
                
            # Log dos critérios CONSERVADORES atendidos
            logger.info(f"✅ CRITÉRIOS CONSERVADORES ATENDIDOS - Executando trade: winrate={winrate:.1%} (min: {self.config.min_winrate:.1%}), trades={best.get('trades', 0)} (min: {self.config.min_trades_sample}), pnl={best.get('net', 0):.2f} (min: {self.config.min_pnl_positive}), score={best.get('combined_score', 0):.3f}")
                
            # Determina direção baseada no último sinal
            symbol = best['symbol']
            ticks_recent = list(ticks_store.get(symbol, []))
            cutoff_ts = time.time() - self.config.sim_window_seconds
            ticks_filtered = [(ts, p) for ts, p in ticks_recent if ts >= cutoff_ts]
            
            if ticks_filtered:
                candles = self._aggregate_to_candles(ticks_filtered, best['tf_type'], best['tf_val'])
                df = self._compute_simple_indicators(candles, STRAT.ma_short, STRAT.ma_long)
                
                if not df.empty:
                    last_diff = df['ma_diff'].iloc[-1]
                    direction = "CALL" if last_diff > 0 else "PUT"
                    
                    # LOG DETALHADO para modo conservador
                    logger.info(f"🎯 EXECUTANDO TRADE CONSERVADOR: {symbol} {direction} stake={self.config.sim_trade_stake} [TF: {best['timeframe_desc']} | Winrate: {winrate:.1%} | PnL: {best.get('net', 0):.2f} | Score: {best.get('combined_score', 0):.3f}]")
                    
                    # Executa trade via API da Deriv com parâmetros automáticos
                    trade_result = await self._execute_real_trade(symbol, direction, self.config.sim_trade_stake, best)
                    
                    if trade_result:
                        self.status.trades_executed += 1
                        self.status.last_trade = {
                            "timestamp": datetime.utcnow(),
                            "symbol": symbol,
                            "direction": direction,
                            "stake": self.config.sim_trade_stake,
                            "reason": f"✅ CONSERVADOR: {best['timeframe_desc']} winrate={winrate:.1%} score={best.get('combined_score', 0):.3f}",
                            "conservative_criteria": {
                                "min_winrate_met": winrate >= self.config.min_winrate,
                                "min_trades_met": best.get('trades', 0) >= self.config.min_trades_sample,
                                "min_pnl_met": best.get('net', 0) >= self.config.min_pnl_positive,
                                "timeframe_appropriate": not (tf_type == "ticks" and tf_val <= 5 and winrate < 0.80)
                            },
                            "performance_metrics": {
                                "winrate": best.get('winrate', 0),
                                "net_pnl": best.get('net', 0),
                                "trades_sample": best.get('trades', 0),
                                "combined_score": best.get('combined_score', 0),
                                "timeframe_bonus": self._get_timeframe_weight_bonus(tf_type, tf_val)
                            },
                            **trade_result
                        }
                        
                        logger.info(f"🎉 TRADE CONSERVADOR EXECUTADO COM SUCESSO: contract_id={trade_result.get('contract_id')}")
                        
        except Exception as e:
            logger.error(f"❌ Erro ao executar trade conservador: {e}")
            
    async def _execute_real_trade(self, symbol: str, direction: str, stake: float, best_combo: Dict) -> Optional[Dict]:
        """
        Executa trade real via API da Deriv usando o endpoint interno /deriv/buy
        Usa automaticamente a duração e unidade do melhor timeframe encontrado
        """
        if not self.deriv_api or not self.deriv_api.connected:
            logger.error(f"API Deriv não conectada - não é possível executar trade real")
            return None
            
        try:
            # Importa BuyRequest do módulo principal
            import sys
            import os
            sys.path.append(os.path.dirname(__file__))
            from server import BuyRequest, deriv_buy
            
            # Converte o timeframe do melhor combo para duration e duration_unit da Deriv
            tf_type = best_combo.get('tf_type', 'ticks')
            tf_val = best_combo.get('tf_val', 5)
            
            # Mapeia tipos de timeframe para unidades da Deriv
            duration, duration_unit = self._convert_timeframe_to_deriv_params(tf_type, tf_val)
            
            # Cria requisição de compra com parâmetros automáticos
            buy_request = BuyRequest(
                symbol=symbol,
                type="CALLPUT",
                contract_type=direction,  # "CALL" ou "PUT"
                duration=duration,
                duration_unit=duration_unit,
                stake=stake,
                currency="USD"
            )
            
            logger.info(f"Executando trade REAL via Deriv API: {symbol} {direction} stake={stake} duration={duration}{duration_unit} [Auto-TF: {tf_type}{tf_val}]")
            
            # Executa trade real usando endpoint interno
            result = await deriv_buy(buy_request)
            
            if result:
                logger.info(f"Trade REAL executado com sucesso: contract_id={result.get('contract_id')}, buy_price={result.get('buy_price')}")
                return {
                    "status": "executed",
                    "contract_id": result.get("contract_id"),
                    "buy_price": result.get("buy_price"),
                    "payout": result.get("payout"),
                    "symbol": symbol,
                    "direction": direction,
                    "stake": stake,
                    "duration": duration,
                    "duration_unit": duration_unit,
                    "auto_timeframe": f"{tf_type}{tf_val}"
                }
            else:
                logger.error(f"Falha ao executar trade real - resultado vazio")
                return None
                
        except Exception as e:
            logger.error(f"Erro ao executar trade real: {e}")
            return None
            
    def _convert_timeframe_to_deriv_params(self, tf_type: str, tf_val: int) -> Tuple[int, str]:
        """
        Converte timeframe interno para parâmetros da API Deriv
        Aplica limites inteligentes baseados nas limitações da Deriv
        """
        if tf_type == 'ticks':
            # Ticks: 1-10 para contratos curtos, limitar valores extremos
            duration = max(1, min(tf_val, 10))  # Entre 1 e 10 ticks
            return duration, "t"
        elif tf_type == 's':
            # Segundos: 15s-300s (5min) são os limites típicos da Deriv para segundos
            if tf_val < 15:
                duration = 15  # Mínimo 15 segundos
            elif tf_val > 300:
                duration = 300  # Máximo 5 minutos em segundos
            else:
                duration = tf_val
            return duration, "s"
        elif tf_type == 'm':
            # Minutos: 1-60 minutos são limites típicos
            duration = max(1, min(tf_val, 60))  # Entre 1 e 60 minutos
            return duration, "m"
        else:
            # Fallback para ticks se tipo desconhecido
            logger.warning(f"Tipo de timeframe desconhecido: {tf_type}, usando fallback para 5 ticks")
            return 5, "t"

# Instância global do bot
auto_bot = AutoSelectionBot()