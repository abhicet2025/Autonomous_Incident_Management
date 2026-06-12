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
