#!/usr/bin/env python3

"""Integration test covering the ingress endpoint (requires, traefik_route)."""

import logging

import jubilant

logger = logging.getLogger(__name__)


def test_ingress_relation(deployed_app, juju: jubilant.Juju):
    """Verify Forgejo stays active when related to traefik-k8s over ingress."""
    juju.deploy(
        "traefik-k8s",
        "traefik-k8s",
        channel="latest/stable",
        trust=True,
        config={"external_hostname": "internal"},
    )
    juju.integrate(f"{deployed_app}:ingress", "traefik-k8s:traefik-route")

    # Traefik may stay "waiting" for a LoadBalancer address on CI's microk8s; that is
    # unrelated to Forgejo's ingress integration.
    def ready(status):
        traefik = status.apps["traefik-k8s"].app_status
        return jubilant.all_active(status, deployed_app) and (
            traefik.current == "active" or "external address" in traefik.message
        )

    status = juju.wait(ready, timeout=600)
    assert jubilant.all_active(status, deployed_app)
    assert not jubilant.any_error(status, "traefik-k8s")
