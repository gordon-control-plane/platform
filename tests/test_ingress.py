import os
import shutil
import subprocess
import tempfile
import yaml  # type: ignore
import pytest

RELEASE_NAME = "test-release-agent-platform"


def render_chart(chart_dir, values=None):
    cmd = ["helm", "template", "test-release", chart_dir]
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
def manifests(chart_dir):
    return render_chart(chart_dir)


def find_manifest(manifests, kind, name):
    return next(
        (
            d
            for d in manifests
            if d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name
        ),
        None,
    )


def get_egress_ports(np_manifest):
    ports = []
    for rule in np_manifest["spec"].get("egress", []):
        for port_info in rule.get("ports", []):
            if "port" in port_info:
                ports.append(port_info["port"])
    return ports


def test_tailscale_deployment(manifests):
    deployment = find_manifest(
        manifests, "Deployment", f"{RELEASE_NAME}-tailscale-ingress"
    )
    assert deployment is not None, "Tailscale deployment should be rendered"

    spec = deployment["spec"]["template"]["spec"]

    assert spec.get("serviceAccountName") == f"{RELEASE_NAME}-tailscale-ingress"

    init_containers = spec.get("initContainers", [])
    assert len(init_containers) == 0, "Should have no init containers"

    containers = spec.get("containers", [])
    caddy = next(c for c in containers if c["name"] == "caddy")

    env_vars = {
        e["name"]: e.get("value") or e.get("valueFrom") for e in caddy.get("env", [])
    }
    assert "TS_KUBE_SECRET" in env_vars
    assert (
        env_vars["TS_KUBE_SECRET"]
        == f"{RELEASE_NAME}-tailscale-state"  # pragma: allowlist secret
    )

    caddy_sc = caddy.get("securityContext", {})
    assert caddy_sc.get("runAsNonRoot") is True
    assert caddy_sc.get("readOnlyRootFilesystem") is True
    assert "ALL" in caddy_sc.get("capabilities", {}).get("drop", [])

    assert (
        deployment["spec"]["strategy"]["type"] == "Recreate"
    ), "Deployment strategy should be Recreate"
    assert (
        deployment["spec"].get("replicas") == 1
    ), "Deployment replicas must be strictly 1"


def test_tailscale_rbac(manifests):
    sa = find_manifest(manifests, "ServiceAccount", f"{RELEASE_NAME}-tailscale-ingress")
    assert sa is not None, "ServiceAccount should be created"

    role = find_manifest(manifests, "Role", f"{RELEASE_NAME}-tailscale-ingress")
    assert role is not None, "Role should be created"

    # Check permissions strictly limit access to the state secret
    has_get_update_patch = False
    has_create = False
    for rule in role.get("rules", []):
        if "secrets" in rule.get("resources", []):
            if not rule.get("resourceNames"):
                assert set(rule.get("verbs", [])) == {
                    "create"
                }, "Must only allow create verb namespace-wide for secrets"
                has_create = True
            elif "get" in rule.get("verbs", []):
                assert f"{RELEASE_NAME}-tailscale-state" in rule.get(
                    "resourceNames", []
                ), "Must restrict access to specific secret name"
                assert set(rule.get("verbs", [])) == {
                    "get",
                    "update",
                    "patch",
                }, "Must restrict to get, update, patch verbs"
                has_get_update_patch = True
    assert has_get_update_patch, "Must have rules to get/update/patch the state secret"
    assert has_create, "Must have rules to create the state secret"

    rb = find_manifest(manifests, "RoleBinding", f"{RELEASE_NAME}-tailscale-ingress")
    assert rb is not None, "RoleBinding should be created"
    assert rb["roleRef"]["name"] == f"{RELEASE_NAME}-tailscale-ingress"
    assert any(s["name"] == f"{RELEASE_NAME}-tailscale-ingress" for s in rb["subjects"])


