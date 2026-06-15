# AGENTS.md

This document describes each agent in the **Autonomous Incident Diagnosis & Resolution** system — what it does, how it is implemented, its inputs/outputs, and the exact configuration/parameters used in code. It is scoped strictly to the agents themselves (`agents/` directory), not the overall system architecture, dashboard, or setup instructions (see `TECHNICAL_DOCUMENTATION.md` for that).

All agents are plain Python classes, instantiated once by the LangGraph orchestrator (`agents/langgraph_orchestrator.py`) and called as pipeline nodes in sequence:

```
MonitorAgent → MLScorerAgent → RCAAgent → DecisionAgent → [HITL | RemediationAgent] → ITSMAgent → FeedbackAgent
```

---

## 1. MonitorAgent

**File:** `agents/monitor_agent.py`
**Class:** `MonitorAgent`
**Entry point:** `analyze(data)`

### Purpose
Tier-1, LLM-free anomaly detector. Runs on every incoming telemetry sample and decides whether the pipeline should proceed further.

### Detection methods (all three run on every call)

1. **Threshold checks** (`_check_thresholds`)
   Compares each metric against fixed `WARN`/`CRITICAL` thresholds:

   | Metric | Warn | Critical |
   |---|---|---|
   | `cpu_usage` | 70 | 85 |
   | `memory_usage` | 75 | 90 |
   | `error_rate` | 5 | 10 |
   | `latency_ms` | 1000 | 2000 |
   | `disk_io` | 75 | 90 |

2. **Z-score statistical check** (`_check_zscore`)
   Maintains a rolling history window of the last **10** samples (`self.window = 10`). For each metric with ≥3 historical values, computes `zscore = |(value - mean) / stdev|`. Flags the metric if `zscore > 2.0`.

3. **Log keyword scan** (`_check_logs`)
   Scans incoming log lines (case-insensitive) for any of: `ERROR`, `CrashLoop`, `exhausted`, `OutOfMemory`, `timeout`, `failed`, `CRITICAL`, `no space`.

### Scoring
- `total_rules` = count of threshold hits + Z-score hits + log keyword hits
- `anomaly_score = min(100, total_rules * 20)`
- `is_anomaly = total_rules > 0`

### Output (dict)
```
incident_id, timestamp, is_anomaly, anomaly_score,
threshold_hits, zscore_hits, log_hits,
metrics, logs, service, raw_data
```

### Routing impact
In the orchestrator, `route_after_monitor` ends the pipeline (`END`) immediately if `is_anomaly` is `False` — no further agents run for normal traffic.

---

## 2. MLScorerAgent

**File:** `agents/ml_scorer_agent.py`
**Class:** `MLScorerAgent`
**Entry point:** `score(monitor_result)`

### Purpose
Tier-2 unsupervised severity scorer using an Isolation Forest, trained on synthetic "normal" telemetry.

### Model
- **Algorithm:** `sklearn.ensemble.IsolationForest`
- **Hyperparameters:** `n_estimators=100`, `contamination=0.05`, `random_state=42`
- **Features used (`FEATURE_KEYS`):** `cpu_usage`, `memory_usage`, `error_rate`, `latency_ms`, `disk_io`
- **Training data:** 200 synthetic normal samples generated via `simulator.incident_simulator.generate_normal()`
- **Persistence:**
  - Model → `data/ml_model.pkl` (via `joblib`)
  - Metadata → `data/ml_model_metadata.json`
  - Training dataset → `data/ml_training_data.csv`
- On instantiation: loads the persisted model if present, otherwise trains it on the spot (`_train`, lazy one-time training, ~0.44s recorded).
- GPU memory is captured before/after training via `rocm-smi --showmeminfo vram --csv` and stored in metadata (`gpu_hardware: "AMD MI300X via ROCm"`).

