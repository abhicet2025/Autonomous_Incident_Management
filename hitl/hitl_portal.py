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
