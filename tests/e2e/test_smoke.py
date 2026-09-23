import subprocess

import pytest

NAMESPACE = "gordon"


def get_current_cluster():
    """Get the active Kube API URL to prevent running E2E against remote clusters."""
    try:
        res = subprocess.run(
            [
                "kubectl",
                "config",
                "view",
                "--minify",
                "-o",
                "jsonpath={.clusters[0].cluster.server}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError:
        return None


@pytest.fixture(autouse=True, scope="session")
def ensure_local_cluster():
    """Ensure E2E tests ONLY run against localhost/127.0.0.1 unless overridden."""
    cluster = get_current_cluster()
    if not cluster:
        pytest.skip("No Kubernetes cluster accessible.")

    if cluster and "127.0.0.1" not in cluster and "localhost" not in cluster:
        pytest.exit(
            f"ABORT: Active cluster is {cluster}. E2E tests MUST NOT run against remote clusters to prevent accidental production impact. Switch to kind or set KUBECONFIG."
        )


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


@pytest.mark.e2e
@pytest.mark.parametrize(
    "label_selector",
    [
        "app.kubernetes.io/name=postgres-operator",
        "application=spilo",
        "app=langfuse",
        "app.kubernetes.io/name=temporal",
        "app=unified-api",
        "app=litellm",
        "app=workers",
    ],
)
def test_pod_running(label_selector: str):
    check_pod_ready(label_selector)


@pytest.mark.e2e
def test_worker_egress_policy_blocks_external():
    """Ported from smoke_test.sh: Verifies NetworkPolicy drops external egress from worker pod."""
    # Get the worker pod name
    pod_cmd = [
        "kubectl",
        "get",
        "pod",
        "-l",
        "app=workers",
        "-n",
        NAMESPACE,
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ]
    worker_pod = ""
    try:
        pod_res = subprocess.run(pod_cmd, check=True, capture_output=True, text=True)
        worker_pod = pod_res.stdout.strip()
    except subprocess.CalledProcessError:
        pytest.fail(
            f"No worker pod found matching app=workers in namespace {NAMESPACE}"
        )

    if not worker_pod:
        pytest.fail(
            f"No worker pod found matching app=workers in namespace {NAMESPACE}"
        )

    # Exec into the pod and try to curl google.com
    exec_cmd = [
        "kubectl",
        "exec",
        worker_pod,
        "-n",
        NAMESPACE,
        "python3",
        "-c",
        'import urllib.request; urllib.request.urlopen("https://google.com", timeout=2)',
    ]

    res = subprocess.run(exec_cmd, capture_output=True, text=True, check=False)

    # The request SHOULD fail (timeout/connection refused) if the NetworkPolicy is working
    if res.returncode == 0:
        pytest.fail(
            "Smoke test failed: NetworkPolicy is NOT dropping external egress from worker pod!"
        )
    else:
        assert (
            "TimeoutError" in res.stderr
            or "URLError" in res.stderr
            or "timeout" in res.stderr.lower()
        ), f"Expected a timeout/network error, got: {res.stderr}"