### Scoring logic
1. Convert incoming metrics to a feature vector via `metrics_to_vector()`.
2. Run `self.model.decision_function(vector)` → `raw_score`.
3. Normalize: `normalized = clamp(0.5 - raw_score, 0.0, 1.0)`.
4. Map normalized score to severity via `SEVERITY_MAP` (checked in order):

   | Threshold (≥) | Severity |
   |---|---|
   | 0.55 | CRITICAL |
   | 0.45 | HIGH |
   | 0.30 | MEDIUM |
   | 0.00 | LOW |

5. If `monitor_result["is_anomaly"]` is `False`, severity is forced to `NONE` and `normalized = 0.0`.

### Output (dict)
```
incident_id, timestamp, severity, confidence (0–1, normalized score),
anomaly_score (0–100), is_anomaly, metrics, threshold_hits, log_hits,
logs, service, raw_data, scoring_latency_ms
```

### Performance
- Scoring latency: ~6–9 ms (measured via `time.time()` around `decision_function`)

---

## 3. RCAAgent (Root Cause Analysis)

**File:** `agents/rca_agent.py`
**Class:** `RCAAgent`
**Entry point:** `analyze(scorer_result)`

### Purpose
Tier-3 agent — combines RAG retrieval over runbooks/feedback with an LLM call to produce a structured root cause, ranked fixes, confidence, classification, and estimated resolution time.

### Dependencies
- `rag.chroma_store.ChromaStore` (instantiated in `__init__`)

### LLM configuration
| Setting | Value |
|---|---|
| Backend | Ollama, `http://localhost:11434/api/chat` |
| Model | `qwen2.5:32b` |
| `temperature` | 0.1 |
| `num_predict` (max tokens) | 1000 |
| `stream` | `False` |

### System prompt (verbatim role definition)
The agent is framed as an expert SRE that must return root cause, ranked fix steps, a confidence score (0–100%), and an estimated resolution time, **strictly as valid JSON**.

### Pipeline within `analyze()`
1. **RAG retrieval** — calls `self.chroma.search_for_rca(incident_type=severity, metrics=metrics, logs=logs)`, which returns:
   - `remediation_chunks` (top 3, filtered to section `IMMEDIATE REMEDIATION STEPS`)
   - `root_cause_chunks` (top 3, filtered to section `COMMON ROOT CAUSES`)
   - `symptom_chunks` (top 2, filtered to section `SYMPTOMS`)
   - `query_used`
2. **Prompt construction** (`_build_prompt`) — assembles:
   - Service name and severity
   - Metric anomalies (key: value list)
   - Threshold violations (metric, value, level)
   - Up to 5 error log lines
   - Top-2 retrieved root-cause and remediation chunks (truncated to 300 chars each)
   - A strict JSON response schema with confidence-scoring guidance:
     - 90–100: exact runbook match, clear root cause
     - 70–89: mostly matches, likely root cause
     - 50–69: partial match, possible root cause
     - <50: unclear, needs investigation
3. **LLM call** (`_call_ollama`) — POSTs to Ollama's `/api/chat`, 120s timeout. On exception, returns a graceful fallback JSON (`root_cause: "LLM unavailable: ..."`, `confidence: 0`, `incident_type: "UNKNOWN"`).
4. **Response parsing** (`_parse_response`) — extracts the JSON object between the first `{` and last `}` in the LLM output; falls back to a generic "manual investigation required" object if parsing fails.

### Expected LLM JSON schema
```json
{
  "root_cause": "one clear sentence",
  "recommendations": ["Step 1: ...", "Step 2: ...", "Step 3: ..."],
  "confidence": 0-100,
  "resolution_time": "<estimate>",
  "incident_type": "CPU_SPIKE|DB_CONN_EXHAUSTION|MEMORY_LEAK|DISK_FULL|SERVICE_CRASH"
}
```

### Output (dict)
```
incident_id, timestamp, service, severity, root_cause, recommendations,
confidence, resolution_time, incident_type, rag_results, metrics, logs,
llm_model, token_count, llm_latency_ms, rag_latency_ms, total_latency_ms, raw_data
```

