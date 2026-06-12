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
