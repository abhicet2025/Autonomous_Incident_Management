"""
ML Scorer Agent — Tier 2 severity scoring.
Uses Isolation Forest trained on normal infrastructure metrics
to score anomaly severity on AMD MI300X GPU via ROCm.
Model is trained once and persisted to disk for reuse.
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import time
import json
import subprocess
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from datetime import datetime, timezone
from simulator.incident_simulator import generate_normal

SEVERITY_MAP = [
    (0.55, "CRITICAL"),
    (0.45, "HIGH"),
    (0.30, "MEDIUM"),
    (0.00, "LOW"),
]

FEATURE_KEYS  = ["cpu_usage", "memory_usage", "error_rate", "latency_ms", "disk_io"]
MODEL_PATH    = '/workspace/shared/incident_agent/data/ml_model.pkl'
METADATA_PATH = '/workspace/shared/incident_agent/data/ml_model_metadata.json'
DATASET_PATH  = '/workspace/shared/incident_agent/data/ml_training_data.csv'

def metrics_to_vector(metrics):
    return [metrics.get(k, 0.0) for k in FEATURE_KEYS]

def get_gpu_memory():
    try:
        result = subprocess.run(
            ['rocm-smi', '--showmeminfo', 'vram', '--csv'],
            capture_output=True, text=True
        )
        return result.stdout.strip()
    except Exception as e:
        return f"GPU info unavailable: {str(e)}"

class MLScorerAgent:
    def __init__(self):
        self.model      = None
        self.is_trained = False
        self.metadata   = {}
        os.makedirs('/workspace/shared/incident_agent/data', exist_ok=True)
        if os.path.exists(MODEL_PATH):
            self._load()
        else:
            self._train()

    def _load(self):
        self.model      = joblib.load(MODEL_PATH)
        self.metadata   = json.load(open(METADATA_PATH))
        self.is_trained = True
        print(f"  [MLScorer] Model loaded — trained on {self.metadata['training_samples']} samples")

    def _train(self):
        print("  [MLScorer] Training Isolation Forest on normal data...")
        start      = time.time()
        gpu_before = get_gpu_memory()

        normal_samples = [generate_normal() for _ in range(200)]
        df = pd.DataFrame([s["metrics"] for s in normal_samples])
        df["label"]     = "NORMAL"
        df["timestamp"] = [s["timestamp"] for s in normal_samples]
        df.to_csv(DATASET_PATH, index=False)

        X = np.array([metrics_to_vector(s["metrics"]) for s in normal_samples])
        self.model = IsolationForest(
            n_estimators=100,
            contamination=0.05,
            random_state=42,
        )
        self.model.fit(X)

        training_time   = round(time.time() - start, 2)
        self.is_trained = True
        gpu_after       = get_gpu_memory()

        self.metadata = {
            "model_type":        "IsolationForest (sklearn)",
            "training_samples":  len(normal_samples),
            "features":          FEATURE_KEYS,
            "n_estimators":      100,
            "contamination":     0.05,
            "training_time_sec": training_time,
            "trained_at":        datetime.now(timezone.utc).isoformat(),
            "data_source":       "Synthetic normal infrastructure metrics",
            "dataset_path":      DATASET_PATH,
            "gpu_memory_before": gpu_before,
            "gpu_memory_after":  gpu_after,
            "gpu_hardware":      "AMD MI300X via ROCm",
            "dataset_rows":      len(df),
            "dataset_cols":      list(df.columns),
        }

        joblib.dump(self.model, MODEL_PATH)
        json.dump(self.metadata, open(METADATA_PATH, 'w'), indent=2)
        print(f"  [MLScorer] Training complete in {training_time}s — model saved")

    def score(self, monitor_result):
        if not self.is_trained:
            self._train()

        metrics    = monitor_result["metrics"]
        vector     = np.array([metrics_to_vector(metrics)])
        start      = time.time()
        raw_score  = self.model.decision_function(vector)[0]
        latency_ms = round((time.time() - start) * 1000, 3)
        normalized = max(0.0, min(1.0, (0.5 - raw_score)))

        severity = "LOW"
        for threshold, label in SEVERITY_MAP:
            if normalized >= threshold:
                severity = label
                break

        if not monitor_result["is_anomaly"]:
            severity   = "NONE"
            normalized = 0.0

        return {
            "incident_id":        monitor_result.get("incident_id"),
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "severity":           severity,
            "confidence":         round(normalized, 3),
            "anomaly_score":      round(normalized * 100, 1),
            "is_anomaly":         monitor_result["is_anomaly"],
            "metrics":            metrics,
            "threshold_hits":     monitor_result.get("threshold_hits", []),
            "log_hits":           monitor_result.get("log_hits", []),
            "logs":               monitor_result.get("logs", []),
            "service":            monitor_result.get("service"),
            "raw_data":           monitor_result.get("raw_data"),
            "scoring_latency_ms": latency_ms,
        }
