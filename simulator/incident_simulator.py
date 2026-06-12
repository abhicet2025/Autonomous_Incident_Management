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
