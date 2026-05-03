# System Architecture

## Overview

The Continuous Pod Attestation System consists of three layers
that work together to enforce Zero Trust throughout the pod lifecycle.

## Layer 1 — Admission Control (Kyverno)

Kyverno acts as the first gate. Every pod creation request passes
through the admission webhook before any container starts.
kubectl apply / scale / rollout
↓
Kubernetes API Server
↓
Kyverno Admission Webhook
↓
┌──────────────────────┐
│  Policy Evaluation   │
│  • Registry check    │
│  • Resource limits   │
│  • Privileged mode   │
│  • ZTA label         │
└──────────┬───────────┘
Pass  │  Fail
↓    │    ↓
Pod      │  Rejected
Created   │  (403 error)

## Layer 2 — Runtime Monitoring (ZTA Engine)

The attestation engine runs as a Deployment inside `zta-system`.
It opens a persistent watch stream against the Kubernetes API
and receives every pod event in real time.
Kubernetes API Watch Stream
↓
Event Classifier
┌───────────────────────┐
│ POD_CREATED           │
│ POD_MODIFIED          │
│ POD_RESTARTED         │
│ POD_DELETED           │
└──────────┬────────────┘
↓
Attestation Engine
┌───────────────────────┐
│ Check 1: ZTA Label    │
│ Check 2: Registry     │
│ Check 3: Limits       │
│ Check 4: Privileged   │
│ Check 5: Restarts     │
└──────────┬────────────┘
Pass  │  Fail
↓    │    ↓
Log      │  Terminate
PASSED    │  + Log FAILED
│  + Write audit

## Trust Drift Prevention

Trust drift occurs when a pod that was once compliant becomes
non-compliant over time without detection.
Traditional ZTA (point-in-time):
t=0  [VERIFY] → admitted → running → ...never checked again...
↑
TRUST DRIFT
This System (continuous):
t=0  [VERIFY] → admitted
t=1  [VERIFY] → restart event
t=2  [VERIFY] → config change
t=3  [VERIFY] → scale event
t=n  [VERIFY] → every state transition
NO TRUST DRIFT

## Data Flow
Pod Event
↓
zta_engine.py (watch loop)
↓
classify_event() → event label
↓
run_attestation() → 5 checks
↓
build_audit_entry() → JSON
↓
write_audit_log() → /var/log/zta/audit.log
↓
if FAILED → terminate_pod() → K8s API delete

## RBAC Design

The engine uses a dedicated ServiceAccount with minimum required
permissions — following the principle of least privilege.
zta-controller (ServiceAccount)
↓
zta-controller-role (ClusterRole)
Permissions:
• pods: get, list, watch, delete
• deployments: get, list, watch
• replicasets: get, list, watch
• events: get, list, watch, create
• namespaces: get, list, watch
• serviceaccounts: get, list, watch
• configmaps: get, list, watch
