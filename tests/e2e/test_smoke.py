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

@pytest.mark.parametrize(
    "label_selector",
    [
        "app.kubernetes.io/name=postgres-operator",
        "application=spilo",
        "app=langfuse",
        "app.kubernetes.io/name=temporal",
        "app=unified-api",
        "app=litellm",
    ],
)
def test_pod_running(label_selector: str):
    check_pod_ready(label_selector)
