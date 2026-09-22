import subprocess

import pytest

NAMESPACE = "gordon"


def check_pod_ready(label_selector: str):
    """Wait for a pod matching the label selector to be ready."""
    cmd = [
        "kubectl",
        "wait",
        "--for=condition=ready",
        "pod",
        "-l",
        label_selector,
        "-n",
        NAMESPACE,
        "--timeout=120s",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        pytest.fail(f"Pod matching {label_selector} not ready. Error: {e.stderr}")


def test_postgres_operator_running():
    # Zalando postgres operator creates pods labeled app.kubernetes.io/name=postgres-operator
    check_pod_ready("app.kubernetes.io/name=postgres-operator")


def test_spilo_database_running():
    # The actual database pods are labeled application=spilo
    check_pod_ready("application=spilo")


def test_langfuse_running():
    check_pod_ready("app=langfuse")


def test_temporal_running():
    # Bitnami temporal chart usually uses app.kubernetes.io/name=temporal
    # Just check if at least one temporal component is running
    check_pod_ready("app.kubernetes.io/name=temporal")


def test_unified_api_running():
    check_pod_ready("app=unified-api")


def test_litellm_running():
    check_pod_ready("app=litellm")


def test_unified_api_health_endpoint():
    import time
    import urllib.request

    pf_cmd = [
        "kubectl",
        "port-forward",
        "svc/unified-api",
        "8000:8000",
        "-n",
        NAMESPACE,
    ]
    pf_proc = subprocess.Popen(pf_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        time.sleep(3)  # Give port-forward time to establish
        req = urllib.request.Request("http://127.0.0.1:8000/health")
        with urllib.request.urlopen(req, timeout=10) as response:
            assert response.status == 200
            data = response.read().decode("utf-8")
            assert "status" in data
    finally:
        pf_proc.terminate()
        pf_proc.wait()
