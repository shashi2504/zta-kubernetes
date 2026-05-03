#!/usr/bin/env python3
"""
ZTA Runtime Monitoring Controller
----------------------------------
Watches Kubernetes pod lifecycle events and triggers
re-verification for continuous Zero Trust enforcement.
"""

import time
import logging
import colorlog
from datetime import datetime
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
logger = logging.getLogger("zta-controller")
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


# ─────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────
WATCHED_NAMESPACE  = "zta-workloads"
ZTA_LABEL          = "zta-monitored"
APPROVED_REGISTRIES = ["docker.io", "nginx", "hashicorp", "registry.k8s.io", "gcr.io"]
RESTART_THRESHOLD  = 10   # max allowed restarts before violation


# ─────────────────────────────────────────
# Event Classifier
# ─────────────────────────────────────────
def classify_event(event_type, pod):
    """Classify the type of lifecycle event for a pod."""
    pod_name     = pod.metadata.name
    restart_count = sum(
        cs.restart_count
        for cs in (pod.status.container_statuses or [])
    )

    if event_type == "ADDED":
        return "POD_CREATED", pod_name, restart_count

    elif event_type == "MODIFIED":
        if restart_count > 0:
            return "POD_RESTARTED", pod_name, restart_count
        else:
            return "POD_MODIFIED", pod_name, restart_count

    elif event_type == "DELETED":
        return "POD_DELETED", pod_name, restart_count

    return "UNKNOWN", pod_name, restart_count


# ─────────────────────────────────────────
# Attestation Checks
# ─────────────────────────────────────────
def check_zta_label(pod):
    """Verify pod carries the zta-monitored label."""
    labels = pod.metadata.labels or {}
    if labels.get(ZTA_LABEL) != "true":
        return False, f"Missing or invalid label '{ZTA_LABEL}=true'"
    return True, "ZTA label present"


def check_approved_registry(pod):
    """Verify all container images come from approved registries."""
    for container in pod.spec.containers:
        image = container.image or ""
        if not any(image.startswith(reg) for reg in APPROVED_REGISTRIES):
            return False, f"Unapproved image registry: {image}"
    return True, "All images from approved registries"


def check_resource_limits(pod):
    """Verify all containers have resource limits defined."""
    for container in pod.spec.containers:
        limits = (container.resources.limits or {}) if container.resources else {}
        if "cpu" not in limits or "memory" not in limits:
            return False, f"Container '{container.name}' missing resource limits"
    return True, "Resource limits defined"


def check_privileged(pod):
    """Verify no container is running in privileged mode."""
    for container in pod.spec.containers:
        sc = container.security_context
        if sc and sc.privileged:
            return False, f"Container '{container.name}' is running in privileged mode"
    return True, "No privileged containers"


def check_restart_count(pod):
    """Flag pods exceeding restart threshold — potential crash loop or attack."""
    for cs in (pod.status.container_statuses or []):
        if cs.restart_count >= RESTART_THRESHOLD:
            return False, f"Container '{cs.name}' has restarted {cs.restart_count} times"
    return True, "Restart count within threshold"


# ─────────────────────────────────────────
# Re-verification Engine
# ─────────────────────────────────────────
def run_attestation(pod, event_label):
    """Run all ZTA checks against a pod and report results."""
    pod_name = pod.metadata.name
    logger.info(f"── Attesting pod: {pod_name} [{event_label}] ──")

    checks = [
        ("ZTA Label",          check_zta_label(pod)),
        ("Approved Registry",  check_approved_registry(pod)),
        ("Resource Limits",    check_resource_limits(pod)),
        ("No Privileged Mode", check_privileged(pod)),
        ("Restart Threshold",  check_restart_count(pod)),
    ]

    violations = []
    for check_name, (passed, message) in checks:
        if passed:
            logger.info(f"  ✅ {check_name}: {message}")
        else:
            logger.warning(f"  ❌ {check_name}: {message}")
            violations.append((check_name, message))

    if violations:
        logger.error(f"  🚨 ATTESTATION FAILED for {pod_name} — {len(violations)} violation(s) detected")
        return False, violations
    else:
        logger.info(f"  ✅ ATTESTATION PASSED for {pod_name}")
        return True, []


# ─────────────────────────────────────────
# Remediation
# ─────────────────────────────────────────
def terminate_pod(v1, pod):
    """Terminate a non-compliant pod automatically."""
    pod_name      = pod.metadata.name
    pod_namespace = pod.metadata.namespace
    try:
        v1.delete_namespaced_pod(name=pod_name, namespace=pod_namespace)
        logger.critical(f"  🔴 TERMINATED non-compliant pod: {pod_name}")
    except client.exceptions.ApiException as e:
        logger.error(f"  Failed to terminate pod {pod_name}: {e}")


# ─────────────────────────────────────────
# Main Watch Loop
# ─────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("  ZTA Runtime Monitoring Controller — Starting")
    logger.info(f"  Watching namespace : {WATCHED_NAMESPACE}")
    logger.info(f"  Approved registries: {APPROVED_REGISTRIES}")
    logger.info(f"  Restart threshold  : {RESTART_THRESHOLD}")
    logger.info("=" * 60)

    # Load kubeconfig (works both locally and in-cluster)
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("Loaded local kubeconfig")

    v1 = client.CoreV1Api()
    w  = watch.Watch()

    logger.info(f"👁  Watching pod events in namespace: {WATCHED_NAMESPACE}")

    while True:
        try:
            for event in w.stream(
                v1.list_namespaced_pod,
                namespace=WATCHED_NAMESPACE,
                timeout_seconds=0
            ):
                event_type = event["type"]
                pod        = event["object"]
                pod_name   = pod.metadata.name
                pod_phase  = pod.status.phase or "Unknown"

                # Skip system/terminated pods
                if pod_phase in ["Succeeded", "Unknown"]:
                    continue

                # Classify the event
                event_label, name, restarts = classify_event(event_type, pod)

                # Log the raw event
                logger.debug(
                    f"EVENT: {event_label} | Pod: {name} | "
                    f"Phase: {pod_phase} | Restarts: {restarts}"
                )

                # Only attest on meaningful lifecycle events
                if event_label in ["POD_CREATED", "POD_RESTARTED", "POD_MODIFIED"]:
                    passed, violations = run_attestation(pod, event_label)

                    # Remediate if attestation fails
                    if not passed:
                        terminate_pod(v1, pod)

                elif event_label == "POD_DELETED":
                    logger.info(f"🗑  Pod deleted: {pod_name}")

        except Exception as e:
            logger.error(f"Watch stream error: {e} — restarting in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
