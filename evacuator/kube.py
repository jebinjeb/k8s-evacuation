from kubernetes import client, config


def load_config():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def get_clients():
    return {
        "core": client.CoreV1Api(),
        "apps": client.AppsV1Api(),
        "policy": client.PolicyV1Api(),
    }