# Autonomous Incident Diagnosis & Resolution Agent
**TCS & AMD AI Hackathon — Track 1: Agents (AGENTS_026)**

---

## 1. Problem Statement

Modern infrastructure teams are overwhelmed by alarms, logs, and metrics from sprawling distributed systems. When something breaks — a CPU spike, a database connection pool exhaustion, a memory leak — engineers must manually correlate signals, diagnose the root cause, decide on a fix, execute it, log a ticket, and document the resolution. This process is slow, inconsistent, and depends heavily on individual expertise.

**Target users:** Site Reliability Engineers (SREs), DevOps teams, and platform engineering groups responsible for production infrastructure uptime.

**Why it matters:** Faster, more consistent incident response directly reduces Mean Time To Resolution (MTTR), minimizes business impact of outages, and frees senior engineers from repetitive triage work — while still keeping a human in the loop for high-risk decisions.

**Mapped hackathon challenge:** Track 1 — Agents (AGENTS_026: Autonomous Incident Diagnosis & Resolution Agent). A multi-agent system that ingests infrastructure telemetry, performs root cause analysis using an LLM + RAG pipeline, and either auto-remediates or routes to a human reviewer based on confidence and severity.

---

## 2. Solution Overview

We built a **production-style, multi-agent incident management system** that mirrors how a real SRE on-call rotation operates — but automated, explainable, and self-improving.

### 2.1 End-to-End Architecture

```
Infrastructure Metrics + Logs (simulated)
              │
              ▼
┌─────────────────────────────────────────┐
│  TIER 1 — Monitor Agent                  │
│  Rule-based thresholds + Z-score         │
│  anomaly detection + log keyword scan    │
│  (no LLM — sub-second, runs at scale)    │
└──────────────┬────────────────────────────┘
               │ anomaly detected
               ▼
┌─────────────────────────────────────────┐
│  TIER 2 — ML Scorer Agent                │
│  Isolation Forest (sklearn) trained on   │
│  200 normal samples — severity scoring   │
│  on AMD MI300X via ROCm                  │
└──────────────┬────────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  TIER 3 — RCA Agent                      │
│  ChromaDB RAG (runbooks + past incident  │
│  feedback) + Qwen2.5-32B via Ollama      │
│  → root cause, fix steps, confidence     │
└──────────────┬────────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  Decision Agent (LangGraph)              │
│  Combines ML + LLM confidence →          │
│  AUTO or HITL routing                    │
│  CRITICAL severity → ALWAYS human review │
└──────┬─────────────────────────┬─────────┘
       │ HITL                    │ AUTO
       ▼                         ▼
┌──────────────────┐    ┌─────────────────────┐
│  HITL Portal      │    │  Remediation Agent  │
│  (Streamlit)      │    │  Executes playbook  │
│  Human approves/  │    │  (5-6 step runbook) │
│  rejects/edits    │    └──────────┬──────────┘
└────────┬──────────┘               │
         │ approved                 │
         └─────────────┬────────────┘
                        ▼
              ┌──────────────────┐
              │  ITSM Agent       │
              │  Creates, updates,│
              │  closes ticket    │
              │  Calculates MTTR  │
              └─────────┬─────────┘
                        ▼
              ┌──────────────────┐
              │  Feedback Agent   │
              │  Stores outcome   │
              │  in ChromaDB →    │
              │  improves future  │
              │  RCA (learning    │
              │  loop)            │
              └──────────────────┘
```

### 2.2 What Was Built During the Hackathon

