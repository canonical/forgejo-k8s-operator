#!/usr/bin/env python3
# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

import logging
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


def _wait_for_forgejo_ready(ops_test: OpsTest, pod: str, timeout: int = 60) -> None:
    """Block until Forgejo's health endpoint returns 200 inside *pod*.

    Uses wget (present in Forgejo's Alpine image) to probe /api/healthz.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "kubectl",
                "exec",
                "-n",
                ops_test.model_name,
                pod,
                "-c",
                "forgejo",
                "--",
                "wget",
                "-qO",
                "/dev/null",
                "http://localhost:3000/api/healthz",
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise TimeoutError(f"Forgejo in {pod} not healthy after {timeout}s")


@pytest_asyncio.fixture(scope="module")
async def deployed_app(ops_test: OpsTest):
    """Build and deploy the full charm stack; yield the forgejo-k8s Application object."""
    local_src = ops_test.tmp_path / "charm-src"
    if not local_src.exists():
        shutil.copytree(
            ".",
            local_src,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", ".tox", "parts", "stage", "prime", "*.charm"),
        )

    charm = await ops_test.build_charm(local_src)
    resources = {"forgejo-image": METADATA["resources"]["forgejo-image"]["upstream-source"]}

    await ops_test.model.deploy(charm, resources=resources, application_name=APP_NAME)

    # Deploy postgresql-k8s and relate it (required relation)
    await ops_test.model.deploy("postgresql-k8s", channel="14/stable", trust=True)
    await ops_test.model.integrate(f"{APP_NAME}:database", "postgresql-k8s:database")

    await ops_test.model.wait_for_idle(
        apps=["postgresql-k8s"],
        status="active",
        raise_on_blocked=True,
        timeout=1000,
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "postgresql-k8s"],
        status="active",
        raise_on_blocked=False,
        timeout=1000,
    )

    yield ops_test.model.applications[APP_NAME]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(deployed_app):
    """Assert the deployed charm and its dependencies reach active status."""
    assert deployed_app.status == "active"


async def test_metrics_bearer_token(ops_test: OpsTest, deployed_app):
    """Verify Forgejo enforces bearer-token auth on /metrics when configured."""
    token = secrets.token_hex(16)

    # Create a Juju user secret and grant it to the application.
    return_code, stdout, stderr = await ops_test.juju(
        "add-secret", "forgejo-metrics-token", f"value={token}"
    )
    assert return_code == 0, f"add-secret failed: {stderr}"
    secret_id = stdout.strip()
    logger.info("Created Juju secret %s", secret_id)

    return_code, _, stderr = await ops_test.juju("grant-secret", secret_id, APP_NAME)
    assert return_code == 0, f"grant-secret failed: {stderr}"

    await deployed_app.set_config({"forgejo__metrics__token": secret_id})
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=120)

    # Wait until Forgejo is actually serving HTTP inside the pod.
    forgejo_pod = f"{APP_NAME}-0"
    _wait_for_forgejo_ready(ops_test, forgejo_pod)

    # In microk8s, pod IPs are directly routable from the host.
    pod_ip_result = subprocess.run(
        [
            "kubectl",
            "get",
            "pod",
            "-n",
            ops_test.model_name,
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
    await deployed_app.reset_config(["forgejo__metrics__token"])
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=120)
