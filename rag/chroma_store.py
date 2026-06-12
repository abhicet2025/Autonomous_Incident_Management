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