### Measured performance (recorded in build)
- LLM latency: ~6–7 s
- Token count: ~850–950 per incident
- RAG retrieval latency: ~5–8 s (first call; faster warm)

---

## 4. DecisionAgent

**File:** `agents/decision_agent.py`
**Class:** `DecisionAgent`
**Entry point:** `route(rca_result)`

### Purpose
Central routing logic — combines ML severity and LLM confidence into a single AUTO/HITL decision, risk level, and (for AUTO) a concrete remediation action.

### Confidence combination
```python
ml_confidence  = rca_result["raw_data"]["confidence"] * 100   # from MLScorerAgent (0-1 → 0-100)
llm_confidence = rca_result["confidence"]                       # from RCAAgent (0-100)
confidence     = round((ml_confidence + llm_confidence) / 2, 1)
```
`CONFIDENCE_THRESHOLD = 85`

### Routing rules (`ROUTING_RULES`)

| Severity | Default | If confidence ≥ 85% | Reason |
|---|---|---|---|
| CRITICAL | HITL | HITL (always) | "CRITICAL severity always requires human approval" |
| HIGH | HITL | AUTO | "HIGH severity routed based on confidence score" |
| MEDIUM | AUTO | AUTO | "MEDIUM severity auto-remediated" |
| LOW | AUTO | AUTO | "LOW severity always auto-remediated" |
| NONE | AUTO | AUTO | "No anomaly detected" |

### Risk level mapping (`RISK_MAP`)

| Severity | Risk Level |
|---|---|
| CRITICAL | HIGH |
| HIGH | MEDIUM |
| MEDIUM | LOW |
| LOW | LOW |
| NONE | LOW |

### Auto-action mapping (`AUTO_ACTION_MAP`) — used when decision = AUTO

| Incident Type | Auto Action |
|---|---|
| CPU_SPIKE | `restart_high_cpu_processes` |
| DB_CONN_EXHAUSTION | `restart_connection_pool` |
| MEMORY_LEAK | `restart_service_instance` |
| DISK_FULL | `cleanup_old_logs` |
| SERVICE_CRASH | `rollback_deployment` |

### Output (dict)
```
incident_id, timestamp, decision (AUTO|HITL), reason, risk_level, auto_action,
service, severity, root_cause, recommendations, confidence, resolution_time,
metrics, logs, latency_ms, rca_result,
factors: { severity, ml_confidence, llm_confidence, combined_confidence,
           confidence_threshold, risk_level }
```

---

## 5. HITL (Human-in-the-Loop) — orchestrator node + Streamlit portal

**Files:** `agents/langgraph_orchestrator.py` (`hitl_node`, `route_after_hitl`), `hitl/hitl_portal.py`, `hitl_processor.py`

### Purpose
Pauses the pipeline for human review when `DecisionAgent` returns `decision == "HITL"`.

### Mechanism
- LangGraph is compiled with `interrupt_before=["hitl"]`, so execution halts before the `hitl` node runs.
- The orchestrator persists the full pipeline state to `data/hitl_state_{incident_id}.json` and adds the incident to the HITL queue (`data/hitl_queue.json`).
- `hitl_node` itself simply records whatever `human_decision`, `human_notes`, and `actual_fix` are present in state (default `"PENDING"`) via `update_active_incident`.
- `route_after_hitl`: if `human_decision == "PENDING"` → `END` (pipeline stays paused); otherwise → `remediation`.

### HITL Portal (`hitl/hitl_portal.py`, Streamlit, port 8501)
- Reads `HITL_QUEUE_PATH` / `HITL_DECISIONS_PATH` (`data/hitl_queue.json`, `data/hitl_decisions.json`).
- Displays incident details, AI confidence, root cause, metrics, logs, and recommendations for each queued incident.
- Human actions: **Approve** (optionally edit/override the fix via `actual_fix`), **Reject**, with free-text `human_notes`.