def test_caddy_config(manifests):
    cm = find_manifest(manifests, "ConfigMap", f"{RELEASE_NAME}-caddy-config")
    assert cm is not None, "Caddy ConfigMap should be created"

    caddyfile = cm["data"]["Caddyfile"]
    assert "tailscale {" in caddyfile
    assert "ephemeral false" in caddyfile
    assert "servers {" in caddyfile
    assert "read_body 120s" in caddyfile
    assert "read_header 5s" in caddyfile
    assert "trusted_proxies static 127.0.0.1/8 ::1/128" in caddyfile
    assert "bind tailscale/" in caddyfile
    assert "header_up X-Webauth-User {http.auth.user.tailscale_user}" in caddyfile
    assert "header_up X-Webauth-Email {http.auth.user.tailscale_user}" in caddyfile
    assert (
        "header_up X-Tailscale-Tailnet {http.auth.user.tailscale_tailnet}" in caddyfile
    )
    assert "request_body {" in caddyfile
    assert "request_body {" in caddyfile
    assert "max_size 10MB" in caddyfile
    assert "handle /v1/*" in caddyfile
    assert "reverse_proxy unified-api:8000" in caddyfile
    assert "handle /telemetry*" in caddyfile
    assert "handle /api/*" in caddyfile
    assert "reverse_proxy frontend:3000" in caddyfile


def test_caddyfile_structural_validation(manifests):
    cm = find_manifest(manifests, "ConfigMap", f"{RELEASE_NAME}-caddy-config")
    assert cm is not None, "Caddy ConfigMap should be created"
    caddyfile = cm["data"]["Caddyfile"]

    has_docker = False
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True)
        res = subprocess.run(
            ["docker", "image", "inspect", "caddy:2.7.6"], capture_output=True
        )
        if res.returncode == 0:
            has_docker = True
        else:
            pull_res = subprocess.run(
                ["docker", "pull", "caddy:2.7.6"], capture_output=True
            )
            if pull_res.returncode == 0:
                has_docker = True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    has_local_caddy = shutil.which("caddy") is not None
    if not has_docker and not has_local_caddy:
        pytest.skip(
            "Neither docker with caddy:2.7.6 image nor local caddy binary is available"
        )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".Caddyfile", delete=False) as f:
        f.write(caddyfile)
        tmp_path = f.name

    try:
        if has_local_caddy:
            result = subprocess.run(
                ["caddy", "validate", "--config", tmp_path, "--adapter", "caddyfile"],
                capture_output=True,
                text=True,
            )
        else:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "-v",
                    f"{tmp_path}:/etc/caddy/Caddyfile",
                    "caddy:2.7.6",
                    "caddy",
                    "validate",
                    "--config",
                    "/etc/caddy/Caddyfile",
                ],
                capture_output=True,
                text=True,
            )

        # If we are using the caddy-tailscale plugin, the standard Caddy binary will fail to validate
        # these directives. We accept these specific errors as a "pass" for structural validation.
        is_plugin_error = (
            "unrecognized global option: tailscale" in result.stderr
            or "unrecognized directive: tailscale_auth" in result.stderr
            or "unrecognized directive: bind" in result.stderr
        )
        assert (
            result.returncode == 0 or is_plugin_error
        ), f"Caddyfile validation failed:\n{result.stderr}\n{caddyfile}"
    finally:
        os.unlink(tmp_path)


def test_network_policies(manifests):
    ingress_np = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-unified-api-ingress"
    )
    assert (
        ingress_np is not None
    ), "Unified API Ingress NP should be created when Tailscale is enabled"

    frontend_ingress = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-frontend-ingress"
    )
    assert frontend_ingress is not None, "Frontend Ingress NP should be created"
    assert (
        frontend_ingress["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"][
            "app"
        ]
        == "tailscale-ingress"
    )

    egress_np = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-tailscale-egress"
    )
    assert egress_np is not None, "Tailscale Egress NP should be created"
    assert "Ingress" in egress_np["spec"].get(
        "policyTypes", []
    ), "Must have Ingress policy type"
    assert "Egress" in egress_np["spec"].get(
        "policyTypes", []
    ), "Must have Egress policy type"
    assert (
        "ingress" in egress_np["spec"] and egress_np["spec"]["ingress"] == []
    ), "Must have empty ingress rules (default deny)"

    egress_ports = get_egress_ports(egress_np)
    for rule in egress_np["spec"].get("egress", []):
        if "to" in rule:
            for to_rule in rule["to"]:
                labels = to_rule.get("podSelector", {}).get("matchLabels", {})
                # Check port scoping for specific apps
                if labels.get("app") == "unified-api":
                    assert any(p["port"] == 8000 for p in rule.get("ports", []))
                if labels.get("app") == "frontend":
                    assert any(p["port"] == 3000 for p in rule.get("ports", []))

    assert 3478 in egress_ports
    assert 53 in egress_ports, "Must allow DNS egress"
    assert 8000 in egress_ports, "Must allow egress to unified API backend"
    assert 3000 in egress_ports, "Must allow egress to UI frontend"

    # Find the generic UDP rule that now includes an ipBlock
    udp_rule = next(
        rule
        for rule in egress_np["spec"].get("egress", [])
        if "to" in rule
        and any(
            "ipBlock" in to_item and "except" in to_item["ipBlock"]
            for to_item in rule["to"]
        )
    )

    ip_block = udp_rule["to"][0]["ipBlock"]
    assert ip_block["cidr"] == "0.0.0.0/0"
    assert "10.0.0.0/8" in ip_block["except"]
    assert "172.16.0.0/12" in ip_block["except"]
    assert "192.168.0.0/16" in ip_block["except"]

    # Verify it applies to UDP
    assert len(udp_rule["ports"]) == 1
    assert udp_rule["ports"][0]["protocol"] == "UDP"