| Component | Description |
|---|---|
| **Incident Simulator** | Generates 5 realistic incident types (CPU spike, DB connection exhaustion, memory leak, disk full, service crash) plus normal traffic, with synthetic metrics and log lines |
| **Monitor Agent** | Tier-1 detection using static thresholds, rolling Z-score deviation, and log keyword matching — no LLM required |
| **ML Scorer Agent** | Isolation Forest model trained on AMD MI300X via ROCm; scores anomaly severity (CRITICAL/HIGH/MEDIUM/LOW) |
| **RCA Agent** | Combines ChromaDB semantic search (section-chunked runbooks + past feedback) with Qwen2.5-32B (Ollama) to produce root cause, ranked fix steps, confidence score, and incident classification |
| **Decision Agent** | Routing logic combining ML + LLM confidence; CRITICAL always routes to human; HIGH/MEDIUM/LOW route by confidence threshold |
| **LangGraph Orchestrator** | Graph-based state machine coordinating all agents, with checkpointing and `interrupt_before` for human-in-the-loop pauses |
| **Remediation Agent** | Executes incident-type-specific playbooks (5–6 sequential steps each) with realistic timing |
| **ITSM Agent** | Creates, updates, and closes tickets with priority (P1–P4), team assignment, full timeline, and MTTR calculation |
| **Feedback Agent** | Ingests every resolution (auto or human-reviewed) back into ChromaDB — closing the self-improvement loop |
| **HITL Portal** (Streamlit) | Human review interface — shows incident details, AI confidence, root cause, metrics vs. normal range, recommended fixes; supports approve/reject with notes and fix overrides |
| **HITL Processor** | Background service that watches for human decisions and automatically resumes the pipeline (remediation → ITSM → feedback) |
| **Dashboard** (Streamlit) | Control Panel to trigger runs (single/all/continuous), Live Monitor for active incidents with per-agent progress, and searchable History with full per-agent trace for every resolved incident |

---

## 3. AI Approach

This solution combines **four complementary AI/ML techniques**, each chosen for the part of the problem it solves best:

1. **Rule-based + statistical anomaly detection** (Monitor Agent) — fast, deterministic, zero-cost first-pass filter so the expensive LLM is only invoked when something is actually wrong.
2. **Unsupervised ML (Isolation Forest)** (ML Scorer Agent) — learns the shape of "normal" from synthetic baseline data and scores how anomalous an incident is, without needing labeled incident data.
3. **Retrieval-Augmented Generation (RAG)** (RCA Agent) — ChromaDB stores section-chunked runbooks (Symptoms, Root Causes, Remediation Steps) and past incident feedback; retrieval is targeted per-section so the LLM gets precisely relevant context.
4. **LLM reasoning** (Qwen2.5-32B) — synthesizes retrieved context + live metrics/logs into a structured root cause, ranked remediation steps, confidence score, and incident classification — all returned as JSON for downstream automation.
5. **Agentic orchestration with human-in-the-loop** (LangGraph) — a graph-based state machine routes between fully autonomous and human-supervised paths based on severity and combined confidence, with full state persistence so a human can review hours later and the pipeline resumes exactly where it left off.
6. **Continuous learning loop** (Feedback Agent) — every resolved incident (whether auto-resolved or human-corrected) is written back into the vector store, so future RCA retrieval becomes progressively better informed by real outcomes.

---

## 4. Key Technologies & Frameworks

| Layer | Technology |
|---|---|
| LLM (primary) | Qwen2.5-32B served via Ollama (4-bit quantized) |
| LLM (validated secondary / production path) | Qwen2.5-32B & 7B served via vLLM 0.15.0 (ROCm 7.0), OpenAI-compatible API |
| Orchestration | LangGraph (graph-based state machine, MemorySaver checkpointing, `interrupt_before` for HITL) |
| Vector Database | ChromaDB (persistent), section-based chunking (150 words, 20-word overlap) |
| Embeddings | all-MiniLM-L6-v2 (ONNX, ChromaDB default embedding function) |
| ML Model | Isolation Forest (scikit-learn), 100 estimators, contamination=0.05 |
| GPU / Compute | AMD Instinct MI300X (206 GB VRAM) via ROCm 7.0 |
| UI | Streamlit (HITL Portal on port 8501, Dashboard on port 8502) |
| Language | Python 3.12 |

---

## 5. Human-in-the-Loop (HITL) Design

A core design principle of this system is that **automation should be earned, not assumed**. The Decision Agent applies the following routing logic:

| Severity | Behavior |
|---|---|
| CRITICAL | **Always** routed to human review, regardless of AI confidence |
| HIGH | Routed to human unless combined confidence ≥ 85% |
| MEDIUM / LOW | Auto-remediated |
| NONE (no anomaly) | No action taken |

When an incident is routed to HITL:
1. The full pipeline state (including RCA, metrics, logs, recommendations) is persisted to disk.
2. The incident appears in the **HITL Portal**, showing AI confidence, root cause, metrics vs. normal ranges, error logs, and recommended fix steps.
3. The engineer can **approve** (optionally overriding the fix), **reject** (escalating to manual investigation), and add notes.
4. The **HITL Processor** (background service) detects the decision, restores the persisted state, and resumes the pipeline — running Remediation → ITSM → Feedback automatically.
5. The human's decision and notes are stored back into the knowledge base via the Feedback Agent, directly influencing future RCA quality for similar incidents.

