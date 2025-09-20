"""
River-based Online Learning for Candle Data (OHLCV)
- Single model for Long/Short (binary: next_close > close)
- Designed to be called per-candle (online/incremental)
- Compatible with FastAPI endpoints in server.py

CSV expected columns: datetime, open, high, low, close, volume
"""

import pandas as pd
import numpy as np
import pickle
import shutil
from collections import deque
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
import json
import time

# River ML (online)
from river import preprocessing, linear_model, metrics, compose

# -----------------------
# Config
# -----------------------
ROLLING_WINDOW = 50
MIN_TICK = 1e-8
MODEL_SAVE_PATH = "/app/backend/ml_models/river_online_model.pkl"
BACKUP_DIR = "/app/backend/ml_models/river_backups"
METADATA_PATH = "/app/backend/ml_models/river_metadata.json"


class RiverOnlineCandleModel:
    def __init__(self):
        # Pipeline: StandardScaler (online) + LogisticRegression (online)
        self.model = compose.Pipeline(
            preprocessing.StandardScaler(),
            linear_model.LogisticRegression()
        )
        # Metrics
        self.metric_acc = metrics.Accuracy()
        self.metric_logloss = metrics.LogLoss()
        # Rolling state
        self.closes = deque(maxlen=ROLLING_WINDOW)
        self.vols = deque(maxlen=ROLLING_WINDOW)
        # Internal counters
        self.sample_count = 0

    def _parse_ts(self, ts: Any):
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00").replace(" ", "T"))
            except Exception:
                # try pandas
                return pd.to_datetime(ts, utc=True).to_pydatetime()
        elif isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts))
        elif isinstance(ts, datetime):
            return ts
        return datetime.utcnow()

    def _make_features(self, timestamp, o, h, l, c, v) -> Dict[str, float]:
        # update history
        self.closes.append(float(c))
        self.vols.append(float(v) if v is not None else 0.0)

        closes = np.array(self.closes, dtype=float)
        vols = np.array(self.vols, dtype=float)

        # returns
        ret_1 = 0.0
        if len(closes) >= 2:
            prev = closes[-2]
            ret_1 = float(np.log((c + MIN_TICK) / (prev + MIN_TICK)))

        sma = float(closes.mean()) if len(closes) > 0 else float(c)
        std = float(closes.std(ddof=0)) if len(closes) > 1 else 0.0
        vol_mean = float(vols.mean()) if len(vols) > 0 else 0.0

        ts = self._parse_ts(timestamp)
        seconds = ts.hour * 3600 + ts.minute * 60 + ts.second
        sec_in_day = 24 * 3600
        tod_sin = float(np.sin(2 * np.pi * seconds / sec_in_day))
        tod_cos = float(np.cos(2 * np.pi * seconds / sec_in_day))

        # range/volatility features from candle
        hl_range = float((h - l) if h is not None and l is not None else 0.0)
        body = float((c - o) if o is not None and c is not None else 0.0)

        return {
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v if v is not None else 0.0),
            "ret_1": float(ret_1),
            "sma": float(sma),
            "std": float(std),
            "vol_mean": float(vol_mean),
            "tod_sin": float(tod_sin),
            "tod_cos": float(tod_cos),
            "hl_range": float(hl_range),
            "body": float(body),
        }

    def predict_and_update(self, timestamp, o, h, l, c, v, next_close: Optional[float] = None) -> Dict[str, Any]:
        x = self._make_features(timestamp, o, h, l, c, v)
        # predict prob up
        try:
            y_proba = self.model.predict_proba_one(x)
        except Exception:
            y_proba = {}
        prob_up = y_proba.get(1, None)
        if prob_up is None:
            try:
                pred_class = self.model.predict_one(x)
                prob_up = 0.9 if pred_class == 1 else 0.1
            except Exception:
                prob_up = 0.5
        pred_class = 1 if float(prob_up) >= 0.5 else 0

        info = {
            "features": x,
            "pred_class": int(pred_class),
            "prob_up": float(prob_up),
            "signal": "LONG" if pred_class == 1 else "SHORT",
        }

        if next_close is not None:
            label = 1 if float(next_close) > float(c) else 0
            # update metrics before learning
            self.metric_acc.update(label, pred_class)
            self.metric_logloss.update(label, prob_up)
            # online update
            self.model.learn_one(x, label)
            self.sample_count += 1
            info.update({
                "label": int(label),
                "acc": float(self.metric_acc.get()),
                "logloss": float(self.metric_logloss.get()),
                "samples": int(self.sample_count),
            })
        return info

    def save(self, path: str = MODEL_SAVE_PATH):
        # ensure parent dir
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        
        # 🔄 BACKUP AUTOMÁTICO: Criar backup antes de salvar
        self._create_backup()
        
        with open(path, "wb") as f:
            pickle.dump(self, f)
            
        # Atualizar metadados
        self._update_metadata()
    
    def _create_backup(self):
        """Cria backup automático do modelo River com timestamp"""
        try:
            Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
            
            if Path(MODEL_SAVE_PATH).exists():
                timestamp = int(time.time())
                backup_filename = f"river_model_samples_{self.sample_count}_{timestamp}.pkl"
                backup_path = Path(BACKUP_DIR) / backup_filename
                
                # Copiar modelo atual para backup
                shutil.copy2(MODEL_SAVE_PATH, backup_path)
                print(f"🔄 Backup River criado: {backup_filename} (samples: {self.sample_count})")
                
                # Manter apenas os últimos 10 backups
                self._cleanup_old_backups()
                
        except Exception as e:
            print(f"⚠️ Erro criando backup River: {e}")
    
    def _cleanup_old_backups(self):
        """Remove backups antigos, mantendo apenas os 10 mais recentes"""
        try:
            backup_files = list(Path(BACKUP_DIR).glob("river_model_samples_*.pkl"))
            if len(backup_files) > 10:
                # Ordenar por timestamp (mais antigo primeiro)
                backup_files.sort()
                for old_backup in backup_files[:-10]:
                    old_backup.unlink()
                    print(f"🗑️ Backup antigo removido: {old_backup.name}")
        except Exception as e:
            print(f"⚠️ Erro limpando backups antigos: {e}")
    
    def _update_metadata(self):
        """Atualiza metadados do modelo River"""
        try:
            metadata = {
                "last_update": datetime.utcnow().isoformat(),
                "sample_count": self.sample_count,
                "accuracy": float(self.metric_acc.get()) if self.sample_count > 0 else None,
                "logloss": float(self.metric_logloss.get()) if self.sample_count > 0 else None,
                "model_path": MODEL_SAVE_PATH,
                "rolling_window": ROLLING_WINDOW
            }
            
            with open(METADATA_PATH, "w") as f:
                json.dump(metadata, f, indent=2)
                
        except Exception as e:
            print(f"⚠️ Erro atualizando metadados River: {e}")
    
    @staticmethod
    def list_backups():
        """Lista todos os backups disponíveis do River"""
        try:
            backup_files = list(Path(BACKUP_DIR).glob("river_model_samples_*.pkl"))
            backups = []
            
            for backup_file in sorted(backup_files, reverse=True):
                # Extrair informações do nome do arquivo
                parts = backup_file.stem.split('_')
                if len(parts) >= 4:
                    try:
                        samples = int(parts[3])
                        timestamp = int(parts[4])
                        date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                        
                        backups.append({
                            "file": backup_file.name,
                            "path": str(backup_file),
                            "samples": samples,
                            "timestamp": timestamp,
                            "date": date_str,
                            "size_mb": round(backup_file.stat().st_size / (1024*1024), 2)
                        })
                    except (ValueError, IndexError):
                        continue
            
            return backups
        except Exception as e:
            print(f"⚠️ Erro listando backups River: {e}")
            return []
    
    @staticmethod
    def restore_from_backup(backup_filename: str):
        """Restaura modelo River de um backup específico"""
        try:
            backup_path = Path(BACKUP_DIR) / backup_filename
            if not backup_path.exists():
                raise FileNotFoundError(f"Backup não encontrado: {backup_filename}")
            
            # Criar backup do modelo atual antes de restaurar
            if Path(MODEL_SAVE_PATH).exists():
                current_timestamp = int(time.time())
                current_backup = Path(BACKUP_DIR) / f"river_model_current_before_restore_{current_timestamp}.pkl"
                shutil.copy2(MODEL_SAVE_PATH, current_backup)
                print(f"🔄 Backup do modelo atual criado: {current_backup.name}")
            
            # Restaurar backup
            shutil.copy2(backup_path, MODEL_SAVE_PATH)
            print(f"✅ Modelo River restaurado de: {backup_filename}")
            
            # Carregar modelo restaurado para verificar
            restored_model = RiverOnlineCandleModel.load()
            print(f"📊 Modelo restaurado: {restored_model.sample_count} amostras, "
                  f"acc: {restored_model.metric_acc.get():.3f}")
            
            return True
            
        except Exception as e:
            print(f"❌ Erro restaurando backup River: {e}")
            return False

    @staticmethod
    def load(path: str = MODEL_SAVE_PATH):
        with open(path, "rb") as f:
            return pickle.load(f)


