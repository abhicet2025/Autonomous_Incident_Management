"""
Remediation Agent — Executes incident remediation playbooks.
Each incident type has a predefined playbook of sequential actions.
In production, these would execute real API calls to Kubernetes,
cloud providers, and database management systems.
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import time
from datetime import datetime, timezone

PLAYBOOKS = {
    "CPU_SPIKE": [
        {"action": "identify_runaway_process",  "description": "Identify top CPU consuming process",     "duration": 1.0},
        {"action": "kill_runaway_process",       "description": "Terminate runaway process (PID: auto)",  "duration": 1.5},
        {"action": "enable_circuit_breaker",     "description": "Enable circuit breaker to shed traffic", "duration": 0.5},
        {"action": "scale_compute_nodes",        "description": "Scale compute nodes from 2 to 4",        "duration": 2.0},
        {"action": "verify_cpu_normalized",      "description": "Verify CPU returns below 70%",           "duration": 1.0},
    ],
    "DB_CONN_EXHAUSTION": [
        {"action": "kill_long_running_queries",  "description": "Kill queries running > 30 seconds",      "duration": 1.5},
        {"action": "restart_connection_pool",    "description": "Restart database connection pool",       "duration": 2.0},
        {"action": "scale_db_replicas",          "description": "Scale DB replicas from 1 to 3",          "duration": 2.5},
        {"action": "increase_pool_size",         "description": "Increase connection pool size to 150",   "duration": 0.5},
        {"action": "enable_query_cache",         "description": "Enable database query cache",            "duration": 0.5},
        {"action": "verify_connections_normal",  "description": "Verify connection count normalized",     "duration": 1.0},
    ],
    "MEMORY_LEAK": [
        {"action": "capture_heap_dump",          "description": "Capture heap dump for analysis",         "duration": 2.0},
        {"action": "restart_service_instance",   "description": "Restart affected service instance",      "duration": 2.0},
        {"action": "increase_heap_size",         "description": "Increase JVM heap size to 4GB",          "duration": 0.5},
        {"action": "enable_gc_logging",          "description": "Enable GC logging for monitoring",       "duration": 0.5},
        {"action": "schedule_rolling_restart",   "description": "Schedule rolling restart every 6 hours", "duration": 0.5},
        {"action": "verify_memory_normalized",   "description": "Verify memory usage below 75%",          "duration": 1.0},
    ],
    "DISK_FULL": [
        {"action": "delete_old_logs",            "description": "Delete log files older than 7 days",     "duration": 2.0},
        {"action": "clear_temp_files",           "description": "Clear /tmp and /var/tmp directories",    "duration": 1.0},
        {"action": "archive_old_data",           "description": "Archive old data to cold storage",       "duration": 2.5},
        {"action": "clear_docker_images",        "description": "Remove unused Docker images",            "duration": 1.5},
        {"action": "setup_log_rotation",         "description": "Configure log rotation policy",          "duration": 0.5},
        {"action": "verify_disk_normalized",     "description": "Verify disk usage below 70%",            "duration": 1.0},
    ],
    "SERVICE_CRASH": [
        {"action": "check_exit_code",            "description": "Analyze container exit code",            "duration": 0.5},
        {"action": "review_crash_logs",          "description": "Review last 50 lines of crash logs",     "duration": 1.0},
        {"action": "rollback_deployment",        "description": "Rollback to last stable deployment",     "duration": 3.0},
        {"action": "verify_health_check",        "description": "Verify health check returning 200",      "duration": 1.5},
        {"action": "notify_dependent_services",  "description": "Notify downstream services of recovery", "duration": 0.5},
        {"action": "verify_service_stable",      "description": "Verify service stable for 2 minutes",    "duration": 2.0},
    ],
}

class RemediationAgent:
    def execute(self, decision_result):
        incident_id   = decision_result.get("incident_id", "UNKNOWN")
        incident_type = (decision_result.get("rca_result", {}) or {}).get("incident_type", "UNKNOWN")
        service       = decision_result.get("service", "unknown")
        decision      = decision_result.get("decision", "UNKNOWN")

        if decision not in ["AUTO", "APPROVED"]:
            return {"incident_id": incident_id, "status": "SKIPPED",
                    "reason": f"Decision was {decision}", "actions": []}

        playbook = PLAYBOOKS.get(incident_type)
        if not playbook:
            return {"incident_id": incident_id, "status": "NO_PLAYBOOK",
                    "reason": f"No playbook for: {incident_type}", "actions": []}

        print(f"  [RemediationAgent] Executing {incident_type} playbook ({len(playbook)} steps)")
        start         = time.time()
        actions_taken = []

        for i, step in enumerate(playbook, 1):
            step_start = time.time()
            print(f"  [RemediationAgent] Step {i}/{len(playbook)}: {step['description']}")
            time.sleep(step["duration"])
            actions_taken.append({
                "step":         i,
                "action":       step["action"],
                "description":  step["description"],
                "status":       "SUCCESS",
                "duration_sec": round(time.time() - step_start, 2),
            })

        total_time = round(time.time() - start, 2)
        print(f"  [RemediationAgent] Complete — {len(playbook)} steps in {total_time}s")

        return {
            "incident_id":    incident_id,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "incident_type":  incident_type,
            "service":        service,
            "status":         "SUCCESS",
            "total_steps":    len(playbook),
            "actions_taken":  actions_taken,
            "total_time_sec": total_time,
            "decision":       decision,
        }
