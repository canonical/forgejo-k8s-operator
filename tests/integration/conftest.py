#!/usr/bin/env python3

import os
import pathlib

import pytest

DEFAULT_FORGEJO_IMAGE = "codeberg.org/forgejo/forgejo:15"


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
