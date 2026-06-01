#!/usr/bin/env python3

import logging
import os
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import jubilant
import pytest
import requests
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
SSH_EXTERNAL_PORT = int(METADATA["config"]["options"]["forgejo__server__ssh_port"]["default"])
FORGEJO_DOMAIN = METADATA["config"]["options"]["forgejo__server__domain"]["default"]


@pytest.fixture(scope="module")
def deployed_app(charm: Path, juju: jubilant.Juju, forgejo_image):
    """Deploy the full charm stack; yield the app name."""
    juju.deploy(
        charm,
        APP_NAME,
        resources={"forgejo-image": forgejo_image},
    )
    juju.deploy("postgresql-k8s", channel="14/stable", trust=True)
    juju.integrate(f"{APP_NAME}:database", "postgresql-k8s:database")

    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME, "postgresql-k8s"),
        timeout=1000,
    )

    yield APP_NAME


@pytest.mark.juju_setup
def test_build_and_deploy(deployed_app, juju: jubilant.Juju):
    """Assert the deployed charm and its dependencies reach active status."""
    status = juju.status()
    assert status.apps[deployed_app].is_active


def test_metrics_bearer_token(deployed_app, juju: jubilant.Juju):
    """Verify Forgejo enforces bearer-token auth on /metrics when configured."""
    token = secrets.token_hex(16)

    # Create a Juju user secret and grant it to the application.
    secret_uri = juju.add_secret("forgejo-metrics-token", {"value": token})
    logger.info("Created Juju secret %s", secret_uri)
    juju.grant_secret(secret_uri, APP_NAME)

    juju.config(APP_NAME, {"forgejo__metrics__token": secret_uri})
    juju.wait(lambda status: jubilant.all_active(status, APP_NAME), timeout=120)

    # In microk8s, pod IPs are directly routable from the host.
    forgejo_pod = f"{APP_NAME}-0"
    pod_ip_result = subprocess.run(
        [
            "kubectl",
            "get",
            "pod",
            "-n",
            juju.model,
            forgejo_pod,
            "-o",
            "jsonpath={.status.podIP}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    pod_ip = pod_ip_result.stdout.strip()
    metrics_url = f"http://{pod_ip}:3000/metrics"
    logger.info("Testing /metrics at %s", metrics_url)

    # Without bearer token: Forgejo should deny the request.
    try:
        urllib.request.urlopen(metrics_url, timeout=10)
        assert False, "Expected 401 without bearer token, but got 200"
    except urllib.error.HTTPError as e:
        assert e.code == 401, f"Expected 401 without bearer token, got {e.code}"

    # With the correct bearer token: Forgejo should serve metrics.
    req = urllib.request.Request(metrics_url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200, f"Expected 200 with correct bearer token, got {resp.status}"

    # Teardown: remove the token so the deploy is clean for any later tests.
    juju.config(APP_NAME, reset=["forgejo__metrics__token"])
    juju.wait(lambda status: jubilant.all_active(status, APP_NAME), timeout=120)


def _get_traefik_lb_ip(model: str, app_name: str = "traefik-k8s") -> str:
    """Return the MetalLB LoadBalancer external IP for the traefik-k8s-lb service."""
    result = subprocess.run(
        [
            "kubectl",
            "get",
            "service",
            f"{app_name}-lb",
            "-n",
            model,
            "-o",
            "jsonpath={.status.loadBalancer.ingress[0].ip}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _wait_for_ssh_banner(host: str, port: int, timeout: int = 300, interval: int = 5) -> None:
    """Block until an SSH banner is received, proving Traefik is routing SSH to Forgejo."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5) as sock:
                sock.settimeout(5)
                data = sock.recv(256)
                if data.startswith(b"SSH-"):
                    return
        except OSError:
            pass
        time.sleep(interval)
    raise TimeoutError(f"No SSH banner from {host}:{port} after {timeout}s")


@pytest.fixture(scope="module")
def deployed_app_with_traefik(deployed_app, juju: jubilant.Juju):
    """Extend the deployed app with Traefik; yield (app_name, traefik_lb_ip)."""
    juju.deploy("traefik-k8s", channel="latest/stable", trust=True)
    juju.integrate(f"{APP_NAME}:ingress", "traefik-k8s:traefik-route")

    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME, "postgresql-k8s", "traefik-k8s"),
        timeout=600,
    )

    traefik_ip = _get_traefik_lb_ip(juju.model)
    logger.info("Traefik LoadBalancer IP: %s", traefik_ip)

    # Wait until Traefik is routing SSH to Forgejo (banner proves dynamic route is configured)
    _wait_for_ssh_banner(traefik_ip, SSH_EXTERNAL_PORT, timeout=300)
    logger.info(
        "SSH banner received on %s:%d - Traefik route is active", traefik_ip, SSH_EXTERNAL_PORT
    )

    yield APP_NAME, traefik_ip

    juju.remove_relation(f"{APP_NAME}:ingress", "traefik-k8s:traefik-route")
    juju.remove_application("traefik-k8s")


def test_ssh_push(deployed_app_with_traefik, juju: jubilant.Juju):
    """Deploy Forgejo + Traefik, then create a repo and push a commit over SSH."""
    app_name, traefik_ip = deployed_app_with_traefik
    admin_user = "testadmin"
    repo_name = "test-repo"

    # Create admin user
    juju.run(
        f"{app_name}/leader",
        "create-admin-user",
        params={"username": admin_user, "email": "admin@test.local"},
    )

    # Generate an API token for the admin
    task = juju.run(
        f"{app_name}/leader",
        "generate-user-token",
        params={
            "username": admin_user,
            "token-name": "ssh-test-token",
            "scopes": "all",
        },
    )
    token = task.results["token"]

    # Use traefik_ip for REST API calls; set Host header so Traefik routes correctly.
    api_base = f"http://{traefik_ip}/api/v1"
    auth_headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "Host": FORGEJO_DOMAIN,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = os.path.join(tmpdir, "id_ed25519")
        pub_key_path = f"{key_path}.pub"

        # Generate SSH keypair
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key_path],
            check=True,
            capture_output=True,
        )
        pub_key = Path(pub_key_path).read_text().strip()

        # Register the public key with Forgejo
        resp = requests.post(
            f"{api_base}/user/keys",
            headers=auth_headers,
            json={"key": pub_key, "read_only": False, "title": "test-key"},
            timeout=30,
        )
        assert resp.status_code == 201, f"Failed to add SSH key: {resp.text}"

        # Create a new repository
        resp = requests.post(
            f"{api_base}/user/repos",
            headers=auth_headers,
            json={"name": repo_name, "private": False, "auto_init": False},
            timeout=30,
        )
        assert resp.status_code == 201, f"Failed to create repository: {resp.text}"

        # Clone, commit, and push over SSH via Traefik's LoadBalancer IP
        repo_dir = os.path.join(tmpdir, repo_name)
        os.makedirs(repo_dir)
        ssh_cmd = f"ssh -i {key_path} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        remote_url = f"ssh://git@{traefik_ip}:{SSH_EXTERNAL_PORT}/{admin_user}/{repo_name}.git"

        env = {**os.environ, "GIT_SSH_COMMAND": ssh_cmd}
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "admin@test.local"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Admin"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial commit"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "push", "-u", "origin", "HEAD:main"],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"git push failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
