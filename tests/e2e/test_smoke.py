import os
import subprocess
import pytest

NAMESPACE = os.environ.get("NAMESPACE", "gordon")


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


def test_ateapi_running():
    """Verify ateapi pod is ready and functionally responding."""
    check_pod_ready("app.kubernetes.io/component=ateapi")

    # Functionally test the API by getting its pod name and hitting the health/version endpoint if possible,
    # or validating it can execute a basic command. Since gRPC curl might not be available, we verify
    # the process is actively listening on the gRPC port.
    cmd_get_pod = [
        "kubectl",
        "get",
        "pods",
        "-n",
        NAMESPACE,
        "-l",
        "app.kubernetes.io/component=ateapi",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ]
    pod_name = subprocess.run(
        cmd_get_pod, check=True, capture_output=True, text=True
    ).stdout.strip()

    # Verify the process is listening on the gRPC port
    cmd_check_port = [
        "kubectl",
        "exec",
        "-n",
        NAMESPACE,
        pod_name,
        "--",
        "bash",
        "-c",
        "cat /proc/net/tcp | grep -q ':C383'",
    ]  # C383 is hex for 50051
    try:
        subprocess.run(cmd_check_port, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"ateapi pod is running but not listening on gRPC port 50051. Error: {e.stderr}"
        )


def test_atecontroller_running():
    check_pod_ready("app.kubernetes.io/component=atecontroller")


def test_atelet_running():
    check_pod_ready("app.kubernetes.io/component=atelet")


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


def test_minio_running():
    check_pod_ready("app=minio")
