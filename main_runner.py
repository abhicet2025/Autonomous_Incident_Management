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