This design ensures the system is **safe to deploy incrementally** — starting fully human-supervised and earning more autonomy over time as the knowledge base and confidence calibration improve.

---

## 6. Dashboard & Demo Experience

To make the system's behavior transparent and demo-friendly, we built a two-application interface:

- **HITL Portal (port 8501):** Dedicated review queue for human decisions — kept separate from the dashboard so approval workflows stay focused and auditable.
- **Dashboard (port 8502):**
  - **Control Panel** — trigger a single scenario, all 5 scenarios sequentially, or a continuous stream that mixes random incidents with normal traffic (configurable rate and interval).
  - **Live Monitor** — shows currently active incidents with a visual per-agent progress tracker (Monitor → ML Scorer → RCA → Decision → HITL → Remediation → ITSM → Feedback).
  - **History** — every resolved incident, searchable by ID/scenario/service/severity, with a full professional breakdown of each agent's output, including metrics, root cause, confidence scores, remediation steps taken, ticket details, and the exact feedback record written to the knowledge base.

---

## 7. Model Insights & Performance Metrics

*(Representative figures captured during development on AMD MI300X / ROCm 7.0)*

| Metric | Value |
|---|---|
| LLM Model | Qwen2.5-32B (via Ollama — primary serving backend) |
| GPU | AMD Instinct MI300X — 206 GB VRAM |
| ML Model | Isolation Forest, trained on 200 synthetic normal samples, ~0.44s training time |
| Embedding Model | all-MiniLM-L6-v2 (ONNX) |
| Knowledge Base Size | 65+ chunks (5 runbooks, section-based chunking) + growing feedback records |
| RCA Tokens per Incident | ~850–950 tokens |
| LLM (RCA) Latency | ~6–7 seconds |
| RAG Retrieval Latency | ~5–8 seconds (first call; faster on warm cache) |
| ML Scoring Latency | ~6–9 ms |
| End-to-End Auto Path | Monitor → Decision → Remediation → ITSM → Feedback, fully automated |
| End-to-End HITL Path | Same, with human approval step; remediation executes in 6–8.5s once approved |

### 7.1 vLLM Integration — Validated Alternative Serving Backend

To explore production-grade serving options, we deployed **Qwen2.5-32B-Instruct via vLLM 0.15.0 (ROCm 7.0)** on the same AMD MI300X GPU and exposed it through vLLM's OpenAI-compatible API on port 8000. We also validated **Qwen2.5-7B-Instruct** on vLLM as a lightweight configuration. The integration was tested end-to-end (model loading, GPU memory allocation, chat completion, token accounting):

| Test | Ollama 32B (single request) | vLLM 32B (single request) | vLLM 7B (single request) |
|---|---|---|---|
| Precision | 4-bit quantized (Q4_K_M) | bf16 (full precision) | bf16 (full precision) |
| Hardware | AMD MI300X / ROCm 7.0 | AMD MI300X / ROCm 7.0 | AMD MI300X / ROCm 7.0 |
| Disk footprint | ~20 GB | ~65 GB | ~15 GB |
| GPU memory (model load) | — | 61.1 GiB | ~15 GiB |
| Total tokens | ~900–950 | 550 | 77 |
| Wall-clock latency | ~6.4–6.8 s | ~7.4 s | ~0.78 s |
| Approx. throughput | ~135–145 tok/s | ~74 tok/s | ~99 tok/s |

**Findings:** For single-request latency, Ollama's 4-bit quantized 32B model performed comparably to or better than vLLM's full-precision 32B — Ollama's serving path is already well-optimized for low-concurrency, single-user workloads like our sequential pipeline. vLLM's architectural advantages (continuous batching, paged attention, prefix caching) are designed for **concurrent multi-request throughput**, which becomes the dominant factor at production scale with many simultaneous incidents.

**Why we tested vLLM in full precision (bf16) rather than quantized:** We deliberately validated vLLM against the *unquantized* 32B model first, to characterize raw inference performance on AMD MI300X/ROCm without quantization as a confounding variable — establishing a clean baseline for the serving engine itself. Our current persistent storage allocation favours Ollama's 4-bit quantized model (~20 GB), which comfortably coexists with the ChromaDB knowledge base and all incident data within quota across session restarts.

