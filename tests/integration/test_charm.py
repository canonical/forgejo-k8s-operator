#!/usr/bin/env python3
# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

import logging
import shutil
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    # Copy source to a local temp directory
    local_src = ops_test.tmp_path / "charm-src"
    shutil.copytree(
        ".",
        local_src,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", ".tox", "parts", "stage", "prime", "*.charm"),
    )

    # Build and deploy charm from local source folder
    charm = await ops_test.build_charm(local_src)
    resources = {"forgejo-image": METADATA["resources"]["forgejo-image"]["upstream-source"]}

    await ops_test.model.deploy(charm, resources=resources, application_name=APP_NAME)

    # Deploy postgresql-k8s and relate it (required relation)
    await ops_test.model.deploy("postgresql-k8s", channel="14/stable", trust=True)
    await ops_test.model.integrate(f"{APP_NAME}:database", "postgresql-k8s:database")

    # Wait for postgresql to be ready first, then check both apps together.
    await ops_test.model.wait_for_idle(
        apps=["postgresql-k8s"],
        status="active",
        raise_on_blocked=True,
        timeout=1000,
    )

    # forgejo-k8s is expected to be blocked while postgresql is still allocating.
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "postgresql-k8s"],
        status="active",
        raise_on_blocked=False,
        timeout=1000,
    )
