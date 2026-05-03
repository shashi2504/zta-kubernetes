#!/usr/bin/env python3
"""
ZTA Continuous Attestation Engine
-----------------------------------
Production-grade controller that:
- Reads policies from Kubernetes ConfigMap
- Continuously attests pods on every lifecycle event
- Writes structured audit logs for every decision
- Auto-remediates violations via pod termination
"""

import os
import json
import time
import logging
import colorlog
from datetime import datetime, timezone
from kubernetes import client, config, watch

# ─────────────────────────────────────────
# Logging Setup
# ─────────────────────────────────────────
handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s [%(levelname)s]%(reset)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={
        "DEBUG":    "cyan",
        "INFO":     "green",
        "WARNING":  "yellow",
        "ERROR":    "red",
        "CRITICAL": "bold_red",
    }
))
logger = logging.getLogger("zta-engine")
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


# ─────────────────────────────────────────
# Load Kubernetes Config
# ─────────────────────────────────────────
def load_k8s_config():
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("Loaded local kubeconfig")


# ─────────────────────────────────────────
# Load Policy from ConfigMap
# ─────────────────────────────────────────
def load_policy(v1):
    """Load ZTA policy configuration from Kubernetes ConfigMap."""
    try:
        cm = v1.read_namespaced_config_map(
            name="zta-policy-config",
            namespace="zta-system"
        )
        data = cm.data
        policy = {
            "approved_registries": data.get("approved_registries", "docker.io,nginx").split(","),
            "restart_threshold":   int(data.get("restart_threshold", "10")),
            "watched_namespace":   data.get("watched_namespace", "zta-workloads"),
            "zta_label_key":       data.get("zta_label_key", "zta-monitored"),
            "zta_label_value":     data.get("zta_label_value", "true"),
            "log_file_path":       data.get("log_file_path", "/var/log/zta/audit.log"),
        }
        logger.info(f"Policy loaded from ConfigMap: {policy}")
        return policy
    except Exception as e:
        logger.error(f"Failed to load ConfigMap, using defaults: {e}")
        return {
            "approved_registries": ["docker.io", "nginx", "hashicorp", "registry.k8s.io", "gcr.io"],
            "restart_threshold":   10,
            "watched_namespace":   "zta-workloads",
            "zta_label_key":       "zta-monitored",
            "zta_label_value":     "true",
            "log_file_path":       "./zta-audit.log",
        }


