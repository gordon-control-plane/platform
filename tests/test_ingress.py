import subprocess
import yaml  # type: ignore
import pytest


def render_chart():
    subprocess.run(
        ["helm", "dependency", "update", "charts/agent-platform"],
        capture_output=True,
        check=True,
    )
    result = subprocess.run(
        ["helm", "template", "test-release", "charts/agent-platform"],
        capture_output=True,
        text=True,
        check=True,
    )
    # Parse all YAML documents
    docs = []
    for doc in yaml.safe_load_all(result.stdout):
        if doc:
            docs.append(doc)
    return docs


@pytest.fixture(scope="module")
def manifests():
    return render_chart()


def test_tailscale_deployment(manifests):
    deployment = next(
        (
            d
            for d in manifests
            if d.get("kind") == "Deployment"
            and d["metadata"]["name"] == "test-release-agent-platform-tailscale-ingress"
        ),
        None,
    )
    assert deployment is not None, "Tailscale deployment should be rendered"

    spec = deployment["spec"]["template"]["spec"]

    assert (
        spec.get("serviceAccountName")
        == "test-release-agent-platform-tailscale-ingress"
    )

    init_containers = spec.get("initContainers", [])
    assert (
        len(init_containers) >= 2
    ), "Should have tailscale and configurator init containers"

    tailscale = next(c for c in init_containers if c["name"] == "tailscale")
    assert (
        tailscale.get("restartPolicy") == "Always"
    ), "Tailscale must be a native sidecar with restartPolicy: Always"

    env_vars = {
        e["name"]: e.get("value") or e.get("valueFrom")
        for e in tailscale.get("env", [])
    }
    assert "TS_KUBE_SECRET" in env_vars
    assert (
        env_vars["TS_KUBE_SECRET"]
        == "test-release-agent-platform-tailscale-state"  # pragma: allowlist secret
    )
    ts_sc = tailscale.get("securityContext", {})
    assert ts_sc.get("runAsNonRoot") is True
    assert "ALL" in ts_sc.get("capabilities", {}).get("drop", [])

    configurator = next(
        c for c in init_containers if c["name"] == "tailscale-serve-configurator"
    )
    args = configurator.get("args", [""])[0]
    assert "tailscale serve" in args
    assert "tail -f /dev/null" not in args, "Configurator should not block indefinitely"
    sc = configurator.get("securityContext", {})
    assert sc.get("runAsNonRoot") is True
    assert sc.get("readOnlyRootFilesystem") is True
    assert sc.get("allowPrivilegeEscalation") is False
    assert "ALL" in sc.get("capabilities", {}).get("drop", [])

    caddy = next(c for c in spec.get("containers", []) if c["name"] == "caddy")
    caddy_sc = caddy.get("securityContext", {})
    assert caddy_sc.get("runAsNonRoot") is True
    assert caddy_sc.get("readOnlyRootFilesystem") is True
    assert "ALL" in caddy_sc.get("capabilities", {}).get("drop", [])

    assert (
        deployment["spec"]["strategy"]["type"] == "Recreate"
    ), "Deployment strategy should be Recreate"


def test_tailscale_rbac(manifests):
    sa = next(
        (
            d
            for d in manifests
            if d.get("kind") == "ServiceAccount"
            and d["metadata"]["name"] == "test-release-agent-platform-tailscale-ingress"
        ),
        None,
    )
    assert sa is not None, "ServiceAccount should be created"

    role = next(
        (
            d
            for d in manifests
            if d.get("kind") == "Role"
            and d["metadata"]["name"] == "test-release-agent-platform-tailscale-ingress"
        ),
        None,
    )
    assert role is not None, "Role should be created"

    # Check permissions strictly limit access to the state secret
    has_get_update_patch = False
    for rule in role.get("rules", []):
        if "secrets" in rule.get("resources", []) and "get" in rule.get("verbs", []):
            assert "test-release-agent-platform-tailscale-state" in rule.get(
                "resourceNames", []
            ), "Must restrict access to specific secret name"
            has_get_update_patch = True
    assert has_get_update_patch, "Must have rules to get/update/patch the state secret"


def test_caddy_config(manifests):
    cm = next(
        (
            d
            for d in manifests
            if d.get("kind") == "ConfigMap"
            and d["metadata"]["name"] == "test-release-agent-platform-caddy-config"
        ),
        None,
    )
    assert cm is not None, "Caddy ConfigMap should be created"

    caddyfile = cm["data"]["Caddyfile"]
    assert "auto_https off" in caddyfile
    assert "read_body 120s" in caddyfile
    assert "trusted_proxies static 127.0.0.1/8 ::1/128" in caddyfile
    assert "request_body {" in caddyfile
    assert "max_size 10MB" in caddyfile


def test_network_policies(manifests):
    ingress_np = next(
        (
            d
            for d in manifests
            if d.get("kind") == "NetworkPolicy"
            and d["metadata"]["name"]
            == "test-release-agent-platform-unified-api-ingress"
        ),
        None,
    )
    assert (
        ingress_np is not None
    ), "Unified API Ingress NP should be created when Tailscale is enabled"

    egress_np = next(
        (
            d
            for d in manifests
            if d.get("kind") == "NetworkPolicy"
            and d["metadata"]["name"] == "test-release-agent-platform-tailscale-egress"
        ),
        None,
    )
    assert egress_np is not None, "Tailscale Egress NP should be created"

    egress_ports = []
    for rule in egress_np["spec"].get("egress", []):
        for port_info in rule.get("ports", []):
            egress_ports.append(port_info["port"])

    assert 443 in egress_ports
    assert 3478 in egress_ports
    assert 53 in egress_ports
    assert 8000 in egress_ports, "Must allow egress to unified API backend"
    assert 3000 in egress_ports, "Must allow egress to UI frontend"
