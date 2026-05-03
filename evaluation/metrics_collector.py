#!/usr/bin/env python3
"""
ZTA Metrics Collector
----------------------
Reads the audit log and produces quantitative evaluation
metrics suitable for research publication.
"""

import json
import sys
from datetime import datetime, timezone
from collections import defaultdict

def load_audit_log(path):
    entries = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries

def compute_metrics(entries):
    total         = len(entries)
    passed        = sum(1 for e in entries if e["result"] == "PASSED")
    failed        = sum(1 for e in entries if e["result"] == "FAILED")
    terminated    = sum(1 for e in entries if e["action"] == "TERMINATED")

    # Events breakdown
    event_counts = defaultdict(int)
    for e in entries:
        event_counts[e["event"]] += 1

    # Violation types
    violation_types = defaultdict(int)
    for e in entries:
        for v in e.get("violations", []):
            violation_types[v["check"]] += 1

    # Detection latency per violation pod
    # (time from POD_CREATED to first FAILED entry)
    pod_first_seen   = {}
    pod_first_failed = {}
    for e in entries:
        pod  = e["pod"]
        ts   = datetime.fromisoformat(e["timestamp"])
        if e["event"] == "POD_CREATED":
            if pod not in pod_first_seen:
                pod_first_seen[pod] = ts
        if e["result"] == "FAILED":
            if pod not in pod_first_failed:
                pod_first_failed[pod] = ts

    latencies = []
    for pod in pod_first_failed:
        if pod in pod_first_seen:
            delta = (pod_first_failed[pod] - pod_first_seen[pod]).total_seconds()
            latencies.append((pod, delta))

    avg_latency = sum(l for _, l in latencies) / len(latencies) if latencies else 0
    min_latency = min(l for _, l in latencies) if latencies else 0
    max_latency = max(l for _, l in latencies) if latencies else 0

    return {
        "total_attestations":   total,
        "passed":               passed,
        "failed":               failed,
        "terminated":           terminated,
        "pass_rate":            round(passed / total * 100, 2) if total else 0,
        "violation_rate":       round(failed / total * 100, 2) if total else 0,
        "event_counts":         dict(event_counts),
        "violation_types":      dict(violation_types),
        "detection_latencies":  latencies,
        "avg_latency_sec":      round(avg_latency, 4),
        "min_latency_sec":      round(min_latency, 4),
        "max_latency_sec":      round(max_latency, 4),
    }

def print_report(metrics):
    print("\n" + "=" * 60)
    print("  ZTA EVALUATION METRICS REPORT")
    print("=" * 60)

    print(f"\n📊 ATTESTATION SUMMARY")
    print(f"  Total attestations : {metrics['total_attestations']}")
    print(f"  Passed             : {metrics['passed']} ({metrics['pass_rate']}%)")
    print(f"  Failed             : {metrics['failed']} ({metrics['violation_rate']}%)")
    print(f"  Pods terminated    : {metrics['terminated']}")

    print(f"\n📋 EVENT BREAKDOWN")
    for event, count in sorted(metrics['event_counts'].items()):
        print(f"  {event:<20} : {count}")

    print(f"\n🚨 VIOLATION TYPES DETECTED")
    if metrics['violation_types']:
        for vtype, count in sorted(metrics['violation_types'].items()):
            print(f"  {vtype:<25} : {count} occurrence(s)")
    else:
        print("  No violations recorded")

    print(f"\n⚡ DETECTION LATENCY")
    print(f"  Average : {metrics['avg_latency_sec']} seconds")
    print(f"  Minimum : {metrics['min_latency_sec']} seconds")
    print(f"  Maximum : {metrics['max_latency_sec']} seconds")

    if metrics['detection_latencies']:
        print(f"\n  Per-pod latencies:")
        for pod, latency in metrics['detection_latencies']:
            print(f"    {pod:<45} : {latency:.4f}s")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    log_path = sys.argv[1] if len(sys.argv) > 1 else "zta-audit.log"
    entries  = load_audit_log(log_path)
    metrics  = compute_metrics(entries)
    print_report(metrics)
