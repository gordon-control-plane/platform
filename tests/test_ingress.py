import subprocess
import yaml  # type: ignore
import pytest


def render_chart(values=None):
    cmd = ["helm", "template", "test-release", "charts/agent-platform"]
    if values:
        for k, v in values.items():
            if isinstance(v, bool):
                v = str(v).lower()
            cmd.extend(["--set", f"{k}={v}"])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )
    # Parse all YAML documents
    return list(filter(None, yaml.safe_load_all(result.stdout)))


@pytest.fixture(scope="module")
def manifests():
    return render_chart()


def find_manifest(manifests, kind, name):
    return next(
        (
            d
            for d in manifests
            if d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name
        ),
        None,
    )


def test_tailscale_deployment(manifests):
    deployment = find_manifest(
        manifests, "Deployment", "test-release-agent-platform-tailscale-ingress"
    )
    assert deployment is not None, "Tailscale deployment should be rendered"

    spec = deployment["spec"]["template"]["spec"]

    assert (
        spec.get("serviceAccountName")
        == "test-release-agent-platform-tailscale-ingress"
    )

    init_containers = spec.get("initContainers", [])
    assert len(init_containers) >= 1, "Should have tailscale init container"

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

    containers = spec.get("containers", [])
    configurator = next(
        c for c in containers if c["name"] == "tailscale-serve-configurator"
    )
    args = configurator.get("args", [""])[0]
    assert "while true; do" in args
    assert "tailscale serve" in args
    sc = configurator.get("securityContext", {})
    assert sc.get("runAsNonRoot") is True
    assert sc.get("readOnlyRootFilesystem") is True
    assert sc.get("allowPrivilegeEscalation") is False
    assert "ALL" in sc.get("capabilities", {}).get("drop", [])

    caddy = next(c for c in containers if c["name"] == "caddy")
    caddy_sc = caddy.get("securityContext", {})
    assert caddy_sc.get("runAsNonRoot") is True
    assert caddy_sc.get("readOnlyRootFilesystem") is True
    assert "ALL" in caddy_sc.get("capabilities", {}).get("drop", [])

    assert (
        deployment["spec"]["strategy"]["type"] == "Recreate"
    ), "Deployment strategy should be Recreate"


