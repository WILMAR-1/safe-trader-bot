"""
Motor de Machine Learning propio (nuestro FreqAI).

Pipeline completo:
1. Feature Engineering - Crear features expandidos desde indicadores base
2. Limpieza de datos - NaN, outliers, normalizacion, PCA
3. Training - Entrenar modelo predictivo (LightGBM por defecto)
4. Inferencing - Hacer predicciones en tiempo real
5. Retraining adaptativo - Re-entrenar periodicamente en hilo separado

Basado en el mismo concepto de FreqAI pero 100% nuestro codigo.
"""

import os
import logging
import threading
import time
import pickle
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from sklearn.preprocessing import RobustScaler
from sklearn.decomposition import PCA
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, mean_squared_error

logger = logging.getLogger(__name__)

MODEL_DIR = Path("data/models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)


class FeatureEngineer:
    """
    Paso 1: Crear features expandidos.
    Toma indicadores base y genera miles de features adicionales.
    """

    @staticmethod
    def expand_features(df: pd.DataFrame) -> pd.DataFrame:
        """
        Expandir features con multiples periodos y lookbacks.
        De ~20 indicadores base genera 200+ features.
        """
        expanded = df.copy()
        base_cols = ["close", "open", "high", "low", "volume",
                     "rsi", "macd", "macd_hist", "adx", "atr_pct",
                     "bb_width", "vol_ratio", "stoch_k", "stoch_d"]

        for col in base_cols:
            if col not in expanded.columns:
                continue

            # Cambio porcentual en diferentes periodos
            for period in [1, 3, 5, 10, 20]:
                expanded[f"{col}_pct_{period}"] = expanded[col].pct_change(period)

            # Media movil de diferentes periodos
            for period in [5, 10, 20, 50]:
                expanded[f"{col}_sma_{period}"] = expanded[col].rolling(period).mean()

            # Desviacion estandar (volatilidad)
            for period in [5, 10, 20]:
                expanded[f"{col}_std_{period}"] = expanded[col].rolling(period).std()

            # Min/Max relativo
            for period in [10, 20]:
                rolling_min = expanded[col].rolling(period).min()
                rolling_max = expanded[col].rolling(period).max()
                range_val = rolling_max - rolling_min
                expanded[f"{col}_minmax_{period}"] = np.where(
                    range_val != 0,
                    (expanded[col] - rolling_min) / range_val,
                    0.5
                )

        # Features de velas
        expanded["candle_body"] = (expanded["close"] - expanded["open"]) / expanded["open"]
        expanded["candle_upper_shadow"] = (expanded["high"] - expanded[["open", "close"]].max(axis=1)) / expanded["open"]
        expanded["candle_lower_shadow"] = (expanded[["open", "close"]].min(axis=1) - expanded["low"]) / expanded["open"]

        # Distancia del precio a EMAs
        for ema in ["ema_9", "ema_21", "ema_50", "ema_100", "ema_200"]:
            if ema in expanded.columns:
                expanded[f"dist_{ema}"] = (expanded["close"] - expanded[ema]) / expanded[ema]

        # Distancia a Bollinger Bands
        if "bb_upper" in expanded.columns and "bb_lower" in expanded.columns:
            bb_range = expanded["bb_upper"] - expanded["bb_lower"]
            expanded["bb_position"] = np.where(
                bb_range != 0,
                (expanded["close"] - expanded["bb_lower"]) / bb_range,
                0.5
            )

        # Momentum cruzado
        if "rsi" in expanded.columns and "adx" in expanded.columns:
            expanded["rsi_x_adx"] = expanded["rsi"] * expanded["adx"] / 100

        # Hora del dia y dia de la semana (si hay timestamp)
        if "timestamp" in expanded.columns:
            expanded["hour"] = expanded["timestamp"].dt.hour
            expanded["day_of_week"] = expanded["timestamp"].dt.dayofweek

        return expanded

    @staticmethod
    def create_labels(df: pd.DataFrame, lookahead: int = 6, threshold: float = 0.01) -> pd.Series:
        """
        Crear etiquetas (targets) para entrenar el modelo.
        Mira N velas al futuro para determinar si el precio sube o baja.

        Labels:
          1 = Precio sube > threshold% en las proximas N velas (COMPRAR)
          0 = Precio se mantiene o baja (NO COMPRAR)
        """
        future_max = df["close"].rolling(window=lookahead).max().shift(-lookahead)
        future_return = (future_max - df["close"]) / df["close"]
        labels = (future_return > threshold).astype(int)
        return labels


class DataCleaner:
    """
    Paso 2: Limpiar datos antes de entrenar.
    """

    def __init__(self, use_pca: bool = False, pca_components: int = 50):
        self.scaler = RobustScaler()
        self.pca = PCA(n_components=pca_components) if use_pca else None
        self.feature_columns = []
        self.is_fitted = False

    def fit_transform(self, X: pd.DataFrame) -> np.ndarray:
        """Ajustar y transformar datos de entrenamiento."""
        # Eliminar columnas no numericas y constantes
        numeric_cols = X.select_dtypes(include=[np.number]).columns.tolist()
        X_clean = X[numeric_cols].copy()

        # Eliminar columnas con demasiados NaN (>30%)
        nan_ratio = X_clean.isna().mean()
        valid_cols = nan_ratio[nan_ratio < 0.3].index.tolist()
        X_clean = X_clean[valid_cols]

        # Eliminar columnas constantes
        non_const = X_clean.std() > 1e-8
        X_clean = X_clean.loc[:, non_const]

        self.feature_columns = X_clean.columns.tolist()

        # Rellenar NaN restantes
        X_clean = X_clean.fillna(method="ffill").fillna(0)

        # Eliminar outliers (IQR method)
        Q1 = X_clean.quantile(0.01)
        Q3 = X_clean.quantile(0.99)
        X_clean = X_clean.clip(lower=Q1, upper=Q3, axis=1)

        # Normalizar
        X_scaled = self.scaler.fit_transform(X_clean)

        # PCA (reduccion dimensional)
        if self.pca and X_scaled.shape[1] > self.pca.n_components:
            X_scaled = self.pca.fit_transform(X_scaled)

        self.is_fitted = True
        return X_scaled

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        """Transformar datos nuevos usando el ajuste existente."""
        if not self.is_fitted:
            raise ValueError("DataCleaner no ha sido ajustado. Llama fit_transform primero.")

        X_clean = X[self.feature_columns].copy()
        X_clean = X_clean.fillna(method="ffill").fillna(0)

        Q1 = X_clean.quantile(0.01)
        Q3 = X_clean.quantile(0.99)
        X_clean = X_clean.clip(lower=Q1, upper=Q3, axis=1)

        X_scaled = self.scaler.transform(X_clean)

        if self.pca:
            X_scaled = self.pca.transform(X_scaled)

        return X_scaled


class MLModel:
    """
    Paso 3 y 4: Entrenar y predecir.
    Usa LightGBM (rapido y preciso) o RandomForest como fallback.
    """

    def __init__(self, model_type: str = "lightgbm"):
        self.model_type = model_type
        self.model = None
        self.trained = False
        self.train_accuracy = 0
        self.test_accuracy = 0
        self.train_time = None
        self.feature_importance = None

    def train(self, X_train: np.ndarray, y_train: np.ndarray,
              X_test: np.ndarray, y_test: np.ndarray):
        """Entrenar el modelo."""
        start = time.time()

        if self.model_type == "lightgbm":
            try:
                import lightgbm as lgb
                self.model = lgb.LGBMClassifier(
                    n_estimators=500,
                    max_depth=6,
                    learning_rate=0.05,
                    num_leaves=31,
                    min_child_samples=20,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    reg_alpha=0.1,
                    reg_lambda=0.1,
                    random_state=42,
                    verbose=-1,
                    n_jobs=-1,
                )
            except ImportError:
                logger.warning("LightGBM no disponible, usando RandomForest")
                self.model_type = "randomforest"

        if self.model_type == "randomforest":
            from sklearn.ensemble import RandomForestClassifier
            self.model = RandomForestClassifier(
                n_estimators=300,
                max_depth=8,
                min_samples_split=10,
                min_samples_leaf=5,
                random_state=42,
                n_jobs=-1,
            )

        if self.model_type == "gradient_boosting":
            from sklearn.ensemble import GradientBoostingClassifier
            self.model = GradientBoostingClassifier(
                n_estimators=300,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.8,
                random_state=42,
            )

        self.model.fit(X_train, y_train)

        self.train_accuracy = accuracy_score(y_train, self.model.predict(X_train))
        self.test_accuracy = accuracy_score(y_test, self.model.predict(X_test))
        self.train_time = time.time() - start
        self.trained = True

        # Feature importance
        if hasattr(self.model, "feature_importances_"):
            self.feature_importance = self.model.feature_importances_

        logger.info(
            "Modelo entrenado en %.1fs | Train acc: %.2f%% | Test acc: %.2f%%",
            self.train_time, self.train_accuracy * 100, self.test_accuracy * 100
        )

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predecir (0 o 1)."""
        if not self.trained:
            raise ValueError("Modelo no entrenado")
        return self.model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Predecir con probabilidad (0.0 a 1.0)."""
        if not self.trained:
            raise ValueError("Modelo no entrenado")
        return self.model.predict_proba(X)[:, 1]  # Probabilidad de clase 1

    def save(self, filepath: str):
        """Guardar modelo a disco."""
        with open(filepath, "wb") as f:
            pickle.dump({
                "model": self.model,
                "model_type": self.model_type,
                "train_accuracy": self.train_accuracy,
                "test_accuracy": self.test_accuracy,
                "train_time": self.train_time,
            }, f)
        logger.info("Modelo guardado en %s", filepath)

    def load(self, filepath: str):
        """Cargar modelo desde disco."""
        with open(filepath, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.model_type = data["model_type"]
        self.train_accuracy = data["train_accuracy"]
        self.test_accuracy = data["test_accuracy"]
        self.train_time = data["train_time"]
        self.trained = True
        logger.info("Modelo cargado desde %s (acc: %.2f%%)", filepath, self.test_accuracy * 100)


class MLEngine:
    """
    Paso 5: Motor completo con retraining adaptativo.
    Coordina todo el pipeline: features -> limpieza -> training -> prediccion.
    """

    def __init__(self, model_type: str = "randomforest", retrain_hours: int = 24,
                 lookahead: int = 6, threshold: float = 0.01,
                 min_confidence: float = 0.65):
        self.model_type = model_type
        self.retrain_hours = retrain_hours
        self.lookahead = lookahead
        self.threshold = threshold
        self.min_confidence = min_confidence

        self.feature_engineer = FeatureEngineer()
        self.cleaner = DataCleaner(use_pca=False)
        self.model = MLModel(model_type)

        self.last_train_time = None
        self.is_ready = False
        self._retrain_thread = None
        self._lock = threading.Lock()

        # Stats
        self.predictions_made = 0
        self.correct_predictions = 0

    def train(self, df: pd.DataFrame, symbol: str = "") -> dict:
        """
        Entrenar el modelo con datos historicos.

        Args:
            df: DataFrame con indicadores ya calculados
            symbol: Nombre del par (para guardar modelo)

        Returns:
            dict con metricas de entrenamiento
        """
        logger.info("Entrenando modelo ML para %s (%d velas)...", symbol, len(df))

        # 1. Feature Engineering
        expanded = self.feature_engineer.expand_features(df)

        # 2. Crear labels
        labels = self.feature_engineer.create_labels(expanded, self.lookahead, self.threshold)

        # 3. Eliminar filas sin label (ultimas N velas)
        valid_mask = labels.notna()
        expanded = expanded[valid_mask]
        labels = labels[valid_mask]

        if len(expanded) < 100:
            logger.warning("Datos insuficientes para entrenar (%d filas)", len(expanded))
            return {"error": "Datos insuficientes"}

        # 4. Separar features de no-features
        exclude_cols = ["timestamp", "open", "high", "low", "close", "volume"]
        feature_cols = [c for c in expanded.columns if c not in exclude_cols]
        X = expanded[feature_cols]

        # 5. Limpiar y normalizar
        with self._lock:
            X_clean = self.cleaner.fit_transform(X)

        y = labels.values

        # 6. Split train/test (80/20, sin shuffle para respetar temporalidad)
        split_idx = int(len(X_clean) * 0.8)
        X_train, X_test = X_clean[:split_idx], X_clean[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        # Balance check
        buy_pct = y_train.mean() * 100
        logger.info("Distribucion labels - Buy: %.1f%% | No buy: %.1f%%", buy_pct, 100 - buy_pct)

        # 7. Entrenar
        with self._lock:
            self.model.train(X_train, y_train, X_test, y_test)

        self.last_train_time = datetime.now()
        self.is_ready = True

        # 8. Guardar modelo
        safe_symbol = symbol.replace("/", "_") if symbol else "general"
        model_path = MODEL_DIR / f"model_{safe_symbol}.pkl"
        cleaner_path = MODEL_DIR / f"cleaner_{safe_symbol}.pkl"
        self.model.save(str(model_path))
        with open(cleaner_path, "wb") as f:
            pickle.dump(self.cleaner, f)

        result = {
            "symbol": symbol,
            "train_accuracy": round(self.model.train_accuracy * 100, 2),
            "test_accuracy": round(self.model.test_accuracy * 100, 2),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "features": len(self.cleaner.feature_columns),
            "buy_signal_ratio": round(buy_pct, 1),
            "train_time": round(self.model.train_time, 2),
        }

        logger.info("Entrenamiento completado: %s", result)
        return result

    def predict(self, df: pd.DataFrame) -> dict:
        """
        Hacer prediccion sobre la vela actual.

        Returns:
            dict con:
              - should_buy: bool
              - confidence: float (0-1)
              - prediction: int (0 o 1)
        """
        if not self.is_ready:
            return {"should_buy": False, "confidence": 0, "prediction": 0, "reason": "Modelo no entrenado"}

        try:
            # Feature engineering
            expanded = self.feature_engineer.expand_features(df)

            # Tomar solo la ultima fila
            exclude_cols = ["timestamp", "open", "high", "low", "close", "volume"]
            feature_cols = [c for c in expanded.columns if c not in exclude_cols]
            X = expanded[feature_cols].iloc[[-1]]

            # Limpiar y predecir
            with self._lock:
                X_clean = self.cleaner.transform(X)
                confidence = self.model.predict_proba(X_clean)[0]
                prediction = int(confidence >= self.min_confidence)

            self.predictions_made += 1

            return {
                "should_buy": prediction == 1,
                "confidence": round(float(confidence), 4),
                "prediction": prediction,
                "min_confidence": self.min_confidence,
                "reason": f"ML confidence: {confidence:.1%}",
            }

        except Exception as e:
            logger.error("Error en prediccion ML: %s", e)
            return {"should_buy": False, "confidence": 0, "prediction": 0, "reason": str(e)}

    def needs_retrain(self) -> bool:
        """Verificar si el modelo necesita re-entrenamiento."""
        if not self.last_train_time:
            return True
        hours_since = (datetime.now() - self.last_train_time).total_seconds() / 3600
        return hours_since >= self.retrain_hours

    def start_background_retrain(self, get_data_fn, symbol: str):
        """Iniciar re-entrenamiento en hilo separado."""
        if self._retrain_thread and self._retrain_thread.is_alive():
            logger.debug("Re-entrenamiento ya en progreso")
            return

        def retrain():
            try:
                logger.info("Iniciando re-entrenamiento adaptativo para %s...", symbol)
                df = get_data_fn(symbol)
                if df is not None and len(df) > 200:
                    result = self.train(df, symbol)
                    logger.info("Re-entrenamiento completado: acc=%.1f%%", result.get("test_accuracy", 0))
            except Exception as e:
                logger.error("Error en re-entrenamiento: %s", e)

        self._retrain_thread = threading.Thread(target=retrain, daemon=True)
        self._retrain_thread.start()

    def load_model(self, symbol: str) -> bool:
        """Cargar modelo guardado desde disco."""
        safe_symbol = symbol.replace("/", "_") if symbol else "general"
        model_path = MODEL_DIR / f"model_{safe_symbol}.pkl"
        cleaner_path = MODEL_DIR / f"cleaner_{safe_symbol}.pkl"

        if not model_path.exists() or not cleaner_path.exists():
            return False

        try:
            self.model.load(str(model_path))
            with open(cleaner_path, "rb") as f:
                self.cleaner = pickle.load(f)
            self.is_ready = True
            self.last_train_time = datetime.fromtimestamp(model_path.stat().st_mtime)
            return True
        except Exception as e:
            logger.error("Error cargando modelo: %s", e)
            return False

    def get_stats(self) -> dict:
        return {
            "is_ready": self.is_ready,
            "model_type": self.model_type,
            "train_accuracy": round(self.model.train_accuracy * 100, 2) if self.model.trained else 0,
            "test_accuracy": round(self.model.test_accuracy * 100, 2) if self.model.trained else 0,
            "last_train": self.last_train_time.isoformat() if self.last_train_time else None,
            "predictions_made": self.predictions_made,
            "min_confidence": self.min_confidence,
            "needs_retrain": self.needs_retrain(),
        }
