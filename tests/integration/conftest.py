#!/usr/bin/env python3

import os
import pathlib

import jubilant
import pytest
import yaml

DEFAULT_FORGEJO_IMAGE = "codeberg.org/forgejo/forgejo:16.0.6"

METADATA = yaml.safe_load(pathlib.Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


def pytest_addoption(parser):
    parser.addoption(
        "--forgejo-image",
        action="store",
        default=None,
        help="OCI image for the forgejo-image resource (overrides FORGEJO_IMAGE env var).",
    )


@pytest.fixture(scope="session")
def forgejo_image(request):
    """Resolve the Forgejo OCI image reference.

    Priority: --forgejo-image CLI option > FORGEJO_IMAGE env var > default tag.
    """
    return (
        request.config.getoption("--forgejo-image")
        or os.environ.get("FORGEJO_IMAGE")
        or DEFAULT_FORGEJO_IMAGE
    )


@pytest.fixture(scope="session")
def charm():
    """Return the path of the packed charm under test."""
    charm_path = os.environ.get("CHARM_PATH")
    if not charm_path:
        charm_dir = pathlib.Path()
        charms = list(charm_dir.glob("*.charm"))
        assert charms, f"No charms found in {charm_dir.absolute()}"
        assert len(charms) == 1, f"Found more than one charm: {charms}"
        charm_path = charms[0]
    path = pathlib.Path(charm_path).resolve()
    assert path.is_file(), f"{path} is not a file"
    return path


@pytest.fixture(scope="module")
def deployed_app(charm: pathlib.Path, juju: jubilant.Juju, forgejo_image):
    """Deploy forgejo-k8s with its required database backend; yield the app name.

    Shared by the tests that only need a plain forgejo-k8s + postgresql-k8s
    deployment.
    """
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