**Production path:** For production rollout, vLLM would be paired with an **AWQ/GPTQ-quantized Qwen2.5-32B** (~18–20 GB) — a configuration-only change (`--quantization awq`), not a redesign — combining a storage footprint comparable to our current Ollama setup with vLLM's continuous-batching throughput advantage under concurrent load.

**Conclusion:** The vLLM serving path is fully integration-tested and production-ready (OpenAI-compatible `/v1/chat/completions` API, drop-in replacement for the RCA Agent's LLM call, validated at both 7B and 32B scales). For this hackathon's sequential single-incident pipeline, **Ollama remains the active/primary backend**; **vLLM is positioned as the validated, recommended secondary backend for production deployment** at concurrent scale (see Future Work).

---

## 8. Impact & Value

- **Reduced MTTR:** Automated diagnosis and remediation for low/medium severity incidents removes manual triage time entirely.
- **Consistency:** Every incident is analyzed against the same runbooks and historical outcomes, reducing variance between engineers.
- **Explainability:** Every decision — AUTO or HITL — comes with a root cause, confidence score, and reasoning, satisfying audit and compliance needs.
- **Safe automation:** CRITICAL incidents are never auto-resolved without human sign-off, building trust incrementally.
- **Self-improving:** Each resolution (human or automated) enriches the knowledge base, so RCA quality compounds over time without retraining the LLM.
- **Full audit trail:** ITSM tickets capture priority, assigned team, timeline, and MTTR for every incident — auto or HITL.

---

## 9. Key Differentiators & Innovation

- **Tiered detection** keeps cost and latency low — the LLM is only invoked once a statistically meaningful anomaly is confirmed.
- **Section-aware RAG** retrieves targeted runbook sections (Symptoms / Root Causes / Remediation) rather than whole documents, improving relevance of LLM context.
- **Cross-session HITL resumption** — pipeline state is persisted to disk, so a human can review and approve an incident hours after it was raised, and the system resumes exactly where it paused (including after a notebook restart).
- **Closed feedback loop** — human corrections and auto-resolutions both feed back into the same vector store used for future RCA, creating a continuously improving system without model fine-tuning.
- **Dual-interface design** — separating the HITL approval workflow from the monitoring dashboard mirrors real enterprise tooling (e.g., ITSM approval queues vs. observability dashboards).

---

## 10. Future Work

- **Production rollout of vLLM serving** for concurrent multi-incident workloads — integration already validated (see Section 7.1); batching benefits become significant under concurrent load.
- **Fine-tuning (PEFT/LoRA)** on accumulated feedback records to specialize the model for organization-specific infrastructure terminology and playbooks.
- **Real production integrations** — replace simulated metrics/logs with Prometheus/Datadog feeds, and replace simulated remediation/ITSM actions with real Kubernetes API calls and ServiceNow/Jira integration.
- **Expanded incident catalog** beyond the current 5 scenario types, including multi-service cascading failures.
- **Confidence calibration** — track AUTO vs. HITL outcomes over time to dynamically tune the confidence threshold per incident type.
- **Multi-tenant dashboard** with role-based access for different SRE teams/services.

---

## 11. Repository Structure

```
incident_agent/
├── main_runner.py              # Entry point (single / all / continuous modes)
├── dashboard.py                 # Control Panel, Live Monitor, History
├── hitl_processor.py            # Background HITL decision processor
├── setup.sh                      # Environment restore script
├── agents/
│   ├── monitor_agent.py         # Tier 1: rule + Z-score detection
│   ├── ml_scorer_agent.py        # Tier 2: Isolation Forest scoring
│   ├── rca_agent.py              # Tier 3: LLM + RAG analysis
│   ├── decision_agent.py         # AUTO vs HITL routing
│   ├── remediation_agent.py      # Playbook execution
│   ├── itsm_agent.py             # Ticket lifecycle management
│   ├── feedback_agent.py         # Self-improvement loop
│   ├── status_writer.py          # Live status for dashboard
│   └── langgraph_orchestrator.py # Pipeline coordinator
├── rag/
│   ├── chroma_store.py           # Vector DB operations
│   └── knowledge_base/           # Runbook documents (5 incident types)
├── simulator/
│   └── incident_simulator.py     # Synthetic data generation
└── hitl/
    └── hitl_portal.py             # Streamlit HITL review interface
```
