==================================================
FILE: ./agents/decision_agent.py
==================================================
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


==================================================
FILE: ./agents/feedback_agent.py
==================================================
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


==================================================
FILE: ./agents/itsm_agent.py
==================================================
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


==================================================
FILE: ./agents/langgraph_orchestrator.py
==================================================
"""
LangGraph Orchestrator — Multi-agent pipeline coordinator.
Orchestrates the complete incident management workflow using LangGraph
for state management, checkpointing, and human-in-the-loop (HITL) support.

Pipeline: Monitor → ML Scorer → RCA → Decision → HITL/AUTO → Remediation → ITSM → Feedback
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import json
from datetime import datetime, timezone
from typing import TypedDict, Optional, Dict, Any
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver

from agents.monitor_agent import MonitorAgent
from agents.ml_scorer_agent import MLScorerAgent
from agents.rca_agent import RCAAgent
from agents.decision_agent import DecisionAgent
from agents.remediation_agent import RemediationAgent
from agents.itsm_agent import ITSMAgent
from agents.feedback_agent import FeedbackAgent
from agents.status_writer import update_active_incident, complete_incident

PIPELINE_LOG_PATH   = '/workspace/shared/incident_agent/data/pipeline_log.json'
HITL_QUEUE_PATH     = '/workspace/shared/incident_agent/data/hitl_queue.json'
HITL_DECISIONS_PATH = '/workspace/shared/incident_agent/data/hitl_decisions.json'


class PipelineState(TypedDict):
    incident:        Dict[str, Any]
    monitor_result:  Optional[Dict[str, Any]]
    scored_result:   Optional[Dict[str, Any]]
    rca_result:      Optional[Dict[str, Any]]
    decision_result: Optional[Dict[str, Any]]
    rem_result:      Optional[Dict[str, Any]]
    ticket:          Optional[Dict[str, Any]]
    feedback_result: Optional[Dict[str, Any]]
    human_decision:  Optional[str]
    human_notes:     Optional[str]
    actual_fix:      Optional[str]
    status:          str
    error:           Optional[str]
    started_at:      str
    updated_at:      str
    total_time_sec:  Optional[float]


monitor_agent     = None
scorer_agent      = None
rca_agent         = None
decision_agent    = None
remediation_agent = None
itsm_agent        = None
feedback_agent    = None


def init_agents():
    global monitor_agent, scorer_agent, rca_agent
    global decision_agent, remediation_agent, itsm_agent, feedback_agent
    print("  [Orchestrator] Initializing all agents...")
    monitor_agent     = MonitorAgent()
    scorer_agent      = MLScorerAgent()
    rca_agent         = RCAAgent()
    decision_agent    = DecisionAgent()
    remediation_agent = RemediationAgent()
    itsm_agent        = ITSMAgent()
    feedback_agent    = FeedbackAgent()
    print("  [Orchestrator] All agents ready!")


def _convert(obj):
    """Recursively convert numpy types to Python native types for LangGraph serialization."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _convert(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert(v) for v in obj]
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def monitor_node(state: PipelineState) -> PipelineState:
    try:
        result = _convert(monitor_agent.analyze(state["incident"]))
        incident = state["incident"]
        update_active_incident(
            incident["incident_id"], "monitor", result,
            scenario=incident.get("scenario_id"), service=incident.get("service"),
        )
        return {**state, "monitor_result": result, "status": "MONITORING",
                "updated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {**state, "status": "ERROR", "error": str(e)}


def ml_scorer_node(state: PipelineState) -> PipelineState:
    try:
        result = _convert(scorer_agent.score(state["monitor_result"]))
        update_active_incident(
            state["incident"]["incident_id"], "ml_scorer", result,
            severity=result.get("severity"),
        )
        return {**state, "scored_result": result, "status": "SCORED",
                "updated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {**state, "status": "ERROR", "error": str(e)}


def rca_node(state: PipelineState) -> PipelineState:
    try:
        result = _convert(rca_agent.analyze(state["scored_result"]))
        update_active_incident(state["incident"]["incident_id"], "rca", result)
        return {**state, "rca_result": result, "status": "RCA_COMPLETE",
                "updated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {**state, "status": "ERROR", "error": str(e)}


def decision_node(state: PipelineState) -> PipelineState:
    try:
        result = _convert(decision_agent.route(state["rca_result"]))
        update_active_incident(state["incident"]["incident_id"], "decision", result)
        return {**state, "decision_result": result, "status": "DECISION_MADE",
                "updated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {**state, "status": "ERROR", "error": str(e)}


def hitl_node(state: PipelineState) -> PipelineState:
    human_decision = state.get("human_decision", "PENDING")
    update_active_incident(
        state["incident"]["incident_id"], "hitl",
        {"human_decision": human_decision,
         "human_notes": state.get("human_notes", ""),
         "actual_fix": state.get("actual_fix", "")}
    )
    return {**state, "status": f"HITL_{human_decision}",
            "updated_at": datetime.now(timezone.utc).isoformat()}


def remediation_node(state: PipelineState) -> PipelineState:
    try:
        decision_result = state["decision_result"]
        human_decision  = state.get("human_decision")
        if human_decision == "REJECTED":
            return {**state, "rem_result": {"status": "REJECTED", "actions_taken": []},
                    "status": "REJECTED", "updated_at": datetime.now(timezone.utc).isoformat()}
        exec_decision = {
            **decision_result,
            "decision": "APPROVED" if human_decision == "APPROVED" else decision_result["decision"],
        }
        result = _convert(remediation_agent.execute(exec_decision))
        update_active_incident(state["incident"]["incident_id"], "remediation", result)
        return {**state, "rem_result": result, "status": "REMEDIATED",
                "updated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {**state, "status": "ERROR", "error": str(e)}


def itsm_node(state: PipelineState) -> PipelineState:
    try:
        decision_result = state["decision_result"]
        rem_result      = state.get("rem_result", {})
        incident_id     = state["incident"]["incident_id"]
        rca_result      = state["rca_result"]
        ticket          = itsm_agent.create_ticket(decision_result)
        itsm_agent.update_ticket(incident_id, "IN_PROGRESS",
                                 f"RCA: {rca_result['root_cause']}", "RCAAgent")
        resolution = f"Remediated: {rem_result.get('total_steps', 0)} steps"
        if state.get("human_decision"):
            resolution = f"Human {state['human_decision']}: {state.get('actual_fix', '')}"
        closed = itsm_agent.close_ticket(incident_id, resolution, rem_result)
        update_active_incident(incident_id, "itsm", closed)
        return {**state, "ticket": closed, "status": "TICKET_CLOSED",
                "updated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {**state, "status": "ERROR", "error": str(e)}


def feedback_node(state: PipelineState) -> PipelineState:
    try:
        human_decision = state.get("human_decision")
        if human_decision:
            result = feedback_agent.ingest_human_resolution(
                decision_result=state["decision_result"],
                human_decision=human_decision,
                human_notes=state.get("human_notes", ""),
                actual_fix=state.get("actual_fix", ""),
                outcome="RESOLVED" if human_decision == "APPROVED" else "REJECTED",
                mttr=state["ticket"].get("mttr_minutes", 0),
            )
        else:
            result = feedback_agent.ingest_auto_resolution(
                state["decision_result"], state["rem_result"], state["ticket"],
            )
        total_secs = round(
            (datetime.now(timezone.utc) -
             datetime.fromisoformat(state["started_at"])).total_seconds(), 2
        )
        update_active_incident(state["incident"]["incident_id"], "feedback", result)
        complete_incident(state["incident"]["incident_id"], "RESOLVED")
        return {**state, "feedback_result": result, "status": "RESOLVED",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_time_sec": total_secs}
    except Exception as e:
        return {**state, "status": "ERROR", "error": str(e)}


def route_after_monitor(state: PipelineState) -> str:
    if state.get("status") == "ERROR":
        return END
    if not state["monitor_result"]["is_anomaly"]:
        return END
    return "ml_scorer"


def route_after_decision(state: PipelineState) -> str:
    if state.get("status") == "ERROR":
        return END
    return "hitl" if state["decision_result"]["decision"] == "HITL" else "remediation"


def route_after_hitl(state: PipelineState) -> str:
    return END if state.get("human_decision", "PENDING") == "PENDING" else "remediation"


def build_graph(interrupt_hitl: bool = True):
    graph = StateGraph(PipelineState)
    graph.add_node("monitor",     monitor_node)
    graph.add_node("ml_scorer",   ml_scorer_node)
    graph.add_node("rca",         rca_node)
    graph.add_node("decision",    decision_node)
    graph.add_node("hitl",        hitl_node)
    graph.add_node("remediation", remediation_node)
    graph.add_node("itsm",        itsm_node)
    graph.add_node("feedback",    feedback_node)
    graph.add_edge(START, "monitor")
    graph.add_conditional_edges("monitor",  route_after_monitor,
                                {"ml_scorer": "ml_scorer", END: END})
    graph.add_edge("ml_scorer", "rca")
    graph.add_edge("rca",       "decision")
    graph.add_conditional_edges("decision", route_after_decision,
                                {"hitl": "hitl", "remediation": "remediation"})
    graph.add_conditional_edges("hitl",     route_after_hitl,
                                {"remediation": "remediation", END: END})
    graph.add_edge("remediation", "itsm")
    graph.add_edge("itsm",        "feedback")
    graph.add_edge("feedback",    END)
    memory = MemorySaver()
    app    = graph.compile(
        checkpointer     = memory,
        interrupt_before = ["hitl"] if interrupt_hitl else [],
    )
    return app, memory


class LangGraphOrchestrator:
    def __init__(self):
        init_agents()
        self.app, self.memory = build_graph(interrupt_hitl=True)
        self.pipeline_log     = []
        os.makedirs('/workspace/shared/incident_agent/data', exist_ok=True)
        if os.path.exists(PIPELINE_LOG_PATH):
            try:
                with open(PIPELINE_LOG_PATH, 'r') as f:
                    self.pipeline_log = json.load(f)
            except Exception:
                self.pipeline_log = []

    def run(self, incident):
        thread_id     = incident["incident_id"]
        config        = {"configurable": {"thread_id": thread_id}}
        initial_state = {
            "incident":        incident,
            "monitor_result":  None,
            "scored_result":   None,
            "rca_result":      None,
            "decision_result": None,
            "rem_result":      None,
            "ticket":          None,
            "feedback_result": None,
            "human_decision":  None,
            "human_notes":     None,
            "actual_fix":      None,
            "status":          "STARTED",
            "error":           None,
            "started_at":      datetime.now(timezone.utc).isoformat(),
            "updated_at":      datetime.now(timezone.utc).isoformat(),
            "total_time_sec":  None,
        }
        final_state = self.app.invoke(initial_state, config=config)
        if (final_state.get("decision_result") and
                final_state["decision_result"].get("decision") == "HITL"):
            self._save_hitl_state(thread_id, final_state)
            print(f"  ⏸  Added to HITL portal queue: {thread_id}")
        self._save_log(final_state)
        return final_state, config

    def resume_hitl(self, config, human_decision, human_notes, actual_fix):
        thread_id  = config["configurable"]["thread_id"]
        state_path = f'/workspace/shared/incident_agent/data/hitl_state_{thread_id}.json'
        if os.path.exists(state_path):
            with open(state_path, 'r') as f:
                state = json.load(f)
            state["human_decision"] = human_decision
            state["human_notes"]    = human_notes
            state["actual_fix"]     = actual_fix
            state = remediation_node(state)
            state = itsm_node(state)
            state = feedback_node(state)
            final_state = state
        else:
            update = {"human_decision": human_decision,
                      "human_notes": human_notes, "actual_fix": actual_fix}
            self.app.update_state(config, update, as_node="hitl")
            final_state = self.app.invoke(None, config=config)
        self._save_log(final_state)
        return final_state

    def _save_hitl_state(self, thread_id, state):
        queue = []
        if os.path.exists(HITL_QUEUE_PATH):
            try:
                with open(HITL_QUEUE_PATH, 'r') as f:
                    queue = json.load(f)
            except Exception:
                queue = []
        if thread_id not in [q["incident_id"] for q in queue]:
            queue.append({
                "incident_id":     thread_id,
                "queued_at":       datetime.now(timezone.utc).isoformat(),
                "decision_result": state["decision_result"],
                "status":          "PENDING",
                "thread_id":       thread_id,
            })
            state_path = f'/workspace/shared/incident_agent/data/hitl_state_{thread_id}.json'
            with open(state_path, 'w') as f:
                json.dump(dict(state), f, indent=2, default=str)
            with open(HITL_QUEUE_PATH, 'w') as f:
                json.dump(queue, f, indent=2, default=str)

    def _save_log(self, state):
        try:
            incident  = state.get("incident") or {}
            log_entry = {
                "incident_id":    incident.get("incident_id", "UNKNOWN"),
                "scenario":       incident.get("scenario_id", "UNKNOWN"),
                "status":         state.get("status", "UNKNOWN"),
                "started_at":     state.get("started_at", ""),
                "total_time_sec": state.get("total_time_sec"),
            }
            if state.get("rca_result"):
                log_entry["root_cause"] = state["rca_result"].get("root_cause")
            if state.get("decision_result"):
                log_entry["decision"] = state["decision_result"].get("decision")
            self.pipeline_log.append(log_entry)
            with open(PIPELINE_LOG_PATH, 'w') as f:
                json.dump(self.pipeline_log[-100:], f, indent=2, default=str)
        except Exception as e:
            print(f"  [Orchestrator] Log save error: {e}")


==================================================
FILE: ./agents/ml_scorer_agent.py
==================================================
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


==================================================
FILE: ./agents/monitor_agent.py
==================================================
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


==================================================
FILE: ./agents/rca_agent.py
==================================================
"""
RCA Agent — Root Cause Analysis using LLM + RAG.
Combines ChromaDB semantic search over runbooks and past incidents
with Qwen2.5-32B on AMD MI300X to generate root cause analysis
and remediation recommendations.
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import json
import time
import requests
from datetime import datetime, timezone
from rag.chroma_store import ChromaStore

OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:32b"
MAX_TOKENS   = 1000

SYSTEM_PROMPT = """You are an expert Site Reliability Engineer (SRE) \
with deep knowledge of infrastructure incidents.
Your job is to analyze incidents and provide:
1. Clear root cause explanation
2. Specific actionable fix steps ranked by priority
3. Confidence score (0-100%) based on how well symptoms match known patterns
4. Estimated resolution time

Always respond in valid JSON format exactly as specified.
Be specific, concise and actionable. No generic advice."""

class RCAAgent:
    def __init__(self):
        self.chroma     = ChromaStore()
        self.model      = OLLAMA_MODEL
        self.ollama_url = OLLAMA_URL

    def _build_prompt(self, scorer_result, rag_results):
        metrics        = scorer_result["metrics"]
        logs           = scorer_result.get("logs", [])
        threshold_hits = scorer_result.get("threshold_hits", [])
        service        = scorer_result.get("service", "unknown")
        severity       = scorer_result.get("severity", "UNKNOWN")

        metric_text = "\n".join([f"  - {k}: {v}" for k, v in metrics.items()])
        log_text    = "\n".join([f"  - {l}" for l in logs[:5]])
        thresh_text = "\n".join([
            f"  - {h['metric']} = {h['value']} [{h['level']}]"
            for h in threshold_hits
        ])

        remediation_context = ""
        for r in rag_results["remediation_chunks"][:2]:
            remediation_context += f"\n{r['document'][:300]}\n"

        root_cause_context = ""
        for r in rag_results["root_cause_chunks"][:2]:
            root_cause_context += f"\n{r['document'][:300]}\n"

        return f"""Analyze this infrastructure incident:

INCIDENT DETAILS:
  Service  : {service}
  Severity : {severity}

METRICS ANOMALIES:
{metric_text}

THRESHOLD VIOLATIONS:
{thresh_text if thresh_text else "  None detected"}

ERROR LOGS:
{log_text if log_text else "  No logs available"}

RELEVANT RUNBOOK - ROOT CAUSES:
{root_cause_context if root_cause_context else "  No relevant runbook found"}

RELEVANT RUNBOOK - REMEDIATION:
{remediation_context if remediation_context else "  No relevant runbook found"}

Based on the above, respond ONLY with this JSON.
For confidence score:
  - 90-100: symptoms match runbook exactly, clear root cause
  - 70-89 : symptoms mostly match, likely root cause
  - 50-69 : partial match, possible root cause
  - below 50: unclear, needs investigation

{{
  "root_cause": "one clear sentence explaining why this happened",
  "recommendations": [
    "Step 1: specific action",
    "Step 2: specific action",
    "Step 3: specific action"
  ],
  "confidence": <your assessed confidence 0-100>,
  "resolution_time": "<estimated time>",
  "incident_type": "<CPU_SPIKE|DB_CONN_EXHAUSTION|MEMORY_LEAK|DISK_FULL|SERVICE_CRASH>"
}}"""

    def _call_ollama(self, prompt):
        start   = time.time()
        payload = {
            "model":    self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            "stream":  False,
            "options": {"temperature": 0.1, "num_predict": MAX_TOKENS},
        }
        try:
            response    = requests.post(self.ollama_url, json=payload, timeout=120)
            latency_ms  = round((time.time() - start) * 1000, 2)
            data        = response.json()
            content     = data["message"]["content"]
            token_count = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)
            return {"content": content, "latency_ms": latency_ms,
                    "token_count": token_count, "model": self.model}
        except Exception as e:
            return {
                "content": json.dumps({
                    "root_cause":      f"LLM unavailable: {str(e)}",
                    "recommendations": ["Check Ollama server connection"],
                    "confidence":      0,
                    "resolution_time": "unknown",
                    "incident_type":   "UNKNOWN",
                }),
                "latency_ms": 0, "token_count": 0, "model": self.model,
            }

    def _parse_response(self, content):
        try:
            start = content.find('{')
            end   = content.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(content[start:end])
        except Exception:
            pass
        return {
            "root_cause":      content[:200],
            "recommendations": ["Manual investigation required"],
            "confidence":      0,
            "resolution_time": "unknown",
            "incident_type":   "UNKNOWN",
        }

    def analyze(self, scorer_result):
        start       = time.time()
        incident_id = scorer_result.get("incident_id", "UNKNOWN")
        service     = scorer_result.get("service", "unknown")
        metrics     = scorer_result.get("metrics", {})
        logs        = scorer_result.get("logs", [])
        severity    = scorer_result.get("severity", "UNKNOWN")

        print(f"  [RCAAgent] Analyzing: {incident_id} | {service} | {severity}")

        rag_start   = time.time()
        rag_results = self.chroma.search_for_rca(
            incident_type=severity, metrics=metrics, logs=logs,
        )
        rag_latency = round((time.time() - rag_start) * 1000, 2)
        print(f"  [RCAAgent] RAG: {rag_latency}ms | LLM calling...")

        prompt     = self._build_prompt(scorer_result, rag_results)
        llm_result = self._call_ollama(prompt)
        print(f"  [RCAAgent] LLM: {llm_result['latency_ms']}ms | {llm_result['token_count']} tokens")

        parsed     = self._parse_response(llm_result["content"])
        total_time = round((time.time() - start) * 1000, 2)

        return {
            "incident_id":      incident_id,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "service":          service,
            "severity":         severity,
            "root_cause":       parsed.get("root_cause", "Unknown"),
            "recommendations":  parsed.get("recommendations", []),
            "confidence":       parsed.get("confidence", 0),
            "resolution_time":  parsed.get("resolution_time", "unknown"),
            "incident_type":    parsed.get("incident_type", "UNKNOWN"),
            "rag_results":      rag_results,
            "metrics":          metrics,
            "logs":             logs,
            "llm_model":        llm_result["model"],
            "token_count":      llm_result["token_count"],
            "llm_latency_ms":   llm_result["latency_ms"],
            "rag_latency_ms":   rag_latency,
            "total_latency_ms": total_time,
            "raw_data":         scorer_result,
        }


==================================================
FILE: ./agents/remediation_agent.py
==================================================
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


==================================================
FILE: ./agents/status_writer.py
==================================================
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


==================================================
FILE: ./dashboard.py
==================================================
"""
Dashboard — Control panel and live monitoring for the
Autonomous Incident Management System.

Tab 1: Control Panel — trigger incidents
Tab 2: Live Monitor   — active incidents with per-agent progress
Tab 3: History        — resolved incidents, click for details

Run: streamlit run dashboard.py --server.port 8502
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import json
import subprocess
import streamlit as st
from datetime import datetime, timezone

STATUS_PATH  = '/workspace/shared/incident_agent/data/dashboard_status.json'
HISTORY_PATH = '/workspace/shared/incident_agent/data/dashboard_history.json'
RUN_LOG_PATH = '/workspace/shared/incident_agent/data/dashboard_run.log'
PID_PATH     = '/workspace/shared/incident_agent/data/dashboard_run.pid'

SCENARIOS = ["CPU_SPIKE", "DB_CONN_EXHAUSTION", "MEMORY_LEAK", "DISK_FULL", "SERVICE_CRASH"]

st.set_page_config(page_title="Incident Management Dashboard", page_icon="📊", layout="wide")

st.markdown("""
<style>
#root > div:first-child { padding-top: 0 !important; }
.stAppHeader { display: none !important; }
[data-testid="stAppViewContainer"] > div:first-child { padding-top: 1rem !important; }
[data-testid="stMetricValue"] { font-size: 16px !important; font-weight: 600; }
[data-testid="stMetricLabel"] { font-size: 11px !important; color: #57606a; }
[data-testid="stMetric"] {
    background: #f6f8fa; border: 1px solid #d0d7de;
    border-radius: 8px; padding: 10px 14px !important;
}
.block-container { padding-top: 1rem !important; }
h3 { font-size: 15px !important; }
</style>
""", unsafe_allow_html=True)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def is_process_running():
    if not os.path.exists(PID_PATH):
        return False
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        # Process is dead, clean up stale PID file
        try:
            os.remove(PID_PATH)
        except Exception:
            pass
        return False


def time_ago(iso_str):
    try:
        dt   = datetime.fromisoformat(iso_str)
        now  = datetime.now(timezone.utc)
        diff = int((now - dt).total_seconds())
        if diff < 60:   return f"{diff}s ago"
        if diff < 3600: return f"{diff//60}m ago"
        return f"{diff//3600}h ago"
    except Exception:
        return "—"


def severity_icon(severity):
    return {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢", "NONE": "⚪"}.get(severity, "⚪")


# ── Top-left Refresh / Clear button ───────────────────────
if st.button("🔄 Refresh", key="top_refresh"):
    try:
        if os.path.exists(PID_PATH):
            with open(PID_PATH) as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
            except Exception:
                os.remove(PID_PATH)
    except Exception:
        pass
    st.rerun()

# ── Header ────────────────────────────────────────────────
status  = load_json(STATUS_PATH, {"active": {}})
history = load_json(HISTORY_PATH, [])
active  = status.get("active", {})

st.markdown(
    f"<div style='display:flex;align-items:center;justify-content:space-between;padding:8px 0 4px 0'>"
    f"<div>"
    f"<span style='font-size:20px;font-weight:700;color:#24292f'>📊 Incident Management Dashboard</span>"
    f"<span style='font-size:13px;color:#57606a;margin-left:10px'>"
    f"TCS & AMD AI Hackathon — AGENTS_026</span>"
    f"</div>"
    f"<div style='display:flex;gap:16px;align-items:center'>"
    f"<span style='background:#fff8c5;border:1px solid #d4a72c;border-radius:20px;"
    f"padding:3px 12px;font-size:13px;font-weight:600;color:#7d5a00'>"
    f"⚡ {len(active)} active</span>"
    f"<span style='background:#dafbe1;border:1px solid #56d364;border-radius:20px;"
    f"padding:3px 12px;font-size:13px;font-weight:600;color:#116329'>"
    f"✅ {len(history)} resolved</span>"
    f"</div></div>",
    unsafe_allow_html=True
)
st.divider()

tab1, tab2, tab3 = st.tabs(["🚀 Control Panel", "📡 Live Monitor", "📋 History"])

# ── TAB 1: CONTROL PANEL ─────────────────────────────────
with tab1:
    st.markdown("### Run Pipeline")

    running = is_process_running()

    if running:
        st.warning("⚠️ A run is currently in progress.")

    if not running:
        c1, c2, c3 = st.columns([1.3, 1.5, 0.7])

        with c1:
            mode = st.selectbox(
                "Mode", ["Single Scenario", "All Scenarios", "Continuous Stream"],
                label_visibility="collapsed",
            )

        cmd = None
        if mode == "Single Scenario":
            with c2:
                scenario = st.selectbox("Scenario", SCENARIOS, label_visibility="collapsed")
            cmd = ["python3", "main_runner.py", "--scenario", scenario]

        elif mode == "All Scenarios":
            with c2:
                st.caption("Runs all 5 scenarios sequentially (5s gap between each)")
            cmd = ["python3", "main_runner.py", "--all"]

        elif mode == "Continuous Stream":
            with c2:
                cc1, cc2 = st.columns(2)
                interval = cc1.number_input("Interval (s)", min_value=10, max_value=120, value=30, step=5)
                rate     = cc2.slider("Rate", min_value=0.1, max_value=1.0, value=0.4, step=0.1)
            cmd = ["python3", "main_runner.py", "--continuous",
                   "--interval", str(interval), "--rate", str(rate)]

        with c3:
            start_clicked = st.button("▶ Start", type="primary", use_container_width=True)

        if start_clicked:
            log_f = open(RUN_LOG_PATH, 'w')
            proc  = subprocess.Popen(
                cmd, cwd='/workspace/shared/incident_agent',
                stdout=log_f, stderr=subprocess.STDOUT,
            )
            with open(PID_PATH, 'w') as f:
                f.write(str(proc.pid))
            st.success(f"Started! PID: {proc.pid}")
            st.info("Switch to the **Live Monitor** tab to watch progress.")
            st.rerun()

    st.divider()

    # ── Status summary (clean, not raw logs) ─────────────
    st.markdown("### Status")

    if running:
        # Show a short live summary instead of raw logs
        last_line = ""
        if os.path.exists(RUN_LOG_PATH):
            with open(RUN_LOG_PATH) as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            # Find last meaningful line (skip onnxruntime noise)
            for line in reversed(lines):
                if "onnxruntime" not in line and "pthread_setaffinity" not in line:
                    last_line = line
                    break

        c1, c2 = st.columns([1, 4])
        c1.markdown("🔄 **Running**")
        c2.caption(last_line[:120] if last_line else "Starting up...")

        with st.expander("View full log"):
            if os.path.exists(RUN_LOG_PATH):
                with open(RUN_LOG_PATH) as f:
                    log_content = f.read()
                clean_lines = [l for l in log_content.split('\n')
                               if "onnxruntime" not in l and "pthread_setaffinity" not in l]
                clean_log = '\n'.join(clean_lines)
                st.code(clean_log[-3000:] if len(clean_log) > 3000 else clean_log, language="bash")
    else:
        st.caption("System idle. Select a mode above and click Start.")

        with st.expander("View last run log"):
            if os.path.exists(RUN_LOG_PATH):
                with open(RUN_LOG_PATH) as f:
                    log_content = f.read()
                clean_lines = [l for l in log_content.split('\n')
                               if "onnxruntime" not in l and "pthread_setaffinity" not in l]
                clean_log = '\n'.join(clean_lines)
                st.code(clean_log[-3000:] if len(clean_log) > 3000 else clean_log, language="bash")
            else:
                st.caption("No runs yet.")

        st.caption("ℹ️ Incidents requiring approval will appear in HITL Portal. "
                   "Once resolved, view full details in the **History** tab.")

    st.divider()

    # ── Background services status ────────────────────────
    st.markdown("### Background Services")

    hitl_proc_running = False
    try:
        result = subprocess.run(["pgrep", "-f", "hitl_processor.py"],
                                capture_output=True, text=True)
        hitl_proc_running = bool(result.stdout.strip())
    except Exception:
        pass

    c1, c2 = st.columns(2)
    with c1:
        if hitl_proc_running:
            st.success("✅ HITL Processor running")
        else:
            st.error("❌ HITL Processor not running")
            st.caption("Run in a terminal: `python3 hitl_processor.py`")
    with c2:
        st.info("👤 HITL Portal: separate app (port 8501)")


# ── TAB 2: LIVE MONITOR ───────────────────────────────────
with tab2:
    st.markdown("### Active Incidents")
    st.caption("ℹ️ Once HITL-approved, incidents complete via the background HITL Processor "
               "and move to the **History** tab with full remediation details.")

    if not active:
        st.success("✅ No active incidents — system is idle or all resolved.")
    else:
        for incident_id, data in active.items():
            severity = data.get("severity", "UNKNOWN")
            scenario = data.get("scenario", "UNKNOWN")
            service  = data.get("service", "unknown")
            steps    = data.get("steps", {})
            current  = data.get("current_step", "")

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                c1.markdown(f"**{severity_icon(severity)} {incident_id}**")
                c2.caption(f"Scenario: {scenario}")
                c3.caption(f"Service: {service}")
                c4.caption(f"Updated: {time_ago(data.get('updated_at',''))}")

                pipeline_steps = ["monitor", "ml_scorer", "rca", "decision",
                                  "hitl", "remediation", "itsm", "feedback"]
                step_labels = {
                    "monitor": "Monitor", "ml_scorer": "ML Scorer", "rca": "RCA",
                    "decision": "Decision", "hitl": "HITL", "remediation": "Remediation",
                    "itsm": "ITSM", "feedback": "Feedback",
                }

                cols = st.columns(len(pipeline_steps))
                for col, step in zip(cols, pipeline_steps):
                    if step in steps:
                        col.markdown(f"<div style='text-align:center'>"
                                     f"<div style='font-size:20px'>✅</div>"
                                     f"<div style='font-size:11px;color:#57606a'>{step_labels[step]}</div>"
                                     f"</div>", unsafe_allow_html=True)
                    elif step == current:
                        col.markdown(f"<div style='text-align:center'>"
                                     f"<div style='font-size:20px'>🔄</div>"
                                     f"<div style='font-size:11px;color:#57606a'>{step_labels[step]}</div>"
                                     f"</div>", unsafe_allow_html=True)
                    else:
                        col.markdown(f"<div style='text-align:center;opacity:0.3'>"
                                     f"<div style='font-size:20px'>⚪</div>"
                                     f"<div style='font-size:11px;color:#57606a'>{step_labels[step]}</div>"
                                     f"</div>", unsafe_allow_html=True)

                with st.expander("View agent details", expanded=False):
                    if "monitor" in steps:
                        m = steps["monitor"]
                        st.markdown(f"**🔍 Monitor** — anomaly: `{m['is_anomaly']}` | "
                                   f"score: `{m['anomaly_score']}` | "
                                   f"threshold hits: `{len(m['threshold_hits'])}`")

                    if "ml_scorer" in steps:
                        s = steps["ml_scorer"]
                        st.markdown(f"**📊 ML Scorer** — severity: `{s['severity']}` | "
                                   f"confidence: `{s['confidence']}` | "
                                   f"latency: `{s['scoring_latency_ms']}ms`")

                    if "rca" in steps:
                        r = steps["rca"]
                        st.markdown(f"**🧠 RCA** — confidence: `{r['confidence']}%` | "
                                   f"tokens: `{r['token_count']}` | "
                                   f"LLM latency: `{r['llm_latency_ms']}ms`")
                        st.info(r['root_cause'])
                        for i, rec in enumerate(r['recommendations'], 1):
                            st.write(f"  {i}. {rec}")

                    if "decision" in steps:
                        d = steps["decision"]
                        st.markdown(f"**⚖️ Decision** — `{d['decision']}` | "
                                   f"risk: `{d['risk_level']}` | "
                                   f"reason: {d['reason']}")

                    if "hitl" in steps:
                        h = steps["hitl"]
                        st.markdown(f"**👤 HITL** — decision: `{h['human_decision']}`")
                        if h.get("human_notes"):
                            st.caption(f"Notes: {h['human_notes']}")

                    if "remediation" in steps:
                        rem = steps["remediation"]
                        st.markdown(f"**🔧 Remediation** — status: `{rem['status']}` | "
                                   f"steps: `{rem.get('total_steps',0)}` | "
                                   f"time: `{rem.get('total_time_sec',0)}s`")

                    if "itsm" in steps:
                        t = steps["itsm"]
                        st.markdown(f"**🎫 ITSM** — ticket: `{t['ticket_id']}` | "
                                   f"priority: `{t['priority']}` | "
                                   f"status: `{t['status']}`")

                    if "feedback" in steps:
                        st.markdown("**📝 Feedback** — stored in knowledge base ✅")

                    if current == "decision" and "hitl" not in steps:
                        d = steps.get("decision", {})
                        if d.get("decision") == "HITL":
                            st.warning("⏸ Awaiting human review in HITL Portal "
                                      "(separate app, port 8501)")


# ── TAB 3: HISTORY ─────────────────────────────────────────
with tab3:
    st.markdown("### Resolved Incidents")

    if not history:
        st.caption("No resolved incidents yet.")
    else:
        search = st.text_input(
            "🔍 Search by incident ID, scenario, service, or severity",
            placeholder="e.g. INC-202606..., MEMORY_LEAK, CRITICAL, app-service",
        )

        filtered = history
        if search:
            s = search.lower()
            filtered = [
                h for h in history
                if s in (h.get("incident_id") or "").lower()
                or s in (h.get("scenario") or "").lower()
                or s in (h.get("service") or "").lower()
                or s in (h.get("severity") or "").lower()
            ]
            st.caption(f"Found {len(filtered)} of {len(history)} incidents")

        if not filtered:
            st.warning("No incidents match your search.")

        for h in filtered:
            incident_id = h["incident_id"]
            severity    = h.get("severity", "UNKNOWN")
            scenario    = h.get("scenario", "UNKNOWN")
            final       = h.get("final_status", "UNKNOWN")
            service     = h.get("service", "unknown")
            steps       = h.get("steps", {})

            mttr = "—"
            if "itsm" in steps:
                mttr = f"{steps['itsm'].get('mttr_minutes', 0)} min"

            decision_type = "—"
            if "decision" in steps:
                decision_type = steps["decision"].get("decision", "—")
                if "hitl" in steps:
                    decision_type = f"HITL → {steps['hitl'].get('human_decision','—')}"

            with st.expander(
                f"{severity_icon(severity)} {incident_id} | {scenario} | "
                f"{decision_type} | MTTR: {mttr} | {time_ago(h.get('completed_at',''))}"
            ):
                # ── Summary metrics ───────────────────────
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Severity", severity)
                c2.metric("Service", service)
                c3.metric("Decision", decision_type.split(" → ")[0])
                c4.metric("MTTR", mttr)
                c5.metric("Status", final)

                st.divider()

                # ── 1. Monitor Agent ──────────────────────
                if "monitor" in steps:
                    m = steps["monitor"]
                    st.markdown("#### 1️⃣ Monitor Agent")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Anomaly Detected", "Yes" if m["is_anomaly"] else "No")
                    c2.metric("Anomaly Score", m["anomaly_score"])
                    c3.metric("Threshold Hits", len(m["threshold_hits"]))

                    with st.container():
                        st.caption("Metrics at detection time")
                        mcols = st.columns(5)
                        for col, (k, v) in zip(mcols, m["metrics"].items()):
                            col.metric(k.replace("_", " ").title(), v)

                    if m.get("logs"):
                        st.caption("Log excerpts")
                        for log in m["logs"]:
                            st.code(log, language="text")

                    st.divider()

                # ── 2. ML Scorer Agent ────────────────────
                if "ml_scorer" in steps:
                    s = steps["ml_scorer"]
                    st.markdown("#### 2️⃣ ML Scorer Agent")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Severity", s["severity"])
                    c2.metric("Confidence", f"{s['confidence']:.3f}")
                    c3.metric("Scoring Latency", f"{s['scoring_latency_ms']:.2f} ms")
                    st.divider()

                # ── 3. RCA Agent ───────────────────────────
                if "rca" in steps:
                    r = steps["rca"]
                    st.markdown("#### 3️⃣ RCA Agent (LLM + RAG)")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Confidence", f"{r['confidence']}%")
                    c2.metric("Tokens", r['token_count'])
                    c3.metric("LLM Latency", f"{r['llm_latency_ms']:.0f} ms")
                    c4.metric("RAG Latency", f"{r['rag_latency_ms']:.0f} ms")

                    st.caption("Root Cause")
                    st.info(r['root_cause'])

                    st.caption("Recommendations")
                    for i, rec in enumerate(r['recommendations'], 1):
                        st.write(f"{i}. {rec}")

                    st.caption(f"Model: `{r['llm_model']}` | "
                              f"Incident type identified: `{r['incident_type']}` | "
                              f"Est. resolution: {r.get('resolution_time','N/A')}")
                    st.divider()

                # ── 4. Decision Agent ──────────────────────
                if "decision" in steps:
                    d = steps["decision"]
                    st.markdown("#### 4️⃣ Decision Agent")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Routing", d["decision"])
                    c2.metric("Risk Level", d["risk_level"])
                    c3.metric("Combined Confidence", f"{d['confidence']}%")
                    st.caption(f"Reason: {d['reason']}")

                    factors = d.get("factors", {})
                    if factors:
                        st.caption("Confidence breakdown")
                        fc1, fc2, fc3 = st.columns(3)
                        fc1.write(f"ML: `{factors.get('ml_confidence',0):.1f}%`")
                        fc2.write(f"LLM: `{factors.get('llm_confidence',0):.1f}%`")
                        fc3.write(f"Threshold: `{factors.get('confidence_threshold',0)}%`")
                    st.divider()

                # ── 5. Human Review (HITL) ─────────────────
                if "hitl" in steps:
                    hh = steps["hitl"]
                    st.markdown("#### 5️⃣ Human Review (HITL)")
                    c1, c2 = st.columns(2)
                    icon = "✅" if hh['human_decision'] == "APPROVED" else "❌"
                    c1.metric("Decision", f"{icon} {hh['human_decision']}")
                    c2.metric("Reviewed by", "SRE-Engineer")

                    if hh.get("human_notes"):
                        st.caption("Engineer Notes")
                        st.write(hh['human_notes'])
                    if hh.get("actual_fix"):
                        st.caption("Applied Fix")
                        st.write(hh['actual_fix'])
                    st.divider()

                # ── 6. Remediation Agent ───────────────────
                if "remediation" in steps:
                    rem = steps["remediation"]
                    st.markdown("#### 6️⃣ Remediation Agent")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Status", rem["status"])
                    c2.metric("Steps Executed", rem.get("total_steps", 0))
                    c3.metric("Total Time", f"{rem.get('total_time_sec',0)}s")

                    for a in rem.get("actions_taken", []):
                        st.write(f"**Step {a['step']}** — {a['description']} "
                                f"`({a['duration_sec']}s)` — {a['status']}")
                    st.divider()

                # ── 7. ITSM Agent ───────────────────────────
                if "itsm" in steps:
                    t = steps["itsm"]
                    st.markdown("#### 7️⃣ ITSM Agent")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Ticket ID", t['ticket_id'])
                    c2.metric("Priority", t['priority'])
                    c3.metric("Team", t['assigned_team'])
                    c4.metric("MTTR", f"{t.get('mttr_minutes',0)} min")

                    st.caption("Resolution")
                    st.write(t.get('resolution', 'N/A'))
                    st.divider()

                # ── 8. Feedback Agent ───────────────────────
                if "feedback" in steps:
                    fb = steps["feedback"]
                    st.markdown("#### 8️⃣ Feedback Agent")
                    st.success("✅ Resolution stored in ChromaDB knowledge base "
                               "for future RCA improvement")

                    c1, c2 = st.columns(2)
                    c1.metric("Decision Type", fb.get("decision_type", "—"))
                    c2.metric("ChromaDB Doc ID", fb.get("chroma_doc_id", "—"))

                    st.caption("Feedback Record")
                    fb_text = (
                        f"**Root Cause:** {fb.get('root_cause', 'N/A')}\n\n"
                        f"**Fix Applied:** {fb.get('fix_applied', 'N/A')}\n\n"
                        f"**Outcome:** {fb.get('outcome', 'N/A')}"
                    )
                    if fb.get("human_notes"):
                        fb_text += f"\n\n**Human Notes:** {fb['human_notes']}"
                    st.info(fb_text)


==================================================
FILE: ./hitl/hitl_portal.py
==================================================
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import json
import time
import streamlit as st
from datetime import datetime, timezone

HITL_QUEUE_PATH     = '/workspace/shared/incident_agent/data/hitl_queue.json'
HITL_DECISIONS_PATH = '/workspace/shared/incident_agent/data/hitl_decisions.json'
PIPELINE_LOG_PATH   = '/workspace/shared/incident_agent/data/pipeline_log.json'

NORMAL_RANGES = {
    "cpu_usage":    {"min": 15,  "max": 45,   "unit": "%",  "label": "CPU Usage"},
    "memory_usage": {"min": 30,  "max": 55,   "unit": "%",  "label": "Memory"},
    "error_rate":   {"min": 0,   "max": 1.5,  "unit": "%",  "label": "Error Rate"},
    "latency_ms":   {"min": 50,  "max": 200,  "unit": "ms", "label": "Latency"},
    "disk_io":      {"min": 5,   "max": 20,   "unit": "%",  "label": "Disk I/O"},
}

st.set_page_config(
    page_title="Incident HITL Portal",
    page_icon="🚨",
    layout="wide",
)
st.markdown("""
<style>
#root > div:first-child { padding-top: 0 !important; }
.stAppHeader { display: none !important; }
[data-testid="stAppViewContainer"] > div:first-child { padding-top: 1rem !important; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 16px !important; font-weight: 600; }
[data-testid="stMetricLabel"] { font-size: 11px !important; color: #57606a; }
[data-testid="stMetricDelta"] { font-size: 11px !important; }
[data-testid="stMetric"] {
    background: #f6f8fa;
    border: 1px solid #d0d7de;
    border-radius: 8px;
    padding: 10px 14px !important;
}
.block-container { padding-top: 1rem !important; }
h1 { font-size: 22px !important; margin-bottom: 0 !important; }
h2 { font-size: 16px !important; }
h3 { font-size: 15px !important; }
</style>
""", unsafe_allow_html=True)

def load_queue():
    if not os.path.exists(HITL_QUEUE_PATH):
        return []
    try:
        with open(HITL_QUEUE_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return []

def load_decisions():
    if not os.path.exists(HITL_DECISIONS_PATH):
        return []
    try:
        with open(HITL_DECISIONS_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return []

def save_decision(incident_id, decision, notes, actual_fix):
    decisions = load_decisions()
    decisions.append({
        "incident_id": incident_id,
        "decision":    decision,
        "notes":       notes,
        "actual_fix":  actual_fix,
        "decided_at":  datetime.now(timezone.utc).isoformat(),
        "decided_by":  "SRE-Engineer",
    })
    with open(HITL_DECISIONS_PATH, 'w') as f:
        json.dump(decisions, f, indent=2)

def remove_from_queue(incident_id):
    queue = load_queue()
    queue = [q for q in queue if q["incident_id"] != incident_id]
    with open(HITL_QUEUE_PATH, 'w') as f:
        json.dump(queue, f, indent=2, default=str)

def get_decided_ids():
    return {d["incident_id"] for d in load_decisions()}

def time_ago(iso_str):
    try:
        dt   = datetime.fromisoformat(iso_str)
        now  = datetime.now(timezone.utc)
        diff = int((now - dt).total_seconds())
        if diff < 60:   return f"{diff}s ago"
        if diff < 3600: return f"{diff//60}m ago"
        return f"{diff//3600}h ago"
    except Exception:
        return "recently"

def severity_icon(severity):
    return {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(severity, "⚪")

def metric_status(key, value):
    info = NORMAL_RANGES.get(key, {})
    nmax = info.get("max", 100)
    if value > nmax * 2:   return "🔴 Critical"
    if value > nmax * 1.3: return "🟠 High"
    if value > nmax:       return "🟡 Elevated"
    return "🟢 Normal"

def render_info_row(label, value, color="#24292f"):
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;"
        f"padding:6px 0;border-bottom:1px solid #e1e4e8'>"
        f"<span style='font-size:13px;color:#57606a'>{label}</span>"
        f"<span style='font-size:13px;font-weight:600;color:{color}'>{value}</span>"
        f"</div>",
        unsafe_allow_html=True
    )

def render_detail(item):
    incident_id     = item["incident_id"]
    dr              = item.get("decision_result", {})
    rca             = dr.get("rca_result", {}) or {}
    severity        = dr.get("severity", "UNKNOWN")
    service         = dr.get("service", "unknown")
    root_cause      = dr.get("root_cause", "Unknown")
    confidence      = float(dr.get("confidence", 0))
    recommendations = dr.get("recommendations", [])
    metrics         = dr.get("metrics", {})
    logs            = dr.get("logs", [])
    risk_level      = dr.get("risk_level", "UNKNOWN")
    incident_type   = rca.get("incident_type", "UNKNOWN")
    queued_at       = item.get("queued_at", "")

    sev_color = {"CRITICAL": "#cf222e", "HIGH": "#9a6700",
                 "MEDIUM": "#116329", "LOW": "#0969da"}.get(severity, "#24292f")

    # ── Incident header ──────────────────────────────────
    st.markdown(
        f"<div style='background:white;border:1px solid #d0d7de;"
        f"border-radius:10px;padding:16px 20px;margin-bottom:12px'>"
        f"<div style='font-size:18px;font-weight:700;color:#24292f'>"
        f"{severity_icon(severity)} {incident_id}</div>"
        f"<div style='margin-top:10px'>",
        unsafe_allow_html=True
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Severity",    severity)
    c2.metric("Risk",        risk_level)
    c3.metric("Service",     service)
    c4.metric("Type",        incident_type)
    c5.metric("Queued",      time_ago(queued_at))

    st.markdown("</div></div>", unsafe_allow_html=True)

    # ── Confidence ───────────────────────────────────────
    with st.container(border=True):
        conf_label = (
            "High confidence — AI is fairly certain about root cause"
            if confidence >= 80 else
            "Medium confidence — human review recommended"
            if confidence >= 60 else
            "Low confidence — human review is critical"
        )
        st.caption("AI CONFIDENCE SCORE")
        st.progress(min(confidence / 100, 1.0),
                    text=f"**{confidence}%** — {conf_label}")

    # ── Root cause ───────────────────────────────────────
    with st.container(border=True):
        st.caption("ROOT CAUSE ANALYSIS")
        st.warning(f"**{root_cause}**", icon="⚠️")

    # ── Metrics ──────────────────────────────────────────
    with st.container(border=True):
        st.caption(f"SERVICE METRICS — {service}")
        st.write("Current values vs normal operating range:")

        keys  = ["cpu_usage", "memory_usage", "error_rate", "latency_ms", "disk_io"]
        cols  = st.columns(5)
        for col, key in zip(cols, keys):
            if key in metrics:
                info  = NORMAL_RANGES[key]
                val   = metrics[key]
                nmax  = info["max"]
                nmin  = info["min"]
                unit  = info["unit"]
                label = info["label"]
                delta = round(val - nmax, 1)
                col.metric(
                    label      = label,
                    value      = f"{val}{unit}",
                    delta      = f"+{delta}{unit}" if delta > 0 else f"Normal",
                    delta_color= "inverse" if delta > 0 else "normal"
                )

        st.write("")
        for key in keys:
            if key in metrics:
                info   = NORMAL_RANGES[key]
                val    = metrics[key]
                nmax   = info["max"]
                unit   = info["unit"]
                label  = info["label"]
                status = metric_status(key, val)
                bar    = min(val / (nmax * 2.5), 1.0)
                c_l, c_b, c_s = st.columns([1.5, 4, 1.5])
                c_l.write(f"**{label}**")
                c_b.progress(bar)
                c_s.write(f"{val}{unit} {status}")

    # ── Logs ─────────────────────────────────────────────
    if logs:
        with st.expander("📋 View Error Logs", expanded=False):
            for log in logs:
                st.code(log, language="bash")

    # ── Fix steps ────────────────────────────────────────
    with st.container(border=True):
        st.caption("RECOMMENDED FIX STEPS")
        for i, rec in enumerate(recommendations, 1):
            st.write(f"**{i}.** {rec}")

    # ── Decision ─────────────────────────────────────────
    with st.container(border=True):
        st.caption("YOUR DECISION")

        notes = st.text_area(
            "Notes / observations",
            placeholder="Add context, observations or modifications...",
            key=f"notes_{incident_id}",
            height=80,
        )
        actual_fix = st.text_input(
            "Override fix (optional)",
            placeholder="Leave blank to use AI recommendations",
            key=f"fix_{incident_id}",
        )
        if not actual_fix:
            actual_fix = (" | ".join(recommendations[:2])
                          if recommendations else "Follow AI recommendations")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅  Approve & Execute Fix",
                         key=f"approve_{incident_id}",
                         type="primary",
                         use_container_width=True):
                save_decision(incident_id, "APPROVED",
                              notes or "Approved", actual_fix)
                remove_from_queue(incident_id)
                st.success("✅ Approved! Remediation executing.")
                time.sleep(1)
                st.rerun()
        with c2:
            if st.button("❌  Reject & Escalate",
                         key=f"reject_{incident_id}",
                         use_container_width=True):
                save_decision(incident_id, "REJECTED",
                              notes or "Rejected",
                              "Manual investigation required")
                remove_from_queue(incident_id)
                st.warning("❌ Rejected. Escalated for manual review.")
                time.sleep(1)
                st.rerun()

def main():
    queue       = load_queue()
    decisions   = load_decisions()
    decided_ids = get_decided_ids()
    pending     = [q for q in queue
                   if q.get("status") == "PENDING"
                   and q["incident_id"] not in decided_ids]

    # ── Header ───────────────────────────────────────────
    # Header row
    st.markdown(
        f"<div style='display:flex;align-items:center;justify-content:space-between;"
        f"padding:8px 0 4px 0'>"
        f"<div>"
        f"<span style='font-size:20px;font-weight:700;color:#24292f'>🚨 Incident Review Portal</span>"
        f"<span style='font-size:13px;color:#57606a;margin-left:10px'>"
        f"Autonomous Incident Management · Human-in-the-Loop</span>"
        f"</div>"
        f"<div style='display:flex;gap:16px;align-items:center'>"
        f"<span style='background:#fff0f0;border:1px solid #ffa198;border-radius:20px;"
        f"padding:3px 12px;font-size:13px;font-weight:600;color:#cf222e'>"
        f"🔴 {len(pending)} pending</span>"
        f"<span style='background:#dafbe1;border:1px solid #56d364;border-radius:20px;"
        f"padding:3px 12px;font-size:13px;font-weight:600;color:#116329'>"
        f"✅ {len(decisions)} resolved</span>"
        f"</div></div>",
        unsafe_allow_html=True
    )
    if st.button("🔄 Refresh page", key="refresh_top"):
        st.rerun()

    st.divider()

    # ── Two panel layout ─────────────────────────────────
    left, right = st.columns([1, 3])

    with left:
        st.markdown("#### Pending Reviews")

        if not pending:
            st.success("✅ No pending incidents")
        else:
            for item in pending:
                iid      = item["incident_id"]
                dr       = item.get("decision_result", {})
                severity = dr.get("severity", "UNKNOWN")
                service  = dr.get("service", "unknown")
                icon     = severity_icon(severity)
                is_sel   = st.session_state.get("selected") == iid

                if st.button(
                    f"{icon} {iid}\n{service} | {severity}\n🕐 {time_ago(item.get('queued_at',''))}",
                    key=f"sel_{iid}",
                    use_container_width=True,
                    type="primary" if is_sel else "secondary"
                ):
                    st.session_state["selected"] = iid
                    st.rerun()

        st.divider()
        st.markdown("#### Recent Decisions")

        if not decisions:
            st.caption("No decisions yet")
        else:
            for d in reversed(decisions[-5:]):
                icon = "✅" if d["decision"] == "APPROVED" else "❌"
                with st.expander(
                    f"{icon} {d['incident_id']} · {time_ago(d['decided_at'])}",
                    expanded=False
                ):
                    st.write(f"**Decision:** {d['decision']}")
                    st.write(f"**Fix Applied:** {d['actual_fix']}")
                    st.write(f"**Notes:** {d.get('notes') or 'None'}")
                    st.write(f"**Decided by:** {d.get('decided_by','SRE-Engineer')}")
                    st.caption(f"Time: {d['decided_at']}")

    with right:
        selected_id = st.session_state.get("selected")

        if not pending:
            st.markdown("### 🛡️ All systems normal")
            st.write("No incidents require your attention right now.")
        elif (not selected_id or
              selected_id not in [p["incident_id"] for p in pending]):
            st.session_state["selected"] = pending[0]["incident_id"]
            st.rerun()
        else:
            item = next(p for p in pending
                        if p["incident_id"] == selected_id)
            render_detail(item)

if __name__ == "__main__":
    main()


==================================================
FILE: ./hitl_processor.py
==================================================
"""
HITL Processor — Background process that automatically handles
human decisions from the HITL portal and triggers remediation.
Run this alongside main_runner.py during demo.

Usage:
  python3 hitl_processor.py
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import json
import time
from datetime import datetime, timezone
from agents.langgraph_orchestrator import LangGraphOrchestrator

HITL_DECISIONS_PATH = '/workspace/shared/incident_agent/data/hitl_decisions.json'
HITL_QUEUE_PATH     = '/workspace/shared/incident_agent/data/hitl_queue.json'
PROCESSED_PATH      = '/workspace/shared/incident_agent/data/hitl_processed.json'

def load_processed():
    if os.path.exists(PROCESSED_PATH):
        with open(PROCESSED_PATH) as f:
            return set(json.load(f))
    return set()

def save_processed(processed):
    with open(PROCESSED_PATH, 'w') as f:
        json.dump(list(processed), f)

def load_decisions():
    if not os.path.exists(HITL_DECISIONS_PATH):
        return []
    try:
        with open(HITL_DECISIONS_PATH) as f:
            return json.load(f)
    except Exception:
        return []

def main():
    print("\n" + "=" * 60)
    print("  HITL PROCESSOR — Background Service")
    print("  Watching for human decisions from HITL Portal...")
    print("=" * 60)
    print(f"\n  Monitoring: {HITL_DECISIONS_PATH}")
    print(f"  Poll interval: 5 seconds")
    print(f"  Press Ctrl+C to stop\n")

    orchestrator = LangGraphOrchestrator()
    processed    = load_processed()
    tick         = 0

    while True:
        tick += 1
        decisions = load_decisions()
        new_decisions = [d for d in decisions
                         if d["incident_id"] not in processed]

        if new_decisions:
            for d in new_decisions:
                incident_id = d["incident_id"]
                print(f"\n{'─' * 60}")
                print(f"  [HITL Processor] New decision detected!")
                print(f"  Incident  : {incident_id}")
                print(f"  Decision  : {d['decision']}")
                print(f"  Notes     : {d.get('notes', 'None')}")
                print(f"  Fix       : {d.get('actual_fix', 'None')[:60]}")
                print(f"{'─' * 60}")

                config = {"configurable": {"thread_id": incident_id}}

                try:
                    final_state = orchestrator.resume_hitl(
                        config         = config,
                        human_decision = d["decision"],
                        human_notes    = d.get("notes", ""),
                        actual_fix     = d.get("actual_fix", ""),
                    )

                    status = final_state.get("status", "UNKNOWN")
                    print(f"\n  ✅ Pipeline resumed!")
                    print(f"  Status     : {status}")

                    if final_state.get("rem_result"):
                        rem = final_state["rem_result"]
                        print(f"  Remediation: {rem.get('status')} "
                              f"({rem.get('total_steps',0)} steps "
                              f"in {rem.get('total_time_sec',0)}s)")

                    if final_state.get("ticket"):
                        t = final_state["ticket"]
                        print(f"  Ticket     : {t.get('ticket_id')}")
                        print(f"  Priority   : {t.get('priority')}")
                        print(f"  MTTR       : {t.get('mttr_minutes',0)} mins")
                        print(f"  Resolution : {t.get('resolution','N/A')[:80]}")

                    if final_state.get("feedback_result"):
                        fb = final_state["feedback_result"]
                        print(f"  Feedback   : stored in ChromaDB ✅")

                except Exception as e:
                    print(f"  ❌ Error processing {incident_id}: {str(e)}")

                processed.add(incident_id)
                save_processed(processed)

        else:
            if tick % 12 == 0:
                print(f"  [{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                      f"Watching... ({len(processed)} decisions processed)")

        time.sleep(5)

if __name__ == "__main__":
    main()


==================================================
FILE: ./main_runner.py
==================================================
"""
Main Runner — Entry point for the Autonomous Incident Management System.
Provides three execution modes:
  --scenario SCENARIO_ID : Run a single specific incident scenario
  --all                  : Run all 5 incident scenarios sequentially
  --continuous           : Stream incidents continuously (for live demo)

Usage:
  python3 main_runner.py --scenario DB_CONN_EXHAUSTION
  python3 main_runner.py --all
  python3 main_runner.py --continuous --interval 30 --rate 0.4
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import json
import time
import argparse
import random
from datetime import datetime, timezone
from simulator.incident_simulator import generate_incident, generate_normal, INCIDENT_SCENARIOS
from agents.monitor_agent import MonitorAgent
from agents.langgraph_orchestrator import LangGraphOrchestrator

HITL_DECISIONS_PATH = '/workspace/shared/incident_agent/data/hitl_decisions.json'


class IncidentRunner:
    def __init__(self):
        print("\n" + "=" * 60)
        print("  AUTONOMOUS INCIDENT MANAGEMENT SYSTEM")
        print("  TCS & AMD AI Hackathon — AGENTS_026")
        print("=" * 60)
        self.monitor         = MonitorAgent()
        self.orchestrator    = LangGraphOrchestrator()
        self.hitl_configs    = {}
        self._processed_hitl = set()
        print("\n  ✅ System ready — all agents initialized")

    def process_incident(self, incident):
        incident_id = incident.get("incident_id", "UNKNOWN")
        scenario    = incident.get("scenario_id", "UNKNOWN")
        service     = incident.get("service", "unknown")

        print(f"\n{'─' * 60}")
        print(f"  📥 Incident : {incident_id}")
        print(f"  Scenario   : {scenario} | Service: {service}")
        print(f"{'─' * 60}")

        monitor_result = self.monitor.analyze(incident)

        if not monitor_result["is_anomaly"]:
            print(f"  ✅ Normal traffic — anomaly score: {monitor_result['anomaly_score']}")
            return {"status": "NORMAL", "incident_id": incident_id}

        print(f"  🚨 Anomaly detected — score: {monitor_result['anomaly_score']} | "
              f"threshold hits: {len(monitor_result['threshold_hits'])}")

        final_state, config = self.orchestrator.run(incident)
        self.hitl_configs[incident_id] = config

        status          = final_state.get("status", "UNKNOWN")
        decision_result = final_state.get("decision_result", {})
        decision_type   = decision_result.get("decision", "UNKNOWN") if decision_result else "UNKNOWN"

        print(f"\n  {'─' * 55}")
        print(f"  RESULT: {incident_id} → {status} | Decision: {decision_type}")

        if final_state.get("rca_result"):
            rca = final_state["rca_result"]
            print(f"  Root cause : {rca.get('root_cause', 'Unknown')[:80]}...")
            print(f"  Confidence : {rca.get('confidence', 0)}%")
            print(f"  Tokens     : {rca.get('token_count', 0)}")
            print(f"  LLM latency: {rca.get('llm_latency_ms', 0)}ms")

        if status == "RESOLVED" and final_state.get("rem_result"):
            rem = final_state["rem_result"]
            print(f"  Remediation: {rem.get('status')} ({rem.get('total_steps',0)} steps "
                  f"in {rem.get('total_time_sec',0)}s)")
            if final_state.get("ticket"):
                print(f"  MTTR       : {final_state['ticket'].get('mttr_minutes',0)} mins")

        elif decision_type == "HITL":
            print(f"  ⏸  Awaiting human review in HITL Portal")

        return {"status": status, "incident_id": incident_id}

    def check_hitl_decisions(self):
        if not os.path.exists(HITL_DECISIONS_PATH):
            return
        try:
            with open(HITL_DECISIONS_PATH, 'r') as f:
                decisions = json.load(f)
        except Exception:
            return

        for d in decisions:
            incident_id = d["incident_id"]
            if incident_id in self._processed_hitl:
                continue
            config = self.hitl_configs.get(incident_id)
            if not config:
                continue
            print(f"\n  [HITL] Processing: {incident_id} → {d['decision']}")
            final_state = self.orchestrator.resume_hitl(
                config         = config,
                human_decision = d["decision"],
                human_notes    = d.get("notes", ""),
                actual_fix     = d.get("actual_fix", ""),
            )
            print(f"  [HITL] Complete — status: {final_state.get('status')}")
            if final_state.get("rem_result"):
                rem = final_state["rem_result"]
                print(f"  [HITL] Remediation: {rem.get('status')} ({rem.get('total_steps',0)} steps)")
            self._processed_hitl.add(incident_id)

    def run_single(self, scenario_id=None):
        incident = generate_incident(scenario_id)
        return self.process_incident(incident)

    def run_all(self):
        print(f"\n  Mode: ALL SCENARIOS ({len(INCIDENT_SCENARIOS)} scenarios)")
        results = []
        for s in INCIDENT_SCENARIOS:
            print(f"\n{'=' * 60}")
            print(f"  Scenario: {s['id']}")
            result = self.process_incident(generate_incident(s["id"]))
            results.append(result)
            print(f"  Waiting 5 seconds...")
            time.sleep(5)
        print(f"\n{'=' * 60}")
        print(f"  ALL SCENARIOS SUMMARY")
        for r in results:
            icon = "✅" if r["status"] == "RESOLVED" else "⏸" if "HITL" in r["status"] else "ℹ️"
            print(f"  {icon} {r['incident_id']} → {r['status']}")
        return results

    def run_continuous(self, interval_secs=30, incident_rate=0.4):
        print(f"\n  Mode: CONTINUOUS | Interval: {interval_secs}s | Rate: {int(incident_rate*100)}%")
        print(f"  Press Ctrl+C to stop\n")
        tick = 0
        while True:
            tick += 1
            print(f"\n  [Tick {tick}] {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
            self.check_hitl_decisions()
            if random.random() < incident_rate:
                scenario = random.choice(INCIDENT_SCENARIOS)
                self.process_incident(generate_incident(scenario["id"]))
            else:
                result = self.monitor.analyze(generate_normal())
                print(f"  ✅ Normal traffic — anomaly score: {result['anomaly_score']}")
            print(f"  ⏱  Next check in {interval_secs}s...")
            time.sleep(interval_secs)


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Incident Management System — TCS & AMD AI Hackathon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 main_runner.py --scenario DB_CONN_EXHAUSTION
  python3 main_runner.py --all
  python3 main_runner.py --continuous --interval 20 --rate 0.5

Available scenarios:
  CPU_SPIKE, DB_CONN_EXHAUSTION, MEMORY_LEAK, DISK_FULL, SERVICE_CRASH
        """
    )
    parser.add_argument("--scenario",   type=str,   help="Run a specific incident scenario")
    parser.add_argument("--continuous", action="store_true", help="Continuous stream mode")
    parser.add_argument("--all",        action="store_true", help="Run all 5 scenarios")
    parser.add_argument("--interval",   type=int,   default=30)
    parser.add_argument("--rate",       type=float, default=0.4)
    args = parser.parse_args()

    if not any([args.scenario, args.continuous, args.all]):
        parser.print_help()
        sys.exit(0)

    runner = IncidentRunner()

    if args.scenario:
        valid = [s["id"] for s in INCIDENT_SCENARIOS]
        if args.scenario not in valid:
            print(f"  ❌ Unknown scenario: {args.scenario}")
            print(f"  Valid: {', '.join(valid)}")
            sys.exit(1)
        runner.run_single(args.scenario)
    elif args.all:
        runner.run_all()
    elif args.continuous:
        runner.run_continuous(interval_secs=args.interval, incident_rate=args.rate)


if __name__ == "__main__":
    main()


==================================================
FILE: ./rag/chroma_store.py
==================================================
"""
ChromaDB Store — Vector database for RAG knowledge base.
Stores and retrieves runbook sections and past incident feedback
using semantic similarity search. Uses section-based chunking
with 150-word chunks and 20-word overlap for optimal retrieval.
"""
import sys
sys.path.append('/workspace/shared/incident_agent')
import os
import re
import time
import json
import chromadb
from chromadb.utils import embedding_functions
from datetime import datetime, timezone

KNOWLEDGE_BASE_PATH = '/workspace/shared/incident_agent/rag/knowledge_base'
CHROMA_PATH         = '/workspace/shared/incident_agent/data/chroma_db'
COLLECTION_NAME     = 'incident_knowledge'

SECTION_HEADERS = [
    "DESCRIPTION",
    "SYMPTOMS",
    "LOG PATTERNS TO LOOK FOR",
    "COMMON ROOT CAUSES",
    "DIAGNOSTIC STEPS",
    "IMMEDIATE REMEDIATION STEPS",
    "PREVENTION",
    "ESCALATION",
]

def extract_metadata_from_header(content):
    metadata = {"incident_type": "UNKNOWN", "severity": "UNKNOWN", "service": "UNKNOWN"}
    for line in content.split('\n')[:10]:
        if line.startswith('RUNBOOK:'):
            metadata["incident_type"] = line.replace('RUNBOOK:', '').strip()
        elif 'Severity:' in line:
            metadata["severity"] = line.split(':')[1].strip()
        elif 'Service:' in line:
            metadata["service"] = line.split(':')[1].strip()
    return metadata

def chunk_runbook(content, doc_id):
    chunks       = []
    doc_meta     = extract_metadata_from_header(content)
    sections     = re.split(r'\n([A-Z][A-Z\s&/]+):\n', content)
    curr_section = "HEADER"
    chunk_index  = 0

    for part in sections:
        part = part.strip()
        if not part:
            continue
        if part in SECTION_HEADERS or part.isupper():
            curr_section = part
            continue
        if len(part) < 50:
            continue

        words      = part.split()
        chunk_size = 150
        overlap    = 20

        for j in range(0, len(words), chunk_size - overlap):
            chunk_words = words[j:j + chunk_size]
            chunk_text  = ' '.join(chunk_words)
            if len(chunk_text) < 50:
                continue
            chunk_id = f"{doc_id}__{curr_section.replace(' ', '_')}__chunk{chunk_index}"
            chunks.append({
                "id":   chunk_id,
                "text": f"[{doc_meta['incident_type']}] [{curr_section}]\n{chunk_text}",
                "metadata": {
                    "parent_doc":    doc_id,
                    "incident_type": doc_meta["incident_type"],
                    "severity":      doc_meta["severity"],
                    "service":       doc_meta["service"],
                    "section":       curr_section,
                    "chunk_index":   chunk_index,
                    "char_count":    len(chunk_text),
                    "word_count":    len(chunk_words),
                    "type":          "runbook_chunk",
                    "ingested_at":   datetime.now(timezone.utc).isoformat(),
                },
            })
            chunk_index += 1
    return chunks

class ChromaStore:
    def __init__(self):
        os.makedirs(CHROMA_PATH, exist_ok=True)
        self.client     = chromadb.PersistentClient(path=CHROMA_PATH)
        self.ef         = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )
        print(f"  [ChromaDB] Collection: {COLLECTION_NAME} | Chunks: {self.collection.count()}")

    def ingest_runbooks(self):
        start        = time.time()
        total_chunks = 0
        for fname in sorted(os.listdir(KNOWLEDGE_BASE_PATH)):
            if not fname.endswith('.txt'):
                continue
            doc_id = fname.replace('.txt', '')
            with open(os.path.join(KNOWLEDGE_BASE_PATH, fname), 'r') as f:
                content = f.read()
            new_chunks = 0
            for chunk in chunk_runbook(content, doc_id):
                if not self.collection.get(ids=[chunk["id"]])["ids"]:
                    self.collection.add(
                        ids=[chunk["id"]],
                        documents=[chunk["text"]],
                        metadatas=[chunk["metadata"]],
                    )
                    new_chunks += 1
            total_chunks += new_chunks

        metadata = {
            "total_chunks":    self.collection.count(),
            "embedding_model": "all-MiniLM-L6-v2 (ONNX)",
            "chunk_strategy":  "section-based, 150 words, 20 word overlap",
            "ingested_at":     datetime.now(timezone.utc).isoformat(),
        }
        json.dump(metadata, open(
            '/workspace/shared/incident_agent/data/chroma_metadata.json', 'w'
        ), indent=2)
        return total_chunks

    def search(self, query, n_results=5, section_filter=None):
        count = self.collection.count()
        if count == 0:
            return []
        where   = {"section": section_filter} if section_filter else None
        start   = time.time()
        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, count),
            where=where,
        )
        latency = round((time.time() - start) * 1000, 2)
        docs = []
        for i in range(len(results['ids'][0])):
            docs.append({
                "id":         results['ids'][0][i],
                "document":   results['documents'][0][i],
                "metadata":   results['metadatas'][0][i],
                "distance":   round(results['distances'][0][i], 4),
                "latency_ms": latency,
            })
        return sorted(docs, key=lambda x: x['distance'])

    def search_for_rca(self, incident_type, metrics, logs):
        log_text    = ' '.join(logs[:3]) if logs else ''
        metric_text = ' '.join([f"{k}:{v}" for k, v in metrics.items()])
        query       = f"{incident_type} {metric_text} {log_text}"
        return {
            "remediation_chunks": self.search(query, n_results=3,
                                              section_filter="IMMEDIATE REMEDIATION STEPS"),
            "root_cause_chunks":  self.search(query, n_results=3,
                                              section_filter="COMMON ROOT CAUSES"),
            "symptom_chunks":     self.search(query, n_results=2,
                                              section_filter="SYMPTOMS"),
            "query_used":         query,
        }

    def add_feedback(self, incident_id, root_cause, fix_applied,
                     outcome, human_notes, service):
        doc_id  = f"feedback_{incident_id}"
        content = (
            f"[PAST INCIDENT FEEDBACK] [IMMEDIATE REMEDIATION STEPS]\n"
            f"Incident ID  : {incident_id}\n"
            f"Service      : {service}\n"
            f"Root Cause   : {root_cause}\n"
            f"Fix Applied  : {fix_applied}\n"
            f"Outcome      : {outcome}\n"
            f"Human Notes  : {human_notes}\n"
            f"Recorded At  : {datetime.now(timezone.utc).isoformat()}"
        )
        self.collection.add(
            ids=[doc_id],
            documents=[content],
            metadatas=[{
                "type":        "feedback",
                "incident_id": incident_id,
                "service":     service,
                "outcome":     outcome,
                "section":     "IMMEDIATE REMEDIATION STEPS",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }],
        )
        return doc_id


==================================================
FILE: ./scripts/patch_prometheus.py
==================================================
import sys

path = sys.argv[1]
content = open(path).read()

old = "        route_name = route.path"
marker = 'getattr(route, "path", None)'

if marker in content:
    print("    prometheus_fastapi_instrumentator already patched")
elif old in content:
    new = '''        route_name = getattr(route, "path", None)
        if route_name is None:
            continue'''
    content = content.replace(old, new)
    open(path, 'w').write(content)
    print("    Patched prometheus_fastapi_instrumentator routing bug")
else:
    print("    Warning: prometheus routing pattern not found - skipping patch")


==================================================
FILE: ./setup.sh
==================================================
#!/bin/bash
echo "=== Restoring environment ==="
pip install chromadb langchain langchain-community langchain-core \
  sentence-transformers streamlit scikit-learn pandas numpy \
  prometheus-client python-dotenv httpx pydantic colorlog \
  langgraph --ignore-installed blinker --quiet
echo "=== All packages ready ==="

# Reinstall Ollama binary if missing
if ! command -v ollama &> /dev/null; then
    echo "=== Reinstalling Ollama ==="
    apt-get update -q && apt-get install -y zstd
    curl -fsSL https://ollama.com/install.sh | sh
    echo "=== Ollama reinstalled ==="
else
    echo "=== Ollama already installed ==="
fi

# Start Ollama server if not running
echo "=== Starting Ollama server ==="
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "=== Ollama already running ==="
else
    ollama serve > /workspace/shared/ollama.log 2>&1 &
    sleep 5
    echo "=== Ollama server started ==="
fi

# Pull model if not available
echo "=== Checking LLM model ==="
if ! ollama list | grep -q "qwen2.5:32b"; then
    echo "=== Pulling qwen2.5:32b ==="
    ollama pull qwen2.5:32b
    echo "=== Model ready ==="
else
    echo "=== qwen2.5:32b already available ==="
fi

# Start HITL Portal
echo "=== Starting HITL Portal ==="
pkill -f "streamlit run hitl" 2>/dev/null
sleep 2
cd /workspace/shared/incident_agent
mkdir -p ~/.streamlit
cat > ~/.streamlit/config.toml << 'TOML'
[server]
port = 8501
address = "0.0.0.0"
enableCORS = false
enableXsrfProtection = false
headless = true

[browser]
serverAddress = "notebooks.amd.com"
serverPort = 443
gatherUsageStats = false
TOML
streamlit run hitl/hitl_portal.py > /workspace/shared/hitl_portal.log 2>&1 &
sleep 3
echo "=== HITL Portal started ==="

# Start Dashboard
echo "=== Starting Dashboard ==="
pkill -f "streamlit run dashboard" 2>/dev/null
sleep 2
streamlit run dashboard.py --server.port 8502 > /workspace/shared/dashboard.log 2>&1 &
sleep 3
echo "=== Dashboard started ==="

# Start vLLM server (optional - alternative high-throughput LLM backend)
# Uses /root (overlay, non-persistent) for venv + HF cache to avoid
# consuming the limited /workspace/shared persistent quota.
echo ""
echo "=== Starting vLLM server (Qwen2.5-7B-Instruct) ==="

VLLM_ENV="/root/vllm_env"
HF_CACHE_DIR="/root/hf_cache"
VLLM_MODEL="Qwen/Qwen2.5-7B-Instruct"
VLLM_LOG="/root/vllm_server.log"

if [ ! -d "$VLLM_ENV" ]; then
    echo "=== Creating vLLM venv (overlay, ~5-7 min) ==="
    python3 -m venv "$VLLM_ENV"
    source "$VLLM_ENV/bin/activate"
    pip install --upgrade pip --quiet
    pip install vllm==0.15.0+rocm700 --extra-index-url https://wheels.vllm.ai/rocm/0.15.0/rocm700 --quiet
    deactivate
    echo "=== vLLM venv created ==="
fi

if ! ldconfig -p | grep -q libmpi_cxx; then
    echo "=== Installing OpenMPI runtime ==="
    apt-get update -q && apt-get install -y libopenmpi-dev openmpi-bin --quiet
fi

PROM_ROUTING="$VLLM_ENV/lib/python3.12/site-packages/prometheus_fastapi_instrumentator/routing.py"
if [ -f "$PROM_ROUTING" ]; then
    python3 /workspace/shared/incident_agent/scripts/patch_prometheus.py "$PROM_ROUTING"
fi

mkdir -p "$HF_CACHE_DIR"
export HF_HOME="$HF_CACHE_DIR"
export HUGGINGFACE_HUB_CACHE="$HF_CACHE_DIR"

if curl -s http://localhost:8000/v1/models > /dev/null 2>&1; then
    echo "=== vLLM server already running ==="
else
    source "$VLLM_ENV/bin/activate"
    setsid nohup vllm serve "$VLLM_MODEL" \
      --port 8000 \
      --dtype auto \
      --max-model-len 8192 \
      --gpu-memory-utilization 0.85 \
      > "$VLLM_LOG" 2>&1 < /dev/null &
    disown
    deactivate
    echo "=== vLLM server starting in background (PID: $!) ==="
    echo "    Model: $VLLM_MODEL (downloads to overlay on first run, ~15GB)"
    echo "    Check status: tail -f $VLLM_LOG"
    echo "    Or test: curl http://localhost:8000/v1/models"
fi

# Verify everything
echo ""
echo "========================================"
echo "=== Environment Status ==="
python3 -c "import chromadb, langchain, streamlit, sklearn, langgraph; print('Packages: OK')"
echo "Ollama model: $(ollama list | grep qwen2.5 | awk '{print $1}')"
echo "HITL Portal: http://localhost:8501"
echo "Dashboard  : http://localhost:8502"
echo ""
echo "=== HITL Portal URL ==="
INSTANCE=$(hostname)
echo "https://notebooks.amd.com/${INSTANCE}/proxy/8501/"
echo ""
echo "=== Dashboard URL ==="
echo "https://notebooks.amd.com/${INSTANCE}/proxy/8502/"
echo "========================================"
echo "=== Ready to go! ==="
echo ""
echo "To run incidents:"
echo "  Option 1: Use the Dashboard Control Panel (recommended)"
echo "  Option 2: Run manually via terminal:"
echo "    cd /workspace/shared/incident_agent"
echo "    python3 main_runner.py --scenario DB_CONN_EXHAUSTION"
echo "    python3 main_runner.py --continuous"
echo "    python3 main_runner.py --all"
echo ""
echo "IMPORTANT: For HITL approvals to auto-trigger remediation, run in a"
echo "separate terminal:"
echo "  python3 hitl_processor.py"
echo ""
echo "vLLM server (alternative high-throughput LLM backend, Qwen2.5-7B):"
echo "  Status : curl http://localhost:8000/v1/models"
echo "  Logs   : tail -f /root/vllm_server.log"
echo "  Note   : venv + model cache on overlay (/root) - not persistent,"
echo "           reinstalls/redownloads each session (~5-10 min total)"


==================================================
FILE: ./simulator/incident_simulator.py
==================================================
import random
import json
from datetime import datetime, timezone

INCIDENT_SCENARIOS = [
    {"id": "CPU_SPIKE", "name": "CPU Spike - Runaway Process", "severity": "HIGH", "service": "compute-service",
     "symptoms": {"cpu_usage": (88,99), "memory_usage": (60,75), "error_rate": (2,8), "latency_ms": (800,2000), "disk_io": (20,40)},
     "logs": ["WARN  [ProcessManager] High CPU detected on worker-3: 94.2%",
              "ERROR [Scheduler] Job queue backlog growing: 342 pending tasks"]},
    {"id": "DB_CONN_EXHAUSTION", "name": "Database Connection Pool Exhausted", "severity": "CRITICAL", "service": "database-service",
     "symptoms": {"cpu_usage": (40,60), "memory_usage": (70,85), "error_rate": (15,35), "latency_ms": (3000,8000), "disk_io": (60,90)},
     "logs": ["ERROR [DatabasePool] Connection pool exhausted - waiting for connection",
              "ERROR [API] Request timeout after 5000ms - DB unavailable",
              "WARN  [ConnectionManager] Active connections: 100/100"]},
    {"id": "MEMORY_LEAK", "name": "Memory Leak - Gradual Exhaustion", "severity": "HIGH", "service": "app-service",
     "symptoms": {"cpu_usage": (45,65), "memory_usage": (88,98), "error_rate": (5,15), "latency_ms": (1500,4000), "disk_io": (10,25)},
     "logs": ["WARN  [MemoryManager] Heap usage at 92.3% - GC pressure increasing",
              "ERROR [JVM] OutOfMemoryError in svc-worker - heap space"]},
    {"id": "DISK_FULL", "name": "Disk Space Critical", "severity": "CRITICAL", "service": "storage-service",
     "symptoms": {"cpu_usage": (30,50), "memory_usage": (50,70), "error_rate": (10,25), "latency_ms": (2000,6000), "disk_io": (90,99)},
     "logs": ["ERROR [StorageManager] Disk usage at 96.5% on volume /data",
              "ERROR [Logger] Failed to write log - no space left on device"]},
    {"id": "SERVICE_CRASH", "name": "Microservice Crash Loop", "severity": "CRITICAL", "service": "orchestration",
     "symptoms": {"cpu_usage": (10,30), "memory_usage": (20,40), "error_rate": (40,80), "latency_ms": (100,300), "disk_io": (5,20)},
     "logs": ["ERROR [Kubernetes] Pod svc-auth-7d9f CrashLoopBackOff - restarts: 18",
              "ERROR [HealthCheck] Service svc-auth health check failed 5 times"]},
]

NORMAL = {"cpu_usage": (15,45), "memory_usage": (30,55), "error_rate": (0,1.5), "latency_ms": (50,200), "disk_io": (5,20)}

def get_metrics(ranges):
    return {k: round(random.uniform(v[0], v[1]), 2) for k, v in ranges.items()}

def generate_incident(scenario_id=None):
    if scenario_id:
        scenario = next((s for s in INCIDENT_SCENARIOS if s["id"] == scenario_id), None)
    else:
        scenario = random.choice(INCIDENT_SCENARIOS)
    return {
        "incident_id": f"INC-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "severity": scenario["severity"],
        "service": scenario["service"],
        "metrics": get_metrics(scenario["symptoms"]),
        "baseline_metrics": get_metrics(NORMAL),
        "logs": scenario["logs"],
    }

def generate_normal():
    return {
        "incident_id": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenario_id": "NORMAL",
        "scenario_name": "Normal Operation",
        "severity": "NONE",
        "service": "all-services",
        "metrics": get_metrics(NORMAL),
        "baseline_metrics": get_metrics(NORMAL),
        "logs": [],
    }

if __name__ == "__main__":
    print("=" * 55)
    print("  INCIDENT SIMULATOR - TEST")
    print("=" * 55)

    print("\n--- Normal metrics sample ---")
    n = generate_normal()
    print(json.dumps(n["metrics"], indent=2))

    print("\n--- All incident scenarios ---")
    for s in INCIDENT_SCENARIOS:
        inc = generate_incident(s["id"])
        print(f"[{inc['severity']:8s}] {inc['scenario_id']:25s} | CPU:{inc['metrics']['cpu_usage']}% | ERR:{inc['metrics']['error_rate']}%")

    print("\n--- Sample DB incident ---")
    inc = generate_incident("DB_CONN_EXHAUSTION")
    print(f"ID       : {inc['incident_id']}")
    print(f"Scenario : {inc['scenario_name']}")
    print(f"Severity : {inc['severity']}")
    print(f"Service  : {inc['service']}")
    print(f"Metrics  : {inc['metrics']}")
    print(f"Log[0]   : {inc['logs'][0]}")

    print("\n✅ Simulator ready!")


