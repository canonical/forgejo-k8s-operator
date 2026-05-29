#!/usr/bin/env python3

import logging
import secrets
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import jubilant
import pytest
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


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