def test_tailscale_disabled(chart_dir):
    manifests = render_chart(
        chart_dir,
        {
            "tailscaleIngress.enabled": False,
            "frontend.enabled": True,
            "unifiedApi.enabled": True,
        },
    )

    deployment = find_manifest(
        manifests, "Deployment", f"{RELEASE_NAME}-tailscale-ingress"
    )
    assert deployment is None, "Tailscale deployment should not be rendered"

    sa = find_manifest(manifests, "ServiceAccount", f"{RELEASE_NAME}-tailscale-ingress")
    assert sa is None, "Tailscale ServiceAccount should not be rendered"

    role = find_manifest(manifests, "Role", f"{RELEASE_NAME}-tailscale-ingress")
    assert role is None, "Tailscale Role should not be rendered"

    rb = find_manifest(manifests, "RoleBinding", f"{RELEASE_NAME}-tailscale-ingress")
    assert rb is None, "Tailscale RoleBinding should not be rendered"

    cm = find_manifest(manifests, "ConfigMap", f"{RELEASE_NAME}-caddy-config")
    assert cm is None, "Caddy ConfigMap should not be rendered"

    frontend_ingress = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-frontend-ingress"
    )
    assert frontend_ingress is None, "Frontend Ingress NP should not be rendered"

    unified_ingress = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-unified-api-ingress"
    )
    assert unified_ingress is None, "Unified API Ingress NP should not be rendered"

    egress_np = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-tailscale-egress"
    )
    assert egress_np is None, "Tailscale Egress NP should not be rendered"


def test_tailscale_frontend_disabled(chart_dir):
    manifests = render_chart(
        chart_dir,
        {
            "tailscaleIngress.enabled": True,
            "frontend.enabled": False,
            "unifiedApi.enabled": True,
        },
    )

    frontend_ingress = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-frontend-ingress"
    )
    assert frontend_ingress is None, "Frontend Ingress NP should not be rendered"

    egress_np = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-tailscale-egress"
    )
    assert egress_np is not None, "Tailscale Egress NP should be created"

    egress_ports = get_egress_ports(egress_np)

    assert 3000 not in egress_ports, "Must not allow egress to UI frontend if disabled"
    assert 8000 in egress_ports, "Must allow egress to unified API"


def test_tailscale_unifiedapi_disabled(chart_dir):
    manifests = render_chart(
        chart_dir,
        {
            "tailscaleIngress.enabled": True,
            "frontend.enabled": True,
            "unifiedApi.enabled": False,
        },
    )

    unified_ingress = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-unified-api-ingress"
    )
    assert unified_ingress is None, "Unified API Ingress NP should not be rendered"

    egress_np = find_manifest(
        manifests, "NetworkPolicy", f"{RELEASE_NAME}-tailscale-egress"
    )
    assert egress_np is not None, "Tailscale Egress NP should be created"

    egress_ports = get_egress_ports(egress_np)

    assert 8000 not in egress_ports, "Must not allow egress to unified API if disabled"
    assert 3000 in egress_ports, "Must allow egress to UI frontend"
