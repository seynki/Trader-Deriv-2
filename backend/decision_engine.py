from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
from strategies import StrategyContext
from strategies import registry as strat_registry

CONFIG_DIR = Path(__file__).parent / "config"
CONFIG_DIR.mkdir(exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "weights": {
        "river": 0.35,
        "ma_crossover": 0.2,
        "rsi_reinforced": 0.2,
        "ml_engine": 0.25
    },
    "decision_threshold": 0.55,
    "min_strategies_agree": 1
}


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return DEFAULT_CONFIG


def save_config(cfg: Dict[str, Any]):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


class WeightedVotingDecisionEngine:
    def __init__(self, config: Dict[str, Any] | None = None):
        self.config = config or load_config()
        self.weights: Dict[str, float] = self.config.get("weights", {})
        self.threshold: float = float(self.config.get("decision_threshold", 0.55))
        self.min_agree: int = int(self.config.get("min_strategies_agree", 1))

    def evaluate(self, df: pd.DataFrame, ctx: StrategyContext) -> Dict[str, Any]:
        details: List[Dict[str, Any]] = []
        votes: Dict[str, float] = {"RISE": 0.0, "FALL": 0.0}
        active_strats = []
        for name, cls in strat_registry.REGISTRY.items():
            try:
                strat = cls()
                d = strat.decide(df, ctx)
                details.append({"strategy": name, "decision": d.__dict__})
                w = float(self.weights.get(name, 0.0))
                if d.signal in ("RISE", "FALL"):
                    votes[d.signal] += w * float(d.confidence)
                    active_strats.append(name)
            except Exception as e:
                details.append({"strategy": name, "error": str(e)})
        # final decision
        side = "NEUTRAL"
        score = 0.0
        agree_count = sum(1 for x in details if isinstance(x.get("decision"), dict) and x["decision"].get("signal") in ("RISE","FALL"))
        if agree_count >= self.min_agree:
            if votes["RISE"] >= votes["FALL"] and votes["RISE"] >= self.threshold:
                side = "RISE"
                score = votes["RISE"]
            elif votes["FALL"] > votes["RISE"] and votes["FALL"] >= self.threshold:
                side = "FALL"
                score = votes["FALL"]
        return {"decision": side, "score": score, "votes": votes, "details": details, "used_strategies": active_strats}
