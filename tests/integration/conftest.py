#!/usr/bin/env python3

import os

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
