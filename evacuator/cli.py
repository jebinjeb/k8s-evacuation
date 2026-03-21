# cli.py
#!/usr/bin/env python3
import argparse
import logging
from kubernetes import config
from k8s import cordon_node, uncordon_node, get_pods_on_node, group_by_owner
from evacuator import evacuate_group
from metrics import ProgressTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300
DEFAULT_RETRIES = 5

def main():
    config.load_kube_config()
    parser = argparse.ArgumentParser(description="Advanced K8s Node Evacuator")
    parser.add_argument("--node", required=True)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--uncordon", action="store_true")
    parser.add_argument("--pushgateway")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--eviction-strategy", choices=["high", "low"], default="high")
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        exit(1)

    cordon_node(args.node, dry_run=args.dry_run)
    try:
        pods = get_pods_on_node(args.node)
        log.info(f"Found {len(pods)} pods")
        groups = group_by_owner(pods)
        tracker = ProgressTracker(total=len(pods), pushgateway=args.pushgateway)
        for (kind, name, ns), group_pods in groups.items():
            evacuate_group(kind, name, ns, group_pods, args.batch_size, tracker, args.dry_run, strategy=args.eviction_strategy)
    finally:
        if args.uncordon:
            uncordon_node(args.node, dry_run=args.dry_run)

    log.info("Evacuation completed successfully")

if __name__ == "__main__":
    main()