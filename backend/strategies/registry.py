from __future__ import annotations
from typing import Dict, Type
from .base import BaseStrategy
from .ma_crossover import MACrossoverStrategy
from .rsi_reinforced_strategy import RSIReinforcedStrategy
from .river_strategy import RiverStrategy
from .ml_engine_strategy import MLEngineStrategy
from .hybrid import HybridStrategy

REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "ma_crossover": MACrossoverStrategy,
    "rsi_reinforced": RSIReinforcedStrategy,
    "river": RiverStrategy,
    "ml_engine": MLEngineStrategy,
    "hybrid": HybridStrategy,
}

def create(name: str) -> BaseStrategy:
    if name not in REGISTRY:
        raise ValueError(f"Estratégia desconhecida: {name}")
    return REGISTRY[name]()
