#!/usr/bin/env python3

import time
import argparse
from collections import defaultdict
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Load kube config
config.load_kube_config()

core = client.CoreV1Api()
apps = client.AppsV1Api()
policy = client.PolicyV1Api()

# -----------------------------
# Config Defaults
# -----------------------------
DEFAULT_TIMEOUT = 300
DEFAULT_RETRIES = 5
SLEEP_INTERVAL = 5

# -----------------------------
# Node Operations
# -----------------------------

def cordon_node(node_name):
    print(f"[INFO] Cordoning node {node_name}")
    core.patch_node(node_name, {"spec": {"unschedulable": True}})


def uncordon_node(node_name):
    print(f"[INFO] Uncordoning node {node_name}")
    core.patch_node(node_name, {"spec": {"unschedulable": False}})


# -----------------------------
# Pod Filters
# -----------------------------

def is_mirror_pod(pod):
    return pod.metadata.annotations and \
        "kubernetes.io/config.mirror" in pod.metadata.annotations


def is_daemonset_pod(pod):
    return pod.metadata.owner_references and \
        any(o.kind == "DaemonSet" for o in pod.metadata.owner_references)


def is_job_pod(pod):
    return pod.metadata.owner_references and \
        any(o.kind == "Job" for o in pod.metadata.owner_references)


def is_terminal_pod(pod):
    return pod.status.phase in ["Succeeded", "Failed"]


# -----------------------------
# Get Pods
# -----------------------------

def get_pods_on_node(node_name):
    pods = core.list_pod_for_all_namespaces(
        field_selector=f"spec.nodeName={node_name}"
    ).items

    result = []

    for p in pods:
        if is_mirror_pod(p):
            continue
        if is_daemonset_pod(p):
            continue
        if is_job_pod(p):
            print(f"[SKIP] Job pod {p.metadata.name}")
            continue
        if is_terminal_pod(p):
            print(f"[SKIP] Completed pod {p.metadata.name}")
            continue
        if p.metadata.deletion_timestamp:
            continue

        result.append(p)

    return result


# -----------------------------
# Grouping
# -----------------------------

def group_by_owner(pods):
    groups = defaultdict(list)

    for pod in pods:
        if not pod.metadata.owner_references:
            key = ("orphan", pod.metadata.name, pod.metadata.namespace)
        else:
            owner = pod.metadata.owner_references[0]
            key = (owner.kind, owner.name, pod.metadata.namespace)

        groups[key].append(pod)

    return groups


# -----------------------------
# Pod Readiness
# -----------------------------

def is_pod_ready(pod):
    if pod.status.phase != "Running":
        return False

    for c in pod.status.conditions or []:
        if c.type == "Ready" and c.status == "True":
            return True

    return False


# -----------------------------
# Desired State Checks
# -----------------------------

def get_desired_replicas(kind, name, namespace):
    try:
        if kind == "Deployment":
            return apps.read_namespaced_deployment(name, namespace).spec.replicas
        elif kind == "StatefulSet":
            return apps.read_namespaced_stateful_set(name, namespace).spec.replicas
    except:
        pass

    return 1


def count_ready_pods(namespace, label_selector):
    pods = core.list_namespaced_pod(namespace, label_selector=label_selector).items
    return sum(1 for p in pods if is_pod_ready(p))


def wait_until_desired_state(kind, name, namespace, label_selector,
                             timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):

    desired = get_desired_replicas(kind, name, namespace)

    for attempt in range(retries):
        print(f"[CHECK] Desired state attempt {attempt+1}/{retries}")

        start = time.time()

        while time.time() - start < timeout:
            ready = count_ready_pods(namespace, label_selector)

            print(f"[STATUS] {kind}/{name}: {ready}/{desired} ready")

            if ready == desired:
                print("[OK] Desired state reached")
                return True

            time.sleep(SLEEP_INTERVAL)

        print("[WARN] Desired state not reached, retrying...")

    raise TimeoutError(f"{kind}/{name} failed to reach desired state")


# -----------------------------
# Eviction
# -----------------------------