### HITL Processor (`hitl_processor.py`)
- Background loop that watches `data/hitl_decisions.json` for new human decisions.
- On a decision, loads `data/hitl_state_{incident_id}.json`, sets `human_decision` / `human_notes` / `actual_fix` in the saved state, and resumes the LangGraph run via `orchestrator.resume_hitl(config, human_decision, human_notes, actual_fix)`.
- Resumption re-enters the graph at `remediation` (if approved) or ends at `REJECTED` (handled inside `remediation_node`).

---

## 6. RemediationAgent

**File:** `agents/remediation_agent.py`
**Class:** `RemediationAgent`
**Entry point:** `execute(decision_result)`

### Purpose
Executes a predefined, incident-type-specific playbook of sequential remediation steps.

### Guard conditions
- If `decision` is not `"AUTO"` or `"APPROVED"` → returns `{"status": "SKIPPED", ...}`.
- If no playbook exists for the `incident_type` → returns `{"status": "NO_PLAYBOOK", ...}`.

### Playbooks (`PLAYBOOKS` dict — 5 incident types, 5–6 steps each)

| Incident Type | Steps (action → description, duration in seconds) |
|---|---|
| **CPU_SPIKE** | identify_runaway_process (1.0) → kill_runaway_process (1.5) → enable_circuit_breaker (0.5) → scale_compute_nodes (2.0) → verify_cpu_normalized (1.0) |
| **DB_CONN_EXHAUSTION** | kill_long_running_queries (1.5) → restart_connection_pool (2.0) → scale_db_replicas (2.5) → increase_pool_size (0.5) → enable_query_cache (0.5) → verify_connections_normal (1.0) |
| **MEMORY_LEAK** | capture_heap_dump (2.0) → restart_service_instance (2.0) → increase_heap_size (0.5) → enable_gc_logging (0.5) → schedule_rolling_restart (0.5) → verify_memory_normalized (1.0) |
| **DISK_FULL** | delete_old_logs (2.0) → clear_temp_files (1.0) → archive_old_data (2.5) → clear_docker_images (1.5) → setup_log_rotation (0.5) → verify_disk_normalized (1.0) |
| **SERVICE_CRASH** | check_exit_code (0.5) → review_crash_logs (1.0) → rollback_deployment (3.0) → verify_health_check (1.5) → notify_dependent_services (0.5) → verify_service_stable (2.0) |

### Execution
Each step is executed sequentially with a real `time.sleep(duration)` to simulate realistic remediation timing, and recorded with `step`, `action`, `description`, `status: "SUCCESS"`, `duration_sec`.

### Output (dict)
```
incident_id, timestamp, incident_type, service, status (SUCCESS|SKIPPED|NO_PLAYBOOK),
total_steps, actions_taken[], total_time_sec, decision
```

---

## 7. ITSMAgent

**File:** `agents/itsm_agent.py`
**Class:** `ITSMAgent`
**Entry points:** `create_ticket()`, `update_ticket()`, `close_ticket()`

### Purpose
Simulates an ITSM system (ServiceNow/Jira-style) ticket lifecycle with full timeline and MTTR calculation. Persists each ticket as JSON under `data/tickets/{incident_id}.json`.

### Priority mapping (`PRIORITY_MAP`)

| Severity | Priority | SLA (minutes) | Team |
|---|---|---|---|
| CRITICAL | P1 | 30 | Platform-SRE |
| HIGH | P2 | 120 | App-SRE |
| MEDIUM | P3 | 480 | DevOps |
| LOW | P4 | 1440 | DevOps |
| NONE | P5 | 2880 | DevOps |

### Service → team mapping (`SERVICE_TEAM_MAP`)

| Service | Team |
|---|---|
| database-service | DBA-Team |
| compute-service | Platform-Team |
| app-service | App-Team |
| storage-service | Storage-Team |
| orchestration | Platform-Team |
| all-services | SRE-Team |
| (other/unknown) | SRE-Team (default) |

