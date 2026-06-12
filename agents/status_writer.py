"""
Status Writer — Shared utility for writing live pipeline status
to a JSON file that the Dashboard polls for real-time updates.
"""
import json
import os
from datetime import datetime, timezone

STATUS_PATH = '/workspace/shared/incident_agent/data/dashboard_status.json'
HISTORY_PATH = '/workspace/shared/incident_agent/data/dashboard_history.json'


def update_active_incident(incident_id, agent_step, data, severity=None,
                            scenario=None, service=None):
    """
    Update the live status for an active incident.
    Called after each agent completes its step.
    """
    status = load_status()

    if incident_id not in status["active"]:
        status["active"][incident_id] = {
            "incident_id": incident_id,
            "scenario":    scenario,
            "severity":    severity,
            "service":     service,
            "started_at":  datetime.now(timezone.utc).isoformat(),
            "steps":       {},
            "current_step": agent_step,
        }

    status["active"][incident_id]["steps"][agent_step] = data
    status["active"][incident_id]["current_step"] = agent_step
    status["active"][incident_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

    if severity:
        status["active"][incident_id]["severity"] = severity
    if scenario:
        status["active"][incident_id]["scenario"] = scenario
    if service:
        status["active"][incident_id]["service"] = service

    save_status(status)


def complete_incident(incident_id, final_status):
    """Move an incident from active to history when pipeline completes."""
    status = load_status()

    if incident_id in status["active"]:
        incident_data = status["active"].pop(incident_id)
        incident_data["final_status"] = final_status
        incident_data["completed_at"] = datetime.now(timezone.utc).isoformat()

        history = load_history()
        history.insert(0, incident_data)
        save_history(history[:100])

    save_status(status)


def load_status():
    if os.path.exists(STATUS_PATH):
        try:
            with open(STATUS_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"active": {}, "updated_at": datetime.now(timezone.utc).isoformat()}


def save_status(status):
    status["updated_at"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, 'w') as f:
        json.dump(status, f, indent=2, default=str)


def load_history():
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_history(history):
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, 'w') as f:
        json.dump(history, f, indent=2, default=str)
