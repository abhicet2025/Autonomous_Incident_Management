"""
Decision Agent — Routes incidents to AUTO remediation or HITL review.
Combines ML Scorer confidence with LLM confidence to make routing decisions.
CRITICAL incidents always require human approval regardless of confidence.
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import time
from datetime import datetime, timezone

ROUTING_RULES = {
    "CRITICAL": {"default": "HITL", "high_confidence": "HITL",
                 "reason": "CRITICAL severity always requires human approval"},
    "HIGH":     {"default": "HITL", "high_confidence": "AUTO",
                 "reason": "HIGH severity routed based on confidence score"},
    "MEDIUM":   {"default": "AUTO", "high_confidence": "AUTO",
                 "reason": "MEDIUM severity auto-remediated"},
    "LOW":      {"default": "AUTO", "high_confidence": "AUTO",
                 "reason": "LOW severity always auto-remediated"},
    "NONE":     {"default": "AUTO", "high_confidence": "AUTO",
                 "reason": "No anomaly detected"},
}

CONFIDENCE_THRESHOLD = 85

RISK_MAP = {
    "CRITICAL": "HIGH", "HIGH": "MEDIUM",
    "MEDIUM": "LOW", "LOW": "LOW", "NONE": "LOW",
}

AUTO_ACTION_MAP = {
    "CPU_SPIKE":          "restart_high_cpu_processes",
    "DB_CONN_EXHAUSTION": "restart_connection_pool",
    "MEMORY_LEAK":        "restart_service_instance",
    "DISK_FULL":          "cleanup_old_logs",
    "SERVICE_CRASH":      "rollback_deployment",
}

class DecisionAgent:
    def route(self, rca_result):
        start       = time.time()
        severity    = rca_result.get("severity", "UNKNOWN")
        incident_id = rca_result.get("incident_id", "UNKNOWN")
        service     = rca_result.get("service", "unknown")

        ml_confidence  = rca_result.get("raw_data", {}).get("confidence", 0) * 100
        llm_confidence = rca_result.get("confidence", 0)
        confidence     = round((ml_confidence + llm_confidence) / 2, 1)

        rules        = ROUTING_RULES.get(severity, ROUTING_RULES["MEDIUM"])
        is_high_conf = confidence >= CONFIDENCE_THRESHOLD
        decision     = rules["high_confidence"] if is_high_conf else rules["default"]
        reason       = rules["reason"]
        risk_level   = RISK_MAP.get(severity, "MEDIUM")
        latency_ms   = round((time.time() - start) * 1000, 2)

        incident_type = (rca_result.get("rca_result", {}) or {}).get("incident_type", "UNKNOWN")

        auto_action = None
        if decision == "AUTO":
            recommendations = rca_result.get("recommendations", [])
            auto_action = {
                "action":        AUTO_ACTION_MAP.get(incident_type, "manual_investigation"),
                "description":   recommendations[0] if recommendations else "Follow runbook",
                "incident_type": incident_type,
            }

        print(f"  [DecisionAgent] {incident_id} → {decision} | Confidence: {confidence}% | Risk: {risk_level}")

        return {
            "incident_id":     incident_id,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "decision":        decision,
            "reason":          reason,
            "risk_level":      risk_level,
            "auto_action":     auto_action,
            "service":         service,
            "severity":        severity,
            "root_cause":      rca_result.get("root_cause"),
            "recommendations": rca_result.get("recommendations", []),
            "confidence":      confidence,
            "resolution_time": rca_result.get("resolution_time"),
            "metrics":         rca_result.get("metrics"),
            "logs":            rca_result.get("logs"),
            "latency_ms":      latency_ms,
            "rca_result":      rca_result,
            "factors": {
                "severity":             severity,
                "ml_confidence":        ml_confidence,
                "llm_confidence":       llm_confidence,
                "combined_confidence":  confidence,
                "confidence_threshold": CONFIDENCE_THRESHOLD,
                "risk_level":           risk_level,
            },
        }
