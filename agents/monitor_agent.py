"""
Monitor Agent — Tier 1 anomaly detection.
Uses rule-based threshold checks, Z-score statistical analysis,
and log keyword scanning to identify infrastructure anomalies.
No LLM required — designed for sub-second detection at scale.
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import statistics
from datetime import datetime, timezone

THRESHOLDS = {
    "cpu_usage":    {"warn": 70,   "critical": 85},
    "memory_usage": {"warn": 75,   "critical": 90},
    "error_rate":   {"warn": 5,    "critical": 10},
    "latency_ms":   {"warn": 1000, "critical": 2000},
    "disk_io":      {"warn": 75,   "critical": 90},
}

LOG_KEYWORDS = [
    "ERROR", "CrashLoop", "exhausted", "OutOfMemory",
    "timeout", "failed", "CRITICAL", "no space",
]

class MonitorAgent:
    def __init__(self):
        self.history = []
        self.window  = 10

    def _check_thresholds(self, metrics):
        triggered = []
        for metric, value in metrics.items():
            if metric not in THRESHOLDS:
                continue
            t = THRESHOLDS[metric]
            if value >= t["critical"]:
                triggered.append({"metric": metric, "value": value,
                                   "level": "CRITICAL", "threshold": t["critical"]})
            elif value >= t["warn"]:
                triggered.append({"metric": metric, "value": value,
                                   "level": "WARN", "threshold": t["warn"]})
        return triggered

    def _check_zscore(self, metrics):
        self.history.append(metrics)
        if len(self.history) > self.window:
            self.history.pop(0)
        if len(self.history) < 3:
            return []
        flagged = []
        for metric, value in metrics.items():
            if metric not in THRESHOLDS:
                continue
            values = [h[metric] for h in self.history if metric in h]
            if len(values) < 3:
                continue
            mean  = statistics.mean(values)
            stdev = statistics.stdev(values)
            if stdev == 0:
                continue
            zscore = abs((value - mean) / stdev)
            if zscore > 2.0:
                flagged.append({"metric": metric, "value": value,
                                "zscore": round(zscore, 2), "mean": round(mean, 2)})
        return flagged

    def _check_logs(self, logs):
        hits = []
        for line in logs:
            for kw in LOG_KEYWORDS:
                if kw.lower() in line.lower():
                    hits.append({"keyword": kw, "log": line})
                    break
        return hits

    def analyze(self, data):
        metrics        = data["metrics"]
        logs           = data.get("logs", [])
        threshold_hits = self._check_thresholds(metrics)
        zscore_hits    = self._check_zscore(metrics)
        log_hits       = self._check_logs(logs)
        total_rules    = len(threshold_hits) + len(zscore_hits) + len(log_hits)
        anomaly_score  = min(100, total_rules * 20)
        is_anomaly     = total_rules > 0
        return {
            "incident_id":    data.get("incident_id"),
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "is_anomaly":     is_anomaly,
            "anomaly_score":  anomaly_score,
            "threshold_hits": threshold_hits,
            "zscore_hits":    zscore_hits,
            "log_hits":       log_hits,
            "metrics":        metrics,
            "logs":           logs,
            "service":        data.get("service"),
            "raw_data":       data,
        }
