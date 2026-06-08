#!/usr/bin/env python3

import logging
from pathlib import Path

import jubilant
import pytest
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.fixture(scope="module")
def deployed_app(charm: Path, juju: jubilant.Juju, forgejo_image):
    """Deploy forgejo-k8s with pgbouncer-k8s as a database proxy."""
    juju.deploy(
        charm,
        APP_NAME,
        resources={"forgejo-image": forgejo_image},
    )
    juju.deploy("postgresql-k8s", channel="14/stable", trust=True)
    juju.deploy("pgbouncer-k8s", channel="1/stable", trust=True)

    juju.integrate("pgbouncer-k8s:backend-database", "postgresql-k8s:database")
    juju.integrate(f"{APP_NAME}:database", "pgbouncer-k8s:database")

    juju.wait(
        lambda status: jubilant.all_active(status, APP_NAME, "pgbouncer-k8s", "postgresql-k8s"),
        timeout=300,
    )

    yield APP_NAME


def test_pgbouncer_database_proxy(deployed_app, juju: jubilant.Juju):
    """Verify Forgejo reaches active status when its database is proxied through pgbouncer-k8s."""
    status = juju.status()
    assert jubilant.all_active(status, deployed_app, "pgbouncer-k8s", "postgresql-k8s")
