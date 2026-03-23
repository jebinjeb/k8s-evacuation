# evacuator.py
import logging
from k8s import wait_until_desired_state, get_pod_resource_score, evict_pod, wait_for_replacement

log = logging.getLogger(__name__)

def evacuate_group(kind, name, ns, pods, batch_size, tracker, dry_run, strategy="high", max_batches=None):
    if kind == "orphan":
        log.warning(f"[SKIP] Orphan workload {name}")
        return

    if not pods:
        log.warning(f"[SKIP] No pods for {kind}/{name}")
        return

    # Apply max_batches only for spread strategy
    if strategy != "spread":
        max_batches = None

    owner_uid = pods[0].metadata.owner_references[0].uid
    log.info(f"[GROUP] {kind}/{name} ({ns}) - {len(pods)} pods")

    wait_until_desired_state(kind, name, ns, owner_uid)

    reverse = strategy == "high"

    # Sorting
    if kind == "StatefulSet":
        try:
            pods = sorted(pods, key=lambda p: int(p.metadata.name.split("-")[-1]))
            log.info("[ORDER] StatefulSet ordinal order applied")
        except Exception:
            log.warning("Failed to sort StatefulSet pods")
    else:
        pods = sorted(pods, key=get_pod_resource_score, reverse=reverse)
        log.info(f"[ORDER] Pods sorted by resource usage ({'high→low' if reverse else 'low→high'})")

    # Batching
    if batch_size <= 0:
        batches = [pods]
    elif batch_size == 1:
        batches = [[p] for p in pods]
    else:
        batches = [pods[i:i + batch_size] for i in range(0, len(pods), batch_size)]

    # Evacuation loop
    batch_count = 0

    for batch in batches:
        if max_batches is not None and batch_count >= max_batches:
            log.info(f"[STOP] Reached max_batches={max_batches}, exiting early ({len(batches)-batch_count} batches remaining)")
            break

        log.info(f"[BATCH {batch_count+1}] Processing {len(batch)} pods")

        for pod in batch:
            evict_pod(pod, dry_run=dry_run)
            tracker.evicted += 1
            tracker.log()

        if not dry_run:
            for pod in batch:
                wait_for_replacement(pod, pod.spec.node_name)

        wait_until_desired_state(kind, name, ns, owner_uid)

        batch_count += 1

    return