### Lifecycle
1. **`create_ticket(decision_result)`** — builds a ticket with `status: "OPEN"`, priority/team from the maps above, embeds `root_cause`, `recommendations`, `metrics`, `ai_confidence`, `decision`, and an initial timeline entry (`TICKET_CREATED`, actor `AutoDetection`).
2. **`update_ticket(incident_id, status, details, actor)`** — appends a timeline event (`STATUS_CHANGED_TO_{status}`), e.g. moving to `IN_PROGRESS` with the RCA root cause attached, actor `RCAAgent`.
3. **`close_ticket(incident_id, resolution, remediation_result)`** — sets `status: "RESOLVED"`, computes:
   ```python
   mttr_minutes = (now - created_at).total_seconds() / 60
   ```
   appends a `TICKET_RESOLVED` timeline event (actor `AutoRemediation`) including `mttr_minutes`.

### Output
Full ticket JSON dict: `ticket_id, created_at, updated_at, title, description, priority, severity, status, service, assigned_team, sla_minutes, ai_confidence, decision, metrics, recommendations, timeline[], resolution, closed_at, mttr_minutes`

---

## 8. FeedbackAgent

**File:** `agents/feedback_agent.py`
**Class:** `FeedbackAgent`
**Entry points:** `ingest_auto_resolution()`, `ingest_human_resolution()`, `get_statistics()`

### Purpose
Closes the continuous-learning loop by writing every resolution outcome (AUTO or human-reviewed) into ChromaDB and a local JSON log (`data/feedback_log.json`), so future RCA retrieval benefits from real outcomes.

### Dependencies
- `rag.chroma_store.ChromaStore` — calls `chroma.add_feedback(...)`

### `ingest_auto_resolution(decision_result, remediation_result, ticket)`
- Builds `fix_applied` by joining all `actions_taken[].description` with `" → "`.
- Calls `chroma.add_feedback(incident_id, root_cause, fix_applied, outcome=f"AUTO-RESOLVED in {mttr} minutes", human_notes="Automatic remediation successful. N steps executed.", service)`.
- Appends a record with `decision_type: "AUTO"`, `outcome: "RESOLVED"`, `mttr_minutes`, `chroma_doc_id` to `feedback_log.json`.

### `ingest_human_resolution(decision_result, human_decision, human_notes, actual_fix, outcome, mttr)`
- Calls `chroma.add_feedback(incident_id, root_cause, fix_applied=actual_fix, outcome=f"{human_decision}: {outcome}", human_notes, service)`.
- Appends a record with `decision_type: "HUMAN"`, `human_decision`, `human_notes`, `outcome`, `mttr_minutes`, `chroma_doc_id`.

### `get_statistics()`
Aggregates `feedback_log.json` into:
```
total_incidents, auto_resolved, human_resolved, avg_mttr_minutes,
by_incident_type (dict), knowledge_base_size (= chroma.collection.count())
```

---

## 9. StatusWriter (shared utility used by all agents via the orchestrator)

**File:** `agents/status_writer.py`
**Functions:** `update_active_incident()`, `complete_incident()`, `load_status()`, `save_status()`, `load_history()`, `save_history()`

### Purpose
Not an "agent" in the LLM sense, but the shared live-status mechanism every orchestrator node calls into. Writes to:
- `data/dashboard_status.json` — currently active incidents and their per-agent step results (`active[incident_id].steps[agent_step] = data`)
- `data/dashboard_history.json` — last 100 completed incidents (`complete_incident` moves an entry from active → history with `final_status` and `completed_at`)

This is what powers the Dashboard's "Live Monitor" (per-agent progress tracker) and "History" tabs.

---

## 10. ChromaStore (RAG layer used by RCAAgent and FeedbackAgent)

**File:** `rag/chroma_store.py`
**Class:** `ChromaStore`