def evict_pod(pod, retries=DEFAULT_RETRIES):
    for attempt in range(retries):
        print(f"[EVICT] {pod.metadata.namespace}/{pod.metadata.name} (try {attempt+1})")

        eviction = client.V1Eviction(
            metadata=client.V1ObjectMeta(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace
            )
        )

        try:
            policy.create_namespaced_pod_eviction(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                body=eviction
            )
            return

        except ApiException as e:
            if e.status == 429:
                print("[WARN] PDB blocked eviction, retrying...")
                time.sleep(SLEEP_INTERVAL)
            else:
                raise

    raise RuntimeError(f"Failed to evict {pod.metadata.name}")


# -----------------------------
# Wait for Replacement (UID SAFE)
# -----------------------------

def wait_for_replacement(pod, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
    ns = pod.metadata.namespace
    labels = pod.metadata.labels or {}
    label_selector = ",".join([f"{k}={v}" for k, v in labels.items()])

    # Capture existing pod UIDs BEFORE eviction
    old_pods = core.list_namespaced_pod(ns, label_selector=label_selector).items
    old_uids = {p.metadata.uid for p in old_pods}

    for attempt in range(retries):
        print(f"[WAIT] Replacement attempt {attempt+1}/{retries}")

        start = time.time()

        while time.time() - start < timeout:
            pods = core.list_namespaced_pod(ns, label_selector=label_selector).items

            for p in pods:
                if p.metadata.uid in old_uids:
                    continue

                if is_pod_ready(p):
                    print(f"[READY] New pod {p.metadata.name} is ready")
                    return True

            time.sleep(SLEEP_INTERVAL)

        print("[WARN] Replacement not ready, retrying...")

    raise TimeoutError(f"Replacement pod for {pod.metadata.name} failed")


# -----------------------------
# Evacuation Logic
# -----------------------------

def evacuate_group(kind, name, ns, pods, batch_size):
    labels = pods[0].metadata.labels or {}
    label_selector = ",".join([f"{k}={v}" for k, v in labels.items()])

    print(f"\n[GROUP] {kind}/{name} ({ns}) - {len(pods)} pods")

    # -----------------------------
    # PRE-CHECK
    # -----------------------------
    wait_until_desired_state(kind, name, ns, label_selector)

    # -----------------------------
    # StatefulSet ordering
    # -----------------------------
    if kind == "StatefulSet":
        pods = sorted(pods, key=lambda p: int(p.metadata.name.split("-")[-1]))

    # -----------------------------
    # Batch Strategy
    # -----------------------------
    if batch_size <= 0:
        print("[MODE] All-at-once eviction")
        batches = [pods]

    elif batch_size == 1:
        print("[MODE] One-by-one eviction")
        batches = [[p] for p in pods]

    else:
        print(f"[MODE] Batch eviction (size={batch_size})")
        batches = [
            pods[i:i + batch_size]
            for i in range(0, len(pods), batch_size)
        ]

    # -----------------------------
    # Execute Evacuation
    # -----------------------------
    for idx, batch in enumerate(batches, start=1):
        print(f"\n[BATCH {idx}/{len(batches)}] Processing {len(batch)} pods")

        # Evict
        for pod in batch:
            evict_pod(pod)

        # Wait for replacements
        for pod in batch:
            wait_for_replacement(pod)

        # -----------------------------
        # POST-CHECK
        # -----------------------------
        wait_until_desired_state(kind, name, ns, label_selector)


# -----------------------------
# Main
# -----------------------------

def main():
    parser = argparse.ArgumentParser(description="Advanced K8s Node Evacuator")

    parser.add_argument("--node", required=True)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--uncordon", action="store_true")

    args = parser.parse_args()

    global DEFAULT_TIMEOUT, DEFAULT_RETRIES
    DEFAULT_TIMEOUT = args.timeout
    DEFAULT_RETRIES = args.retries

    cordon_node(args.node)

    pods = get_pods_on_node(args.node)
    print(f"[INFO] Found {len(pods)} pods")

    groups = group_by_owner(pods)

    for (kind, name, ns), group_pods in groups.items():
        evacuate_group(kind, name, ns, group_pods, args.batch_size)

    if args.uncordon:
        uncordon_node(args.node)

    print("\n[DONE] Evacuation completed successfully")


if __name__ == "__main__":
    main()