# K8s Intelligent Node Evacuator

A Python-based tool to safely evacuate pods from a Kubernetes node, with **intelligent pod ordering, batch support, StatefulSet handling**, optional **Prometheus metrics**, and live CLI progress tracking.

---

## Features

- **Safe pod evacuation** without modifying Deployment/StatefulSet specs.  
- **Pod-aware batching**: one-by-one, fixed-size batch, or all-at-once.  
- **StatefulSet support**: pods evicted in ordinal order to preserve stability.  
- **Pre- and post-checks**: waits for workloads to reach **desired state**.  
- **Excludes**: DaemonSets, Jobs, completed/failed pods, mirror pods.  
- **Optional metrics**: push per-pod progress and status to Prometheus Pushgateway.  
- **Live progress tracking** in CLI.  
- Fully configurable **timeout** and **retry** logic.  

---

## Installation

```bash
pip install kubernetes prometheus-client