While not an "agent" in the orchestration graph, this is the shared retrieval component both `RCAAgent` and `FeedbackAgent` depend on, so it's documented here for completeness.

### Configuration
- **Vector DB:** ChromaDB `PersistentClient`, path `data/chroma_db`
- **Collection:** `incident_knowledge`, `hnsw:space = "cosine"`
- **Embedding function:** `chromadb.utils.embedding_functions.DefaultEmbeddingFunction()` → all-MiniLM-L6-v2 (ONNX)

### Ingestion (`ingest_runbooks`)
- Reads all `.txt` files from `rag/knowledge_base/`.
- Each runbook is chunked by `chunk_runbook()`:
  - Splits on ALL-CAPS section headers matching `SECTION_HEADERS` (`DESCRIPTION`, `SYMPTOMS`, `LOG PATTERNS TO LOOK FOR`, `COMMON ROOT CAUSES`, `DIAGNOSTIC STEPS`, `IMMEDIATE REMEDIATION STEPS`, `PREVENTION`, `ESCALATION`).
  - Within each section, splits into **150-word chunks with 20-word overlap** (chunks <50 chars discarded).
  - Each chunk is prefixed with `[incident_type] [section]` and tagged with metadata: `parent_doc, incident_type, severity, service, section, chunk_index, char_count, word_count, type="runbook_chunk", ingested_at`.
- Deduplicates by chunk ID before adding to the collection.

### Retrieval (`search`, `search_for_rca`)
- `search(query, n_results, section_filter)` — semantic similarity query, optionally filtered by `section` metadata, results sorted by distance.
- `search_for_rca(incident_type, metrics, logs)` — builds a single query string from incident type + metric key:value pairs + first 3 log lines, then runs three filtered searches:
  - 3 results from `IMMEDIATE REMEDIATION STEPS`
  - 3 results from `COMMON ROOT CAUSES`
  - 2 results from `SYMPTOMS`

### Feedback ingestion (`add_feedback`)
- Writes a synthetic document tagged `[PAST INCIDENT FEEDBACK] [IMMEDIATE REMEDIATION STEPS]` containing incident ID, service, root cause, fix applied, outcome, human notes, and timestamp — with `metadata.type = "feedback"`. This makes past resolutions retrievable by the same `IMMEDIATE REMEDIATION STEPS` filter used in RCA, directly improving future retrieval.

---

## 11. Orchestration Summary (LangGraphOrchestrator)

**File:** `agents/langgraph_orchestrator.py`
**Class:** `LangGraphOrchestrator`

- Instantiates all 7 agents once (`init_agents()`).
- Builds a `StateGraph(PipelineState)` with nodes: `monitor, ml_scorer, rca, decision, hitl, remediation, itsm, feedback`.
- **Conditional edges:**
  - `route_after_monitor`: no anomaly → `END`; anomaly → `ml_scorer`
  - `route_after_decision`: `decision == "HITL"` → `hitl`; else → `remediation`
  - `route_after_hitl`: `human_decision == "PENDING"` → `END`; else → `remediation`
- **Checkpointing:** `MemorySaver`, with `interrupt_before=["hitl"]` enabling pause/resume.
- `_convert()` recursively converts numpy types to native Python types before storing state (LangGraph serialization requirement).
- `run(incident)` invokes the graph for a fresh incident (`thread_id = incident_id`); if it pauses at HITL, persists state to `data/hitl_state_{incident_id}.json` and queues it.
- `resume_hitl(config, human_decision, human_notes, actual_fix)` reloads persisted state, injects the human decision, and resumes execution from `remediation` onward.
- All run outcomes are appended to `data/pipeline_log.json`.

### PipelineState fields
```python
incident, monitor_result, scored_result, rca_result, decision_result,
rem_result, ticket, feedback_result, human_decision, human_notes,
actual_fix, status, error, started_at, updated_at, total_time_sec
```
