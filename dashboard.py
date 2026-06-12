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
