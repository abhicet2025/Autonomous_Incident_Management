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
