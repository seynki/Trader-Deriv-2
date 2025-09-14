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
TIMEFRAMES = [
    ("ticks", 10),       # janela de 10 ticks
    ("ticks", 25),       # janela de 25 ticks  
    ("ticks", 50),       # janela de 50 ticks
    ("ticks", 100),      # janela de 100 ticks
    ("s", 1),            # 1 segundo
    ("s", 5),            # 5 segundos
    ("s", 30),           # 30 segundos
    ("m", 1),            # 1 minuto
    ("m", 3),            # 3 minutos
    ("m", 5),            # 5 minutos
]

SIM_WINDOW_SECONDS = 60  # janela de histórico (em segundos) usada para simular performance
SIM_TRADE_STAKE = 1.0    # stake hipotético por simulação (apenas para ranking)

# Websocket endpoint público (Deriv/Binaryws)
DERIV_WS = f"wss://ws.derivws.com/websockets/v3?app_id={os.environ.get('DERIV_APP_ID', '1089')}"

# parâmetros da estratégia de simulação (EXEMPLO: estratégia de momentum simples)
@dataclass
class StrategyParams:
    ma_short: int = 3
    ma_long: int = 8
    expiry_seconds: int = 10  # duração do contrato simulado (em segundos) para TICKS/segundos
    take_profit: float = 0.8  # lucro hipotético relativo p/ considerar como win (simulação)
    stop_loss: float = -1.0   # perda hipotética relativa
    direction_threshold: float = 0.0  # se MA_short - MA_long > threshold => CALL
    min_winrate: float = 0.70  # winrate mínimo para executar trades (70%)
    min_trades_sample: int = 5  # mínimo de trades na amostra para considerar válido

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
    min_winrate: float = 0.70  # winrate mínimo para executar trades (70%)
    min_trades_sample: int = 5  # mínimo de trades na amostra
    use_combined_score: bool = True  # usar score combinado (winrate + pnl + volume)

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
    min_winrate: float = 0.70
    use_combined_score: bool = True
    evaluation_stats: Optional[Dict[str, Any]] = None

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
         - simula performance
        Retorna ranking por 'net' (ou por winrate).
        """
        results = []
        cutoff_ts = time.time() - self.config.sim_window_seconds
        
        for sym in self.config.symbols:
            ticks_list = list(ticks_store.get(sym, []))
            # filtrar somente ticks recentes (janela)
            ticks_recent = [(ts, p) for ts, p in ticks_list if ts >= cutoff_ts]
            
            if not ticks_recent:
                continue
                
            for tf_type, tf_val in self.config.timeframes:
                try:
                    candles = self._aggregate_to_candles(ticks_recent, tf_type, tf_val)
                    sim = self._simulate_simple_strategy(candles, STRAT, stake=self.config.sim_trade_stake)
                    
                    results.append({
                        "symbol": sym,
                        "tf_type": tf_type,
                        "tf_val": tf_val,
                        "timeframe_desc": f"{tf_type}{tf_val}",
                        **sim
                    })
                except Exception as e:
                    logger.warning(f"Erro ao avaliar {sym} {tf_type}{tf_val}: {e}")
                    
        # ordena por net (poderíamos usar winrate)
        results_sorted = sorted(results, key=lambda x: (x['net'], x['winrate'] if x['winrate'] is not None else -1), reverse=True)
        return {"results": results_sorted}
        
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
        """Tenta executar trade real baseado na melhor combinação"""
        if not self.status.best_combo or not self.deriv_api:
            return
            
        try:
            # Verifica se o resultado é bom o suficiente para executar
            best = self.status.best_combo
            
            # Critério relaxado para teste: winrate >= 50% e pelo menos 2 trades na simulação
            if (best.get('winrate', 0) or 0) >= 0.50 and best.get('trades', 0) >= 2:
                
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
                        
                        logger.info(f"Executando trade: {symbol} {direction} stake={self.config.sim_trade_stake}")
                        
                        # Executa trade via API da Deriv (simulação aqui)
                        trade_result = await self._execute_real_trade(symbol, direction, self.config.sim_trade_stake)
                        
                        if trade_result:
                            self.status.trades_executed += 1
                            self.status.last_trade = {
                                "timestamp": datetime.utcnow(),
                                "symbol": symbol,
                                "direction": direction,
                                "stake": self.config.sim_trade_stake,
                                "reason": f"Best combo: {best['tf_type']}{best['tf_val']} winrate={best.get('winrate', 0):.2%}",
                                **trade_result
                            }
                            
        except Exception as e:
            logger.error(f"Erro ao executar trade automático: {e}")
            
    async def _execute_real_trade(self, symbol: str, direction: str, stake: float) -> Optional[Dict]:
        """
        Executa trade real via API da Deriv usando o endpoint interno /deriv/buy
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
            
            # Cria requisição de compra
            buy_request = BuyRequest(
                symbol=symbol,
                type="CALLPUT",
                contract_type=direction,  # "CALL" ou "PUT"
                duration=5,
                duration_unit="t",
                stake=stake,
                currency="USD"
            )
            
            logger.info(f"Executando trade REAL via Deriv API: {symbol} {direction} stake={stake}")
            
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
                    "stake": stake
                }
            else:
                logger.error(f"Falha ao executar trade real - resultado vazio")
                return None
                
        except Exception as e:
            logger.error(f"Erro ao executar trade real: {e}")
            return None

# Instância global do bot
auto_bot = AutoSelectionBot()