def test_tailscale_rbac(manifests):
    sa = find_manifest(
        manifests, "ServiceAccount", "test-release-agent-platform-tailscale-ingress"
    )
    assert sa is not None, "ServiceAccount should be created"

    role = find_manifest(
        manifests, "Role", "test-release-agent-platform-tailscale-ingress"
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

    rb = find_manifest(
        manifests, "RoleBinding", "test-release-agent-platform-tailscale-ingress"
    )
    assert rb is not None, "RoleBinding should be created"
    assert rb["roleRef"]["name"] == "test-release-agent-platform-tailscale-ingress"
    assert any(
        s["name"] == "test-release-agent-platform-tailscale-ingress"
        for s in rb["subjects"]
    )


def test_caddy_config(manifests):
    cm = find_manifest(
        manifests, "ConfigMap", "test-release-agent-platform-caddy-config"
    )
    assert cm is not None, "Caddy ConfigMap should be created"

    caddyfile = cm["data"]["Caddyfile"]
    assert "auto_https off" in caddyfile
    assert "read_body 120s" in caddyfile
    assert "read_header 5s" in caddyfile
    assert "trusted_proxies static 127.0.0.1/8 ::1/128" in caddyfile
    assert "request_body {" in caddyfile
    assert "max_size 10MB" in caddyfile
    assert "handle /v1/*" in caddyfile
    assert "reverse_proxy unified-api:8000" in caddyfile
    assert "handle /telemetry*" in caddyfile
    assert "handle /api/*" in caddyfile
    assert "reverse_proxy frontend:3000" in caddyfile


def test_network_policies(manifests):
    ingress_np = find_manifest(
        manifests, "NetworkPolicy", "test-release-agent-platform-unified-api-ingress"
    )
    assert (
        ingress_np is not None
    ), "Unified API Ingress NP should be created when Tailscale is enabled"

    frontend_ingress = find_manifest(
        manifests, "NetworkPolicy", "test-release-agent-platform-frontend-ingress"
    )
    assert frontend_ingress is not None, "Frontend Ingress NP should be created"
    assert (
        frontend_ingress["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"][
            "app"
        ]
        == "tailscale-ingress"
    )

    egress_np = find_manifest(
        manifests, "NetworkPolicy", "test-release-agent-platform-tailscale-egress"
    )
    assert egress_np is not None, "Tailscale Egress NP should be created"

    egress_ports = []
    for rule in egress_np["spec"].get("egress", []):
        for port_info in rule.get("ports", []):
            if "port" in port_info:
                egress_ports.append(port_info["port"])

        if "to" in rule:
            for to_rule in rule["to"]:
                labels = to_rule.get("podSelector", {}).get("matchLabels", {})
                # Check port scoping for specific apps
                if labels.get("app") == "unified-api":
                    assert any(p["port"] == 8000 for p in rule.get("ports", []))
                if labels.get("app") == "frontend":
                    assert any(p["port"] == 3000 for p in rule.get("ports", []))

    assert 443 in egress_ports
    assert 3478 in egress_ports
    assert 8000 in egress_ports, "Must allow egress to unified API backend"
    assert 3000 in egress_ports, "Must allow egress to UI frontend"

    # Find the generic UDP rule that now includes an ipBlock
    udp_rule = next(
        rule
        for rule in egress_np["spec"].get("egress", [])
        if "to" in rule and any("ipBlock" in to_item for to_item in rule["to"])
    )

    ip_block = udp_rule["to"][0]["ipBlock"]
    assert ip_block["cidr"] == "0.0.0.0/0"
    assert "10.0.0.0/8" in ip_block["except"]
    assert "172.16.0.0/12" in ip_block["except"]
    assert "192.168.0.0/16" in ip_block["except"]

    # Verify it applies to UDP
    assert len(udp_rule["ports"]) == 1
    assert udp_rule["ports"][0]["protocol"] == "UDP"


def test_tailscale_disabled():
    manifests = render_chart(
        {
            "tailscaleIngress.enabled": False,
            "frontend.enabled": True,
            "unifiedApi.enabled": True,
        }
    )

    deployment = find_manifest(
        manifests, "Deployment", "test-release-agent-platform-tailscale-ingress"
    )
    assert deployment is None, "Tailscale deployment should not be rendered"

    sa = find_manifest(
        manifests, "ServiceAccount", "test-release-agent-platform-tailscale-ingress"
    )
    assert sa is None, "Tailscale ServiceAccount should not be rendered"

    role = find_manifest(
        manifests, "Role", "test-release-agent-platform-tailscale-ingress"
    )
    assert role is None, "Tailscale Role should not be rendered"

    rb = find_manifest(
        manifests, "RoleBinding", "test-release-agent-platform-tailscale-ingress"
    )
    assert rb is None, "Tailscale RoleBinding should not be rendered"

    cm = find_manifest(
        manifests, "ConfigMap", "test-release-agent-platform-caddy-config"
    )
    assert cm is None, "Caddy ConfigMap should not be rendered"

    frontend_ingress = find_manifest(
        manifests, "NetworkPolicy", "test-release-agent-platform-frontend-ingress"
    )
    assert frontend_ingress is None, "Frontend Ingress NP should not be rendered"

    unified_ingress = find_manifest(
        manifests, "NetworkPolicy", "test-release-agent-platform-unified-api-ingress"
    )
    assert unified_ingress is None, "Unified API Ingress NP should not be rendered"

    egress_np = find_manifest(
        manifests, "NetworkPolicy", "test-release-agent-platform-tailscale-egress"
    )
    assert egress_np is None, "Tailscale Egress NP should not be rendered"
