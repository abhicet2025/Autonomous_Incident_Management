"""
Feedback Agent — Closes the self-improvement loop.
Ingests incident resolution outcomes into ChromaDB as feedback documents.
Each stored feedback enriches the RAG knowledge base, improving future
RCA quality for similar incidents over time.
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import json
from datetime import datetime, timezone
from rag.chroma_store import ChromaStore

FEEDBACK_LOG_PATH = '/workspace/shared/incident_agent/data/feedback_log.json'

class FeedbackAgent:
    def __init__(self):
        self.chroma       = ChromaStore()
        self.feedback_log = []
        if os.path.exists(FEEDBACK_LOG_PATH):
            with open(FEEDBACK_LOG_PATH, 'r') as f:
                self.feedback_log = json.load(f)

    def _save_log(self):
        with open(FEEDBACK_LOG_PATH, 'w') as f:
            json.dump(self.feedback_log, f, indent=2)

    def ingest_auto_resolution(self, decision_result, remediation_result, ticket):
        incident_id   = decision_result.get("incident_id", "UNKNOWN")
        service       = decision_result.get("service", "unknown")
        root_cause    = decision_result.get("root_cause", "Unknown")
        severity      = decision_result.get("severity", "UNKNOWN")
        incident_type = (decision_result.get("rca_result", {}) or {}).get("incident_type", "UNKNOWN")
        actions       = remediation_result.get("actions_taken", [])
        mttr          = ticket.get("mttr_minutes", 0)
        fix_applied   = " → ".join([a["description"] for a in actions])

        doc_id = self.chroma.add_feedback(
            incident_id  = incident_id,
            root_cause   = root_cause,
            fix_applied  = fix_applied,
            outcome      = f"AUTO-RESOLVED in {mttr} minutes",
            human_notes  = f"Automatic remediation successful. {len(actions)} steps executed.",
            service      = service,
        )

        record = {
            "incident_id":   incident_id,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "incident_type": incident_type,
            "service":       service,
            "severity":      severity,
            "root_cause":    root_cause,
            "fix_applied":   fix_applied,
            "decision_type": "AUTO",
            "outcome":       "RESOLVED",
            "mttr_minutes":  mttr,
            "chroma_doc_id": doc_id,
        }
        self.feedback_log.append(record)
        self._save_log()
        print(f"  [FeedbackAgent] Auto-resolution stored: {doc_id} | Total: {len(self.feedback_log)}")
        return record

    def ingest_human_resolution(self, decision_result, human_decision,
                                 human_notes, actual_fix, outcome, mttr):
        incident_id   = decision_result.get("incident_id", "UNKNOWN")
        service       = decision_result.get("service", "unknown")
        root_cause    = decision_result.get("root_cause", "Unknown")
        severity      = decision_result.get("severity", "UNKNOWN")
        incident_type = (decision_result.get("rca_result", {}) or {}).get("incident_type", "UNKNOWN")

        doc_id = self.chroma.add_feedback(
            incident_id  = incident_id,
            root_cause   = root_cause,
            fix_applied  = actual_fix,
            outcome      = f"{human_decision}: {outcome}",
            human_notes  = human_notes,
            service      = service,
        )

        record = {
            "incident_id":    incident_id,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "incident_type":  incident_type,
            "service":        service,
            "severity":       severity,
            "root_cause":     root_cause,
            "fix_applied":    actual_fix,
            "decision_type":  "HUMAN",
            "human_decision": human_decision,
            "human_notes":    human_notes,
            "outcome":        outcome,
            "mttr_minutes":   mttr,
            "chroma_doc_id":  doc_id,
        }
        self.feedback_log.append(record)
        self._save_log()
        print(f"  [FeedbackAgent] Human feedback stored: {doc_id} | Total: {len(self.feedback_log)}")
        return record

    def get_statistics(self):
        if not self.feedback_log:
            return {"total": 0}
        total       = len(self.feedback_log)
        auto_count  = sum(1 for r in self.feedback_log if r["decision_type"] == "AUTO")
        human_count = sum(1 for r in self.feedback_log if r["decision_type"] == "HUMAN")
        avg_mttr    = round(sum(r.get("mttr_minutes", 0) for r in self.feedback_log) / total, 2)
        by_type = {}
        for r in self.feedback_log:
            t = r.get("incident_type", "UNKNOWN")
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "total_incidents":     total,
            "auto_resolved":       auto_count,
            "human_resolved":      human_count,
            "avg_mttr_minutes":    avg_mttr,
            "by_incident_type":    by_type,
            "knowledge_base_size": self.chroma.collection.count(),
        }
