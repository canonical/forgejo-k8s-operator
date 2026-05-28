"""Traefik ingress route configuration for the Forgejo K8s charm."""


def get_traefik_route_config(
    model_name: str,
    app_name: str,
    domain: str,
    port: int,
    tls_enabled: bool = False,
) -> dict:
    """Build a Traefik route configuration for Forgejo.

    Forgejo always listens on *port* internally, running as a non-root user.
    Traefik handles the external 80/443 mapping.

    HTTP mode: standard HTTP router forwarding to Forgejo.
    TLS mode: TCP TLS-passthrough router so Forgejo terminates TLS.
    """
    router_name = f"{model_name}-{app_name}-router"
    service_name = f"{model_name}-{app_name}-service"
    k8s_service = f"{app_name}.{model_name}.svc.cluster.local"

    if tls_enabled:
        return {
            "tcp": {
                "routers": {
                    router_name: {
                        "rule": f"HostSNI(`{domain}`)",
                        "service": service_name,
                        "entryPoints": ["websecure"],
                        "tls": {"passthrough": True},
                    }
                },
                "services": {
                    service_name: {
                        "loadBalancer": {
                            "servers": [{"address": f"{k8s_service}:{port}"}],
                        }
                    }
                },
            }
        }

    return {
        "http": {
            "routers": {
                router_name: {
                    "rule": f"Host(`{domain}`)",
                    "service": service_name,
                    "entryPoints": ["web"],
                }
            },
            "services": {
                service_name: {
                    "loadBalancer": {"servers": [{"url": f"http://{k8s_service}:{port}"}]}
                },
            },
        }
    }