def run_on_dataframe(df: pd.DataFrame, model: Optional[RiverOnlineCandleModel] = None) -> Dict[str, Any]:
        """Simulate streaming over a OHLCV dataframe (sorted by datetime)"""
        required_cols = {"datetime", "open", "high", "low", "close", "volume"}
        # normalize column names to lower
        df2 = df.copy()
        df2.columns = [c.lower() for c in df2.columns]
        if not required_cols.issubset(set(df2.columns)):
            raise ValueError(f"CSV precisa conter colunas: {sorted(list(required_cols))}")
        df2 = df2.sort_values("datetime").reset_index(drop=True)

        if model is None:
            model = RiverOnlineCandleModel()

        logs = []
        for i in range(len(df2)):
            row = df2.iloc[i]
            next_close = float(df2.iloc[i + 1]["close"]) if i + 1 < len(df2) else None
            info = model.predict_and_update(
                row["datetime"], float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row.get("volume", 0.0)),
                next_close=next_close,
            )
            if i % 100 == 0:
                logs.append({"i": i, "prob_up": round(info["prob_up"], 4), "pred": int(info["pred_class"]), "label": info.get("label")})

        model.save()
        summary = {
            "message": "treino online finalizado",
            "model_path": MODEL_SAVE_PATH,
            "samples": int(model.sample_count),
            "acc": float(model.metric_acc.get()) if model.sample_count > 0 else None,
            "logloss": float(model.metric_logloss.get()) if model.sample_count > 0 else None,
            "logs": logs[-5:],
        }
        return {"model": model, "summary": summary}