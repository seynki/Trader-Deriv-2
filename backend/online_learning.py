"""
Online Learning Module for ML Trading Models
Implements incremental learning to adapt models with new market data
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, List, Tuple
from sklearn.linear_model import SGDClassifier, PassiveAggressiveClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score
from joblib import dump, load
import json
import time
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class OnlineLearningModel:
    """
    Online Learning Model that can adapt incrementally to new data
    Supports multiple algorithms: SGD, PassiveAggressive, Mini-batch RF
    """
    
    def __init__(self, 
                 model_type: str = "sgd",
                 learning_rate: str = "adaptive", 
                 alpha: float = 0.0001,
                 max_iter: int = 1000,
                 class_weight: str = "balanced",
                 random_state: int = 42):
        
        self.model_type = model_type
        self.learning_rate = learning_rate
        self.alpha = alpha
        self.max_iter = max_iter
        self.class_weight = class_weight
        self.random_state = random_state
        
        # Initialize the model
        self.model = self._create_model()
        self.is_fitted = False
        self.features = None
        self.update_count = 0
        self.performance_history = []
        
        # Buffer for mini-batch updates
        self.buffer_X = []
        self.buffer_y = []
        self._partial_fit_initialized = False
        self._known_classes = np.array([0, 1], dtype=int)
        self._fitted_classes = None

        self.buffer_size = 10  # Update every 10 samples
        
    def _create_model(self):
        """Create the online learning model based on type"""
        if self.model_type == "sgd":
            return SGDClassifier(
                loss='log_loss',  # For probability estimates
                learning_rate=self.learning_rate,
                alpha=self.alpha,
                max_iter=self.max_iter,
                class_weight=self.class_weight,
                random_state=self.random_state,
                warm_start=True,  # Allows incremental learning
                eta0=0.01  # Initial learning rate
            )
        elif self.model_type == "passive_aggressive":
            return PassiveAggressiveClassifier(
                C=1.0,
                max_iter=self.max_iter,
                class_weight=self.class_weight,
                random_state=self.random_state,
                warm_start=True
            )
        else:
            raise ValueError(f"Unsupported model type: {self.model_type}")
    
    def initial_fit(self, X: pd.DataFrame, y: pd.Series, features: List[str]):
        """Initial training of the model"""
        self.features = features
        X_filtered = X[features].fillna(0)
        
        # Replace inf values
        X_filtered = X_filtered.replace([np.inf, -np.inf], 0)
        
        y_clean = pd.Series(y).astype(int)
        self.model.fit(X_filtered, y_clean)
        self.is_fitted = True
        # Cache fitted classes for safety
        try:
            self._fitted_classes = np.array(sorted(set(int(v) for v in pd.Series(self.model.classes_).astype(int).tolist())), dtype=int)
        except Exception:
            self._fitted_classes = self._known_classes
        # After a full fit(), scikit sets classes_; next partial_fit can omit classes
        self._partial_fit_initialized = True
        
        # Evaluate initial performance
        y_pred = self.model.predict(X_filtered)
        accuracy = accuracy_score(y_clean, y_pred)
        precision = precision_score(y_clean, y_pred, zero_division=0)
        
        self.performance_history.append({
            'timestamp': datetime.utcnow().isoformat(),
            'accuracy': float(accuracy),
            'precision': float(precision),
            'update_count': self.update_count,
            'sample_size': len(y_clean)
        })
        
        logger.info(f"Online model initial fit: accuracy={accuracy:.3f}, precision={precision:.3f}")
        
    def partial_fit(self, X: pd.DataFrame, y: pd.Series):
        """Incrementally update the model with new data"""
        if self.features is None:
            raise ValueError("Features not set")
            
        X_filtered = X[self.features].fillna(0)
        X_filtered = X_filtered.replace([np.inf, -np.inf], 0)
        y_clean = pd.Series(y).astype(int)
        
        # Use partial_fit for incremental learning
        if hasattr(self.model, 'partial_fit'):
            # Determine classes to pass on first incremental call
            first_incremental = not self._partial_fit_initialized
            if first_incremental:
                # Build a safe classes array that always contains the current labels and [0,1]
                try:
                    batch_labels = np.array(sorted(set(int(v) for v in pd.Series(y_clean).astype(int).unique().tolist())), dtype=int)
                except Exception:
                    batch_labels = np.array([0, 1], dtype=int)
                classes_to_use = np.array(sorted(set(batch_labels.tolist() + self._known_classes.tolist())), dtype=int)
                try:
                    logger.info(f"OnlineLearningModel.partial_fit first call - classes={classes_to_use.tolist()}, y={batch_labels.tolist()}")
                except Exception:
                    pass
                self.model.partial_fit(X_filtered, y_clean, classes=classes_to_use)
                self.is_fitted = True
                self._partial_fit_initialized = True
            else:
                self.model.partial_fit(X_filtered, y_clean)
        else:
            # For models that don't support partial_fit, use mini-batch approach
            self._mini_batch_update(X_filtered, y_clean)
        
        self.update_count += len(y_clean)
        
        # Evaluate performance periodically (every 5 trades as solicitado)
        if self.update_count % 5 == 0:
            self._evaluate_performance(X_filtered, y_clean)
    
    def _mini_batch_update(self, X: pd.DataFrame, y: pd.Series):
        """Mini-batch update for models that don't support partial_fit"""
        self.buffer_X.append(X)
        self.buffer_y.append(y)
        
        if len(self.buffer_X) >= self.buffer_size:
            # Combine buffered data
            X_batch = pd.concat(self.buffer_X, ignore_index=True)
            y_batch = pd.concat(self.buffer_y, ignore_index=True)
            
            # Retrain with combined data (for tree-based models)
            self.model.fit(X_batch, y_batch)
            
            # Clear buffer
            self.buffer_X = []
            self.buffer_y = []
    
    def _evaluate_performance(self, X: pd.DataFrame, y: pd.Series):
        """Evaluate current model performance"""
        if len(y) == 0:
            return
            
        try:
            y_pred = self.model.predict(X)
            accuracy = accuracy_score(y, y_pred)
            precision = precision_score(y, y_pred, zero_division=0)
            
            self.performance_history.append({
                'timestamp': datetime.utcnow().isoformat(),
                'accuracy': float(accuracy),
                'precision': float(precision),
                'update_count': self.update_count,
                'sample_size': len(y)
            })
            
            logger.info(f"Online model performance: accuracy={accuracy:.3f}, precision={precision:.3f}, updates={self.update_count}")
            
        except Exception as e:
            logger.warning(f"Performance evaluation failed: {e}")
    
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Make predictions"""
        if not self.is_fitted:
            raise ValueError("Model not fitted")
            
        X_filtered = X[self.features].fillna(0)
        X_filtered = X_filtered.replace([np.inf, -np.inf], 0)
        
        return self.model.predict(X_filtered)
    
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Get prediction probabilities"""
        if not self.is_fitted:
            raise ValueError("Model not fitted")
            
        X_filtered = X[self.features].fillna(0)
        X_filtered = X_filtered.replace([np.inf, -np.inf], 0)
        
        if hasattr(self.model, 'predict_proba'):
            return self.model.predict_proba(X_filtered)
        else:
            # For models without predict_proba, use decision_function
            scores = self.model.decision_function(X_filtered)
            # Convert to probabilities using sigmoid
            proba_pos = 1 / (1 + np.exp(-scores))
            return np.column_stack([1-proba_pos, proba_pos])
    
    def get_performance_history(self) -> List[Dict[str, Any]]:
        """Get model performance history"""
        return self.performance_history
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get model information"""
        return {
            'model_type': self.model_type,
            'is_fitted': self.is_fitted,
            'update_count': self.update_count,
            'features_count': len(self.features) if self.features else 0,
            'buffer_size': len(self.buffer_X),
            'performance_samples': len(self.performance_history)
        }


class OnlineLearningManager:
    """
    Manager for online learning models
    Handles model lifecycle, data ingestion, and adaptation
    """
    
    def __init__(self, models_dir: str = "/app/backend/ml_models"):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(exist_ok=True)
        self.active_models: Dict[str, OnlineLearningModel] = {}
        self.adaptation_buffer: Dict[str, List[Dict[str, Any]]] = {}
        
    def create_online_model(self, 
                          model_id: str,
                          initial_data: pd.DataFrame,
                          features: List[str],
                          target_col: str = 'target',
                          model_type: str = "sgd") -> OnlineLearningModel:
        """Create and initially train an online learning model"""
        
        # Create online model
        online_model = OnlineLearningModel(model_type=model_type)
        
        # Prepare data
        X = initial_data[features]
        y = initial_data[target_col]
        
        # Initial fit
        online_model.initial_fit(X, y, features)
        
        # Store model
        self.active_models[model_id] = online_model
        self.adaptation_buffer[model_id] = []
        
        # Save model to disk
        self._save_online_model(model_id, online_model)
        
        logger.info(f"Created online learning model: {model_id}")
        return online_model
    
    def adapt_model(self, 
                   model_id: str, 
                   trade_data: Dict[str, Any],
                   market_data: pd.DataFrame,
                   trade_outcome: int = None) -> bool:
        """
        Enhanced adapt model with explicit trade outcome and return success status
        """
        
        if model_id not in self.active_models:
            logger.warning(f"Model {model_id} not found for adaptation")
            return False
        
        model = self.active_models[model_id]
        
        # Determine outcome if not provided
        if trade_outcome is None:
            profit = trade_data.get('profit', 0)
            trade_outcome = 1 if profit > 0 else 0
        
        # Add to buffer with enhanced data
        adaptation_item = {
            'trade_data': trade_data,
            'market_data': market_data,
            'trade_outcome': trade_outcome,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        self.adaptation_buffer[model_id].append(adaptation_item)
        
        # Process buffer immediately for each trade (no batching delay)
        success = self._process_adaptation_buffer(model_id)
        
        return success
    
    def _process_adaptation_buffer(self, model_id: str) -> bool:
        """Enhanced process buffered adaptation data with immediate updates"""
        if model_id not in self.active_models:
            return False
            
        model = self.active_models[model_id]
        buffer = self.adaptation_buffer[model_id]
        
        if not buffer:
            return False
        
        # Process each item in buffer immediately (no batching)
        success_count = 0
        
        for item in buffer:
            try:
                trade_data = item['trade_data']
                market_data = item['market_data']
                trade_outcome = item.get('trade_outcome')
                
                if isinstance(market_data, pd.DataFrame) and len(market_data) > 0:
                    # Use the latest market state
                    latest_market = market_data.iloc[-1:].copy()
                    
                    # Use explicit trade outcome if provided
                    if trade_outcome is not None:
                        target = trade_outcome
                    else:
                        # Fallback to profit-based outcome
                        profit = trade_data.get('profit', 0)
                        target = 1 if profit > 0 else 0
                    
                    latest_market['target'] = target
                    target_series = pd.Series([target])
                    
                    # Update model immediately
                    model.partial_fit(latest_market, target_series)
                    success_count += 1
                    
                    logger.info(f"✅ Model {model_id} updated with trade outcome: {target} (profit: {trade_data.get('profit', 0):.2f})")
                    
            except Exception as e:
                logger.error(f"❌ Failed to process adaptation item for model {model_id}: {e}")
        
        if success_count > 0:
            try:
                # Save updated model
                self._save_online_model(model_id, model)
                logger.info(f"💾 Model {model_id} saved after {success_count} updates")
            except Exception as e:
                logger.warning(f"⚠️ Failed to save model {model_id}: {e}")
        
        # Clear buffer
        self.adaptation_buffer[model_id] = []
        
        return success_count > 0
    
    def _save_online_model(self, model_id: str, model: OnlineLearningModel):
        """Save online model to disk"""
        try:
            model_path = self.models_dir / f"{model_id}_online.joblib"
            info_path = self.models_dir / f"{model_id}_online_info.json"
            
            # Save model
            dump({
                'model': model.model,
                'features': model.features,
                'is_fitted': model.is_fitted,
                'update_count': model.update_count,
                'model_type': model.model_type
            }, model_path)
            
            # Save additional info
            with open(info_path, 'w') as f:
                json.dump({
                    'model_info': model.get_model_info(),
                    'performance_history': model.get_performance_history(),
                    'last_updated': datetime.utcnow().isoformat()
                }, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save online model {model_id}: {e}")
    
    def load_online_model(self, model_id: str) -> Optional[OnlineLearningModel]:
        """Load online model from disk"""
        try:
            model_path = self.models_dir / f"{model_id}_online.joblib"
            
            if not model_path.exists():
                return None
            
            # Load model data
            model_data = load(model_path)
            
            # Recreate online model
            online_model = OnlineLearningModel(model_type=model_data['model_type'])
            online_model.model = model_data['model']
            online_model.features = model_data['features']
            online_model.is_fitted = model_data['is_fitted']
            online_model.update_count = model_data['update_count']
            
            # Load performance history if available
            info_path = self.models_dir / f"{model_id}_online_info.json"
            if info_path.exists():
                with open(info_path, 'r') as f:
                    info = json.load(f)
                    online_model.performance_history = info.get('performance_history', [])
            
            self.active_models[model_id] = online_model
            self.adaptation_buffer[model_id] = []
            
            logger.info(f"Loaded online model: {model_id}")
            return online_model
            
        except Exception as e:
            logger.error(f"Failed to load online model {model_id}: {e}")
            return None
    
    def get_model_status(self, model_id: str) -> Dict[str, Any]:
        """Get status of online model"""
        if model_id not in self.active_models:
            return {'status': 'not_found'}
        
        model = self.active_models[model_id]
        return {
            'status': 'active',
            'model_info': model.get_model_info(),
            'performance_history': model.get_performance_history()[-10:],  # Last 10 updates
            'buffer_size': len(self.adaptation_buffer.get(model_id, []))
        }
    
    def save_online_model(self, model_id: str) -> bool:
        """Public method to save online model"""
        if model_id not in self.active_models:
            return False
        
        try:
            self._save_online_model(model_id, self.active_models[model_id])
            return True
        except Exception as e:
            logger.error(f"Failed to save model {model_id}: {e}")
            return False

    def list_online_models(self) -> List[str]:
        """List all available online models"""
        return list(self.active_models.keys())
    
    def get_all_models_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all online models"""
        return {model_id: self.get_model_status(model_id) for model_id in self.active_models}
    
    def force_process_all_buffers(self):
        """Force process all adaptation buffers (for debugging)"""
        for model_id in self.active_models:
            if self.adaptation_buffer.get(model_id):
                logger.info(f"Force processing buffer for {model_id}")
                self._process_adaptation_buffer(model_id)
        return list(self.active_models.keys())


# Global online learning manager
online_manager = OnlineLearningManager()