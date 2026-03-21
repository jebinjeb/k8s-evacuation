# metrics.py
import logging
try:
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False

log = logging.getLogger(__name__)

class ProgressTracker:
    def __init__(self, total, pushgateway=None):
        self.total = total
        self.evicted = 0
        self.ready = 0
        self.failed = 0
        self.pushgateway = pushgateway

        if pushgateway and METRICS_AVAILABLE:
            self.registry = CollectorRegistry()
            self.metric = Gauge(
                'evacuation_pod_status',
                'Pod evacuation status',
                ['pod', 'namespace', 'status'],
                registry=self.registry
            )
        else:
            self.registry = None

    def log(self):
        log.info(f"[PROGRESS] total={self.total} evicted={self.evicted} ready={self.ready} failed={self.failed}")

    def update_metrics(self, pod, ns, status):
        if self.registry:
            self.metric.labels(pod=pod, namespace=ns, status=status).set(1)
            push_to_gateway(self.pushgateway, job="k8s_evacuator", registry=self.registry)