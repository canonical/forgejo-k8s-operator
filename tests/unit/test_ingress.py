"""Unit tests for src/ingress.py."""

from ingress import get_ssh_static_config, get_traefik_route_config

MODEL = "test-model"
APP = "forgejo"
DOMAIN = "git.example.com"
PORT = 3000
K8S = f"{APP}.{MODEL}.svc.cluster.local"


# HTTP
def test_http_route_has_http_and_tcp_sections():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT)
    assert "http" in config
    assert "tcp" in config


def test_http_router_rule_and_entrypoint():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT)
    router = config["http"]["routers"][f"{MODEL}-{APP}-router"]
    assert router["rule"] == f"Host(`{DOMAIN}`)"
    assert router["entryPoints"] == ["web"]


def test_http_service_url():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT)
    service = config["http"]["services"][f"{MODEL}-{APP}-service"]
    assert service["loadBalancer"]["servers"][0]["url"] == f"http://{K8S}:{PORT}"


# TLS passthrough
def test_tls_route_has_only_tcp_section():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT, tls_enabled=True)
    assert "http" not in config
    assert "tcp" in config


def test_tls_router_passthrough():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT, tls_enabled=True)
    router = config["tcp"]["routers"][f"{MODEL}-{APP}-router"]
    assert router["rule"] == f"HostSNI(`{DOMAIN}`)"
    assert router["entryPoints"] == ["websecure"]
    assert router["tls"] == {"passthrough": True}


def test_tls_service_address():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT, tls_enabled=True)
    service = config["tcp"]["services"][f"{MODEL}-{APP}-service"]
    assert service["loadBalancer"]["servers"][0]["address"] == f"{K8S}:{PORT}"


# SSH router (present by default, HTTP mode)
def test_ssh_router_present_by_default():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT)
    assert f"{MODEL}-{APP}-ssh-router" in config["tcp"]["routers"]


def test_ssh_router_rule_and_entrypoint():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT)
    router = config["tcp"]["routers"][f"{MODEL}-{APP}-ssh-router"]
    assert router["rule"] == "HostSNI(`*`)"
    assert router["entryPoints"] == ["ssh"]


def test_ssh_service_address_default_port():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT)
    service = config["tcp"]["services"][f"{MODEL}-{APP}-ssh-service"]
    assert service["loadBalancer"]["servers"][0]["address"] == f"{K8S}:2222"


def test_ssh_service_address_custom_listen_port():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT, ssh_listen_port=3022)
    service = config["tcp"]["services"][f"{MODEL}-{APP}-ssh-service"]
    assert service["loadBalancer"]["servers"][0]["address"] == f"{K8S}:3022"


# SSH disabled
def test_ssh_disabled_no_tcp_section_in_http_mode():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT, ssh_enabled=False)
    assert "tcp" not in config


def test_ssh_disabled_no_ssh_router_in_tls_mode():
    config = get_traefik_route_config(
        MODEL, APP, DOMAIN, PORT, tls_enabled=True, ssh_enabled=False
    )
    assert f"{MODEL}-{APP}-ssh-router" not in config["tcp"]["routers"]
    assert f"{MODEL}-{APP}-ssh-service" not in config["tcp"]["services"]


def test_ssh_disabled_tls_still_has_tls_router():
    config = get_traefik_route_config(
        MODEL, APP, DOMAIN, PORT, tls_enabled=True, ssh_enabled=False
    )
    assert f"{MODEL}-{APP}-router" in config["tcp"]["routers"]


# TLS + SSH combined
def test_tls_and_ssh_both_in_tcp_section():
    config = get_traefik_route_config(MODEL, APP, DOMAIN, PORT, tls_enabled=True, ssh_enabled=True)
    routers = config["tcp"]["routers"]
    services = config["tcp"]["services"]
    assert f"{MODEL}-{APP}-router" in routers
    assert f"{MODEL}-{APP}-ssh-router" in routers
    assert f"{MODEL}-{APP}-service" in services
    assert f"{MODEL}-{APP}-ssh-service" in services


# get_ssh_static_config
def test_ssh_static_config_non_standard_port():
    config = get_ssh_static_config(3022)
    assert config["entryPoints"]["ssh"]["address"] == ":3022"
