from __future__ import annotations
from typing import Any, Dict, Callable
import optuna

# We optimize decision engine weights + threshold.
# The objective_func will receive a trial and must return a score to maximize.


def optimize_decision_engine(objective_func: Callable[[optuna.Trial], float], n_trials: int = 20, timeout: int | None = None) -> Dict[str, Any]:
    study = optuna.create_study(direction="maximize")
    study.optimize(objective_func, n_trials=n_trials, timeout=timeout)
    return {
        "best_value": study.best_value,
        "best_params": study.best_params,
        "trials": len(study.trials),
    }
