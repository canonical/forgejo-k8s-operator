"""Traefik ingress route configuration for the Forgejo K8s charm."""


def _k8s_service(app_name: str, model_name: str) -> str:
    return f"{app_name}.{model_name}.svc.cluster.local"


def _http_section(
    router_name: str,
    service_name: str,
    domain: str,
    k8s_svc: str,
    port: int,
) -> dict:
    return {
        "routers": {
            router_name: {
                "rule": f"Host(`{domain}`)",
                "service": service_name,
                "entryPoints": ["web"],
            }
        },
        "services": {
            service_name: {"loadBalancer": {"servers": [{"url": f"http://{k8s_svc}:{port}"}]}}
        },
    }


def _tls_tcp_section(
    router_name: str,
    service_name: str,
    domain: str,
    k8s_svc: str,
    port: int,
) -> dict:
    return {
        "routers": {
            router_name: {
                "rule": f"HostSNI(`{domain}`)",
                "service": service_name,
                "entryPoints": ["websecure"],
                "tls": {"passthrough": True},
            }
        },
        "services": {
            service_name: {"loadBalancer": {"servers": [{"address": f"{k8s_svc}:{port}"}]}}
        },
    }


def _ssh_tcp_section(
    router_name: str,
    service_name: str,
    k8s_svc: str,
    ssh_listen_port: int,
) -> dict:
    return {
        "routers": {
            router_name: {
                "rule": "HostSNI(`*`)",
                "service": service_name,
                "entryPoints": ["ssh"],
            }
        },
        "services": {
            service_name: {
                "loadBalancer": {"servers": [{"address": f"{k8s_svc}:{ssh_listen_port}"}]}
            }
        },
    }


def get_ssh_static_config(ssh_port: int) -> dict:
    """Return Traefik static config that declares the SSH TCP entrypoint.

    When submitted via traefik_route, the traefik-k8s charm will add this
    entrypoint to Traefik's static configuration and expose the port on
    the Kubernetes LoadBalancer service automatically.
    """
    return {"entryPoints": {"ssh": {"address": f":{ssh_port}"}}}


def get_traefik_route_config(
    model_name: str,
    app_name: str,
    domain: str,
    port: int,
    tls_enabled: bool = False,
    ssh_enabled: bool = True,
    ssh_port: int = 2222,
    ssh_listen_port: int = 2222,
) -> dict:
    """Build a Traefik route configuration for Forgejo.

    Forgejo always listens on *port* internally, running as a non-root user.
    Traefik handles the external 80/443 mapping.

    HTTP mode: standard HTTP router forwarding to Forgejo.
    TLS mode: TCP TLS-passthrough router so Forgejo terminates TLS.
    SSH mode: plain TCP router forwarding to Forgejo's SSH listener.
      The Traefik entrypoint listens on *ssh_port* (external, user-facing) and
      forwards to the backend on *ssh_listen_port* (internal container port).
      Added to the config whenever *ssh_enabled* is True.
    """
    prefix = f"{model_name}-{app_name}"
    k8s_svc = _k8s_service(app_name, model_name)

    if tls_enabled:
        tcp = _tls_tcp_section(f"{prefix}-router", f"{prefix}-service", domain, k8s_svc, port)
        if ssh_enabled:
            ssh = _ssh_tcp_section(
                f"{prefix}-ssh-router", f"{prefix}-ssh-service", k8s_svc, ssh_listen_port
            )
            tcp["routers"].update(ssh["routers"])
            tcp["services"].update(ssh["services"])
        return {"tcp": tcp}

    result = {
        "http": _http_section(f"{prefix}-router", f"{prefix}-service", domain, k8s_svc, port)
    }
    if ssh_enabled:
        result["tcp"] = _ssh_tcp_section(
            f"{prefix}-ssh-router", f"{prefix}-ssh-service", k8s_svc, ssh_listen_port
        )
    return result
