"""
ITSM Agent — IT Service Management ticket lifecycle management.
Automatically creates, updates, and closes incident tickets with
full audit trail. Simulates ServiceNow/Jira integration.
In production, replace file operations with ITSM REST API calls.
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import json
from datetime import datetime, timezone

TICKETS_PATH = '/workspace/shared/incident_agent/data/tickets'

PRIORITY_MAP = {
    "CRITICAL": {"priority": "P1", "sla_minutes": 30,   "team": "Platform-SRE"},
    "HIGH":     {"priority": "P2", "sla_minutes": 120,  "team": "App-SRE"},
    "MEDIUM":   {"priority": "P3", "sla_minutes": 480,  "team": "DevOps"},
    "LOW":      {"priority": "P4", "sla_minutes": 1440, "team": "DevOps"},
    "NONE":     {"priority": "P5", "sla_minutes": 2880, "team": "DevOps"},
}

SERVICE_TEAM_MAP = {
    "database-service":  "DBA-Team",
    "compute-service":   "Platform-Team",
    "app-service":       "App-Team",
    "storage-service":   "Storage-Team",
    "orchestration":     "Platform-Team",
    "all-services":      "SRE-Team",
}

class ITSMAgent:
    def __init__(self):
        os.makedirs(TICKETS_PATH, exist_ok=True)

    def _get_ticket_path(self, incident_id):
        return os.path.join(TICKETS_PATH, f"{incident_id}.json")

    def create_ticket(self, decision_result):
        incident_id     = decision_result.get("incident_id", "UNKNOWN")
        severity        = decision_result.get("severity", "MEDIUM")
        service         = decision_result.get("service", "unknown")
        root_cause      = decision_result.get("root_cause", "Under investigation")
        confidence      = decision_result.get("confidence", 0)
        decision        = decision_result.get("decision", "UNKNOWN")
        metrics         = decision_result.get("metrics", {})
        recommendations = decision_result.get("recommendations", [])

        priority_info = PRIORITY_MAP.get(severity, PRIORITY_MAP["MEDIUM"])
        assigned_team = SERVICE_TEAM_MAP.get(service, "SRE-Team")
        now           = datetime.now(timezone.utc)

        ticket = {
            "ticket_id":       incident_id,
            "created_at":      now.isoformat(),
            "updated_at":      now.isoformat(),
            "title":           f"{severity} incident on {service}",
            "description":     root_cause,
            "priority":        priority_info["priority"],
            "severity":        severity,
            "status":          "OPEN",
            "service":         service,
            "assigned_team":   assigned_team,
            "sla_minutes":     priority_info["sla_minutes"],
            "ai_confidence":   confidence,
            "decision":        decision,
            "metrics":         metrics,
            "recommendations": recommendations,
            "timeline": [{
                "timestamp": now.isoformat(),
                "event":     "TICKET_CREATED",
                "details":   f"Incident detected on {service}",
                "actor":     "AutoDetection",
            }],
            "resolution":   None,
            "closed_at":    None,
            "mttr_minutes": None,
        }

        with open(self._get_ticket_path(incident_id), 'w') as f:
            json.dump(ticket, f, indent=2)

        print(f"  [ITSMAgent] Ticket created: {incident_id} | {priority_info['priority']} | {assigned_team}")
        return ticket

    def update_ticket(self, incident_id, status, details, actor="System"):
        ticket_path = self._get_ticket_path(incident_id)
        if not os.path.exists(ticket_path):
            return None
        with open(ticket_path, 'r') as f:
            ticket = json.load(f)
        now = datetime.now(timezone.utc).isoformat()
        ticket["status"]     = status
        ticket["updated_at"] = now
        ticket["timeline"].append({
            "timestamp": now,
            "event":     f"STATUS_CHANGED_TO_{status}",
            "details":   details,
            "actor":     actor,
        })
        with open(ticket_path, 'w') as f:
            json.dump(ticket, f, indent=2)
        return ticket

    def close_ticket(self, incident_id, resolution, remediation_result):
        ticket_path = self._get_ticket_path(incident_id)
        if not os.path.exists(ticket_path):
            return None
        with open(ticket_path, 'r') as f:
            ticket = json.load(f)
        now     = datetime.now(timezone.utc)
        created = datetime.fromisoformat(ticket["created_at"])
        mttr    = round((now - created).total_seconds() / 60, 2)
        ticket["status"]       = "RESOLVED"
        ticket["closed_at"]    = now.isoformat()
        ticket["updated_at"]   = now.isoformat()
        ticket["resolution"]   = resolution
        ticket["mttr_minutes"] = mttr
        ticket["timeline"].append({
            "timestamp":    now.isoformat(),
            "event":        "TICKET_RESOLVED",
            "details":      resolution,
            "actor":        "AutoRemediation",
            "mttr_minutes": mttr,
        })
        with open(ticket_path, 'w') as f:
            json.dump(ticket, f, indent=2)
        print(f"  [ITSMAgent] Ticket closed: {incident_id} | MTTR: {mttr} mins")
        return ticket