# ─────────────────────────────────────────
# Audit Logger
# ─────────────────────────────────────────
def write_audit_log(log_path, entry):
    """Write a structured JSON audit entry to the log file."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to write audit log: {e}")


def build_audit_entry(pod, event_label, passed, violations):
    """Build a structured audit log entry."""
    return {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "pod":         pod.metadata.name,
        "namespace":   pod.metadata.namespace,
        "event":       event_label,
        "phase":       pod.status.phase or "Unknown",
        "result":      "PASSED" if passed else "FAILED",
        "violations":  [{"check": c, "reason": r} for c, r in violations],
        "action":      "TERMINATED" if not passed else "NONE",
        "image":       pod.spec.containers[0].image if pod.spec.containers else "unknown",
        "node":        pod.spec.node_name or "unscheduled",
        "labels":      pod.metadata.labels or {},
    }


# ─────────────────────────────────────────
# Attestation Checks
# ─────────────────────────────────────────
def check_zta_label(pod, policy):
    key   = policy["zta_label_key"]
    value = policy["zta_label_value"]
    labels = pod.metadata.labels or {}
    if labels.get(key) != value:
        return False, f"Missing or invalid label '{key}={value}'"
    return True, "ZTA label present"


def check_approved_registry(pod, policy):
    registries = policy["approved_registries"]
    for container in pod.spec.containers:
        image = container.image or ""
        if not any(image.startswith(reg) for reg in registries):
            return False, f"Unapproved image registry: {image}"
    return True, "All images from approved registries"


def check_resource_limits(pod, policy):
    for container in pod.spec.containers:
        limits = (container.resources.limits or {}) if container.resources else {}
        if "cpu" not in limits or "memory" not in limits:
            return False, f"Container '{container.name}' missing resource limits"
    return True, "Resource limits defined"


def check_privileged(pod, policy):
    for container in pod.spec.containers:
        sc = container.security_context
        if sc and sc.privileged:
            return False, f"Container '{container.name}' is running in privileged mode"
    return True, "No privileged containers"


def check_restart_count(pod, policy):
    threshold = policy["restart_threshold"]
    for cs in (pod.status.container_statuses or []):
        if cs.restart_count >= threshold:
            return False, f"Container '{cs.name}' has restarted {cs.restart_count} times"
    return True, "Restart count within threshold"


# ─────────────────────────────────────────
# Core Attestation Runner
# ─────────────────────────────────────────
def run_attestation(pod, event_label, policy, log_path):
    """Run all ZTA checks, log the result, return pass/fail."""
    pod_name = pod.metadata.name
    logger.info(f"── Attesting pod: {pod_name} [{event_label}] ──")

    checks = [
        ("ZTA Label",          check_zta_label(pod, policy)),
        ("Approved Registry",  check_approved_registry(pod, policy)),
        ("Resource Limits",    check_resource_limits(pod, policy)),
        ("No Privileged Mode", check_privileged(pod, policy)),
        ("Restart Threshold",  check_restart_count(pod, policy)),
    ]

    violations = []
    for check_name, (passed, message) in checks:
        if passed:
            logger.info(f"  ✅ {check_name}: {message}")
        else:
            logger.warning(f"  ❌ {check_name}: {message}")
            violations.append((check_name, message))

    passed = len(violations) == 0

    if passed:
        logger.info(f"  ✅ ATTESTATION PASSED for {pod_name}")
    else:
        logger.error(f"  🚨 ATTESTATION FAILED for {pod_name} — {len(violations)} violation(s)")

    # Write audit entry
    entry = build_audit_entry(pod, event_label, passed, violations)
    write_audit_log(log_path, entry)

    return passed, violations


# ─────────────────────────────────────────
# Remediation
# ─────────────────────────────────────────
def terminate_pod(v1, pod, log_path):
    """Terminate a non-compliant pod and log the action."""
    pod_name      = pod.metadata.name
    pod_namespace = pod.metadata.namespace
    try:
        v1.delete_namespaced_pod(name=pod_name, namespace=pod_namespace)
        logger.critical(f"  🔴 TERMINATED non-compliant pod: {pod_name}")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            logger.debug(f"  Pod {pod_name} already gone (404) — skipping")
        else:
            logger.error(f"  Failed to terminate {pod_name}: {e}")


# ─────────────────────────────────────────
# Event Classifier
# ─────────────────────────────────────────
def classify_event(event_type, pod):
    restart_count = sum(
        cs.restart_count
        for cs in (pod.status.container_statuses or [])
    )
    if event_type == "ADDED":
        return "POD_CREATED", restart_count
    elif event_type == "MODIFIED":
        return ("POD_RESTARTED" if restart_count > 0 else "POD_MODIFIED"), restart_count
    elif event_type == "DELETED":
        return "POD_DELETED", restart_count
    return "UNKNOWN", restart_count


# ─────────────────────────────────────────
# Main Watch Loop
# ─────────────────────────────────────────
def main():
    load_k8s_config()
    v1 = client.CoreV1Api()

    # Load policy from ConfigMap
    policy   = load_policy(v1)
    log_path = policy["log_file_path"]
    ns       = policy["watched_namespace"]

    logger.info("=" * 60)
    logger.info("  ZTA Continuous Attestation Engine — Starting")
    logger.info(f"  Watching namespace  : {ns}")
    logger.info(f"  Approved registries : {policy['approved_registries']}")
    logger.info(f"  Restart threshold   : {policy['restart_threshold']}")
    logger.info(f"  Audit log           : {log_path}")
    logger.info("=" * 60)

    w = watch.Watch()

    while True:
        try:
            for event in w.stream(
                v1.list_namespaced_pod,
                namespace=ns,
                timeout_seconds=0
            ):
                event_type = event["type"]
                pod        = event["object"]
                pod_phase  = pod.status.phase or "Unknown"

                if pod_phase in ["Succeeded", "Unknown"]:
                    continue

                event_label, restarts = classify_event(event_type, pod)

                logger.debug(
                    f"EVENT: {event_label} | Pod: {pod.metadata.name} | "
                    f"Phase: {pod_phase} | Restarts: {restarts}"
                )

                if event_label in ["POD_CREATED", "POD_RESTARTED", "POD_MODIFIED"]:
                    passed, violations = run_attestation(pod, event_label, policy, log_path)
                    if not passed:
                        terminate_pod(v1, pod, log_path)

                elif event_label == "POD_DELETED":
                    logger.info(f"🗑  Pod deleted: {pod.metadata.name}")

        except Exception as e:
            logger.error(f"Watch stream error: {e} — restarting in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
