#!/usr/bin/env python3
# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

"""Forgejo K8s Charm."""

import logging
import re
import shlex
from typing import Optional, cast

import ops
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.data_platform_libs.v0.s3 import S3Requirer
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer

from certificates import CertHandler
from config import ForgejoConfig, map_config_to_env_vars

logger = logging.getLogger(__name__)

SERVICE_NAME = "forgejo"  # Name of Pebble service that runs in the workload container.
FORGEJO_CLI = "/usr/local/bin/forgejo"
ENVIRONMENT_TO_INI = "/usr/local/bin/environment-to-ini"
CUSTOM_FORGEJO_CONFIG_DIR = "/etc/forgejo/"
CUSTOM_FORGEJO_CONFIG_FILE = CUSTOM_FORGEJO_CONFIG_DIR + "config.ini"
PORT = 3000  # Forgejo's internal listen port (non-privileged, runs as git user uid 1000)
FORGEJO_DATA_DIR = "/data"
FORGEJO_SYSTEM_USER_ID = 1000
FORGEJO_SYSTEM_USER = "git"
FORGEJO_SYSTEM_GROUP_ID = 1000
FORGEJO_SYSTEM_GROUP = "git"


class ForgejoK8SOperatorCharm(ops.CharmBase):
    """Forgejo K8s Charm."""

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)
        self._name = "forgejo"
        self.container = self.unit.get_container(self._name)
        self.pebble_service_name = SERVICE_NAME

        # traefik route requirer handles None relation gracefully
        self.ingress = TraefikRouteRequirer(
            self,
            self.model.get_relation("ingress"),  # type: ignore[arg-type]
            "ingress",
            raw=True,
        )

        # observability endpoint support
        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[{"static_configs": [{"targets": [f"*:{PORT}"]}]}],
            refresh_event=self.on.config_changed,
        )
        self._logging = LogForwarder(self, relation_name="logging")
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.forgejo_pebble_ready, self.reconcile)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)
        framework.observe(getattr(self.on, "data_storage_attached"), self._on_storage_attached)

        # actions
        framework.observe(self.on.generate_runner_secret_action, self._on_generate_runner_secret)
        framework.observe(self.on.create_admin_user_action, self._on_create_admin_user)
        framework.observe(self.on.generate_user_token_action, self._on_generate_user_token)
        framework.observe(self.on.reset_user_password_action, self._on_reset_user_password)

        # TLS certificates support
        self.cert_handler = CertHandler(
            self,
            common_name=str(self.model.config.get("forgejo__server__domain") or self.app.name),
            events=[self.on.config_changed, self.on.forgejo_pebble_ready],
        )
        framework.observe(
            self.cert_handler.certificates.on.certificate_available,
            self._on_certificates_available,
        )
        framework.observe(
            self.on["certificates"].relation_changed, self._on_certificates_available
        )
        framework.observe(self.on["certificates"].relation_departed, self._on_certificates_removed)
        framework.observe(self.on["certificates"].relation_broken, self._on_certificates_removed)

        # database support
        self.database = DatabaseRequires(
            self,
            relation_name="database",
            database_name=self.database_name,
        )
        framework.observe(self.database.on.database_created, self.reconcile)
        framework.observe(self.database.on.endpoints_changed, self.reconcile)

        # S3 storage support
        self.s3_client = S3Requirer(self, relation_name="s3-credentials", bucket_name="forgejo")
        framework.observe(self.s3_client.on.credentials_changed, self.reconcile)
        framework.observe(self.s3_client.on.credentials_gone, self.reconcile)

        self.set_ports()

    @property
    def database_name(self):
        """Return the database name scoped to this model and app."""
        return f"{self.model.name}-{self.app.name}"

    def _on_collect_status(self, event: ops.CollectStatusEvent) -> None:
        """Collect and report the unit status."""
        config = self._collect_config_status(event)
        self._collect_database_status(event)
        self._collect_ingress_status(event)
        self._collect_tls_status(event)
        self._collect_service_status(event)
        # If nothing is wrong, report active.
        if config:
            scheme = "https" if self._tls_enabled else "http"
            event.add_status(
                ops.ActiveStatus(f"Serving at {scheme}://{config.forgejo__server__domain}")
            )
        else:
            event.add_status(ops.ActiveStatus())

    def _collect_config_status(self, event: ops.CollectStatusEvent) -> Optional["ForgejoConfig"]:
        """Check charm config validity; return config if valid, None otherwise."""
        config = None
        try:
            config = self.load_config(ForgejoConfig)
        except ValueError as e:
            event.add_status(ops.BlockedStatus(str(e)))
        if config and not config.forgejo__server__domain:
            event.add_status(ops.BlockedStatus("forgejo__server__domain config needs to be set"))
        return config

    def _collect_database_status(self, event: ops.CollectStatusEvent) -> None:
        """Check database relation status."""
        if not self.model.get_relation("database"):
            # We need the user to do 'juju integrate'.
            event.add_status(ops.BlockedStatus("Add a database relation"))
        elif not self.database.fetch_relation_data():
            # We need the Forgejo <-> Postgresql relation to finish integrating.
            event.add_status(ops.WaitingStatus("Waiting for database relation"))

    def _collect_ingress_status(self, event: ops.CollectStatusEvent) -> None:
        """Check ingress relation status."""
        if self.model.get_relation("ingress") and not self.ingress.is_ready():
            # We need the Forgejo <-> Ingress relation to finish integrating.
            event.add_status(ops.WaitingStatus("Waiting for ingress relation"))

    def _collect_tls_status(self, event: ops.CollectStatusEvent) -> None:
        """Check TLS certificate status."""
        if self.model.get_relation("certificates") and not self._tls_enabled:
            event.add_status(ops.WaitingStatus("Waiting for TLS certificate"))

    def _collect_service_status(self, event: ops.CollectStatusEvent) -> None:
        """Check Pebble service status."""
        try:
            status = self.container.get_service(self.pebble_service_name)
        except (ops.pebble.APIError, ops.pebble.ConnectionError, ops.ModelError):
            event.add_status(ops.MaintenanceStatus("Waiting for Pebble in workload container"))
        else:
            if not status.is_running():
                event.add_status(ops.MaintenanceStatus("Waiting for Forgejo to start up"))

    @property
    def _forgejo_version(self) -> Optional[str]:
        """Returns the version of Forgejo.

        Returns:
            A string equal to the Forgejo version.
        """
        if not self.container.can_connect():
            return None
        version_output, _ = self.container.exec([FORGEJO_CLI, "--version"]).wait_output()
        # Output looks like this:
        # Forgejo version 11.0.3+gitea-1.22.0 (release name 11.0.3) built with ...
        result = re.search(r"version (\d*\.\d*\.\d*)", version_output)
        if result is None:
            return result
        return result.group(1)

    def _get_pebble_layer(self, env_vars: dict) -> ops.pebble.Layer:
        """Return the Pebble layer definition for the Forgejo service."""
        pebble_layer: ops.pebble.LayerDict = {
            "summary": "Forgejo service",
            "description": "pebble config layer for the Forgejo server",
            "services": {
                self.pebble_service_name: {
                    "override": "replace",
                    "summary": "Forgejo service",
                    "command": (
                        f"/bin/sh -c '{ENVIRONMENT_TO_INI} -c {CUSTOM_FORGEJO_CONFIG_FILE}"
                        f" && {FORGEJO_CLI} web --config={CUSTOM_FORGEJO_CONFIG_FILE}'"
                    ),
                    "startup": "enabled",
                    "user-id": FORGEJO_SYSTEM_USER_ID,
                    "group-id": FORGEJO_SYSTEM_GROUP_ID,
                    "working-dir": FORGEJO_DATA_DIR,
                    "environment": env_vars,
                }
            },
        }
        return ops.pebble.Layer(pebble_layer)

    def _build_additional_env(
        self,
        domain: str,
        protocol: str,
        tls_ready: bool,
        db_data: dict,
    ) -> dict:
        """Build the additional env vars dict for environment-to-ini.

        Returns FORGEJO__SECTION__KEY env vars that cannot come from Juju config:
        - Computed server values only injected when the user has not set them via Juju config.
        - Relation configuration.
        """
        env: dict = {
            # Top-level (DEFAULT section)
            "FORGEJO____RUN_USER": "git",
            # Repository root
            "FORGEJO__REPOSITORY__ROOT": "/data/gitea/data/forgejo-repositories",
            **db_data,
        }
        # SSH_DOMAIN and ROOT_URL are computed from protocol+domain unless the user
        # has explicitly set them via Juju config (empty string = use computed value).
        if not self.config.get("forgejo__server__root_url", ""):
            env["FORGEJO__SERVER__ROOT_URL"] = f"{protocol}://{domain}/"
        if tls_ready:
            env["FORGEJO__SERVER__PROTOCOL"] = "https"
            env["FORGEJO__SERVER__CERT_FILE"] = self.cert_handler.cert_path
            env["FORGEJO__SERVER__KEY_FILE"] = self.cert_handler.key_path
        return env

    def _on_config_changed(self, e: ops.ConfigChangedEvent):
        self.reconcile(e)

    def reconcile(self, _: ops.EventBase) -> None:
        """Reconcile charm state: build env vars, update Pebble layer, and replan."""
        if not self.container.can_connect():
            logger.warning("Pebble not ready yet, deferring reconcile")
            return

        logger.info("Reconciling workload state")
        # Ensure the base config file exists (fallback if install was deferred).
        self._init_config_file()
        try:
            config = self.load_config(ForgejoConfig)
        except ValueError as e:
            logger.error("Configuration error: %s", e)
            return

        try:
            db_data = self.fetch_postgres_relation_data()

            # Configure TLS if certificates relation is available.
            # Forgejo always listens on PORT (3000) regardless of TLS state;
            # the protocol is controlled via the PROTOCOL/CERT_FILE/KEY_FILE config.
            tls_ready = self.cert_handler.configure_certs()

            domain = config.forgejo__server__domain
            protocol = "https" if tls_ready else "http"

            if self.ingress.is_ready() and self.unit.is_leader():
                if domain:
                    logger.info(f"Config domain {domain} is valid, submitting traefik route")
                    self.ingress.submit_to_traefik(
                        self.get_traefik_route_configuration(domain, tls_ready)
                    )
                else:
                    logger.error("No domain set in charm")

            # Build env vars: computed values and relational data (DB, TLS).
            additional_env = self._build_additional_env(domain, protocol, tls_ready, db_data)

            env_vars = map_config_to_env_vars(self, **additional_env)

            self.container.add_layer("forgejo", self._get_pebble_layer(env_vars), combine=True)
            logger.info("Added updated layer 'forgejo' to Pebble plan")

            # Tell Pebble to incorporate the changes, including restarting the
            # service if required. If the env vars haven't changed, replan is a no-op.
            self.container.replan()
            logger.info(f"Replanned with '{self.pebble_service_name}' service")

            if version := self._forgejo_version:
                self.unit.set_workload_version(version)
            else:
                logger.debug(
                    "Cannot set workload version at this time: could not get Forgejo version."
                )
        # @TODO: Extend exception handling
        except (ops.pebble.APIError, ops.pebble.ConnectionError) as e:
            logger.info("Unable to connect to Pebble: %s", e)

    @property
    def _tls_enabled(self) -> bool:
        """Return True if TLS certificates are provisioned in the relation data."""
        if not self.model.get_relation("certificates"):
            return False
        return self.cert_handler._certificate_is_available()

    def set_ports(self):
        """Open necessary (and close no longer needed) workload ports."""
        planned_ports = {
            ops.model.OpenedPort("tcp", PORT),
        }
        actual_ports = self.unit.opened_ports()

        # Ports may change across an upgrade, so need to sync
        ports_to_close = actual_ports.difference(planned_ports)
        for p in ports_to_close:
            self.unit.close_port(p.protocol, p.port)

        new_ports_to_open = planned_ports.difference(actual_ports)
        for p in new_ports_to_open:
            self.unit.open_port(p.protocol, p.port)

    def fetch_postgres_relation_data(self) -> dict[str, str]:
        """Fetch postgres relation data.

        This function retrieves relation data from a postgres database using
        the `fetch_relation_data` method of the `database` object. The retrieved data is
        then logged for debugging purposes, and any non-empty data is processed to extract
        endpoint information, username, and password. This processed data is then returned as
        a dictionary of FORGEJO__DATABASE__* env vars for environment-to-ini.
        If no data is retrieved, the unit is set to waiting status and
        the program exits with a zero status code.
        """
        relations = self.database.fetch_relation_data()
        logger.debug("Got following database data: %s", relations)
        for data in relations.values():
            if not data:
                continue
            logger.info("New database endpoint is %s", data["endpoints"])
            host, port = data["endpoints"].split(":")
            db_data = {
                "FORGEJO__DATABASE__DB_TYPE": "postgres",
                "FORGEJO__DATABASE__HOST": host,
                "FORGEJO__DATABASE__PORT": port,
                "FORGEJO__DATABASE__NAME": self.database_name,
                "FORGEJO__DATABASE__USER": data["username"],
                "FORGEJO__DATABASE__PASSWD": data["password"],
                "FORGEJO__DATABASE__SCHEMA": "",
                "FORGEJO__DATABASE__SSL_MODE": "disable",
                "FORGEJO__DATABASE__LOG_SQL": "false",
            }
            return db_data
        return {}

    def _fetch_s3_relation_data(self) -> dict[str, str]:
        """Fetch S3 connection info from the s3-credentials relation."""
        if not self.model.get_relation("s3-credentials"):
            return {}
        s3_info = self.s3_client.get_s3_connection_info()
        if not s3_info:
            return {}
        return s3_info

    @property
    def traefik_service_name(self):
        """Return the Traefik service name scoped to this model and app."""
        return f"{self.model.name}-{self.model.app.name}-service"

    def get_traefik_route_configuration(self, domain: str, tls_enabled: bool = False) -> dict:
        """Configure a route from traefik to forgejo.

        Forgejo always listens on PORT (3000) internally, running as a non-root user.
        Traefik handles the external 80/443 mapping.

        HTTP mode: standard HTTP router forwarding to Forgejo on port 3000.
        TLS mode: TCP TLS-passthrough router so Forgejo terminates TLS on port 3000.
        """
        router_name = f"{self.model.name}-{self.model.app.name}-router"
        # Use the stable Kubernetes service DNS so the address survives pod restarts.
        k8s_service = f"{self.app.name}.{self.model.name}.svc.cluster.local"

        if tls_enabled:
            # TCP passthrough: Traefik forwards raw TLS bytes; Forgejo terminates TLS.
            # HostSNI matches on the TLS SNI field, which requires passthrough mode.
            return {
                "tcp": {
                    "routers": {
                        router_name: {
                            "rule": f"HostSNI(`{domain}`)",
                            "service": self.traefik_service_name,
                            "entryPoints": ["websecure"],
                            "tls": {"passthrough": True},
                        }
                    },
                    "services": {
                        self.traefik_service_name: {
                            "loadBalancer": {
                                "servers": [{"address": f"{k8s_service}:{PORT}"}],
                            }
                        }
                    },
                }
            }

        # HTTP mode
        return {
            "http": {
                "routers": {
                    router_name: {
                        "rule": f"Host(`{domain}`)",
                        "service": self.traefik_service_name,
                        "entryPoints": ["web"],
                    }
                },
                "services": {
                    self.traefik_service_name: {
                        "loadBalancer": {"servers": [{"url": f"http://{k8s_service}:{PORT}"}]}
                    }
                },
            }
        }

    def _on_certificates_available(self, event: ops.EventBase) -> None:
        """Handle new/updated TLS certificate - switch Forgejo to HTTPS."""
        logger.info("TLS certificate available, switching to HTTPS")
        self.reconcile(event)

    def _on_certificates_removed(self, event: ops.EventBase) -> None:
        """Handle TLS certificate removal - switch Forgejo back to HTTP."""
        logger.info("TLS certificates removed, switching back to HTTP")
        if self.container.can_connect():
            self.cert_handler.remove_certs()
        self.reconcile(event)

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Create the empty base config file that environment-to-ini will overlay."""
        if not self.container.can_connect():
            logger.info(
                "Container not ready at install time; config file will be created at pebble-ready"
            )
            event.defer()
            return
        self._init_config_file()

    def _init_config_file(self) -> None:
        """Ensure the Forgejo config directory and an empty base config file exist."""
        if not self.container.exists(CUSTOM_FORGEJO_CONFIG_FILE):
            self.container.push(
                CUSTOM_FORGEJO_CONFIG_FILE,
                "",
                make_dirs=True,
                user_id=FORGEJO_SYSTEM_USER_ID,
                user=FORGEJO_SYSTEM_USER,
                group_id=FORGEJO_SYSTEM_GROUP_ID,
                group=FORGEJO_SYSTEM_GROUP,
            )
            logger.info("Created empty base config file at %s", CUSTOM_FORGEJO_CONFIG_FILE)

    def _on_storage_attached(self, _: ops.StorageAttachedEvent) -> None:
        owner = f"{FORGEJO_SYSTEM_USER}:{FORGEJO_SYSTEM_GROUP}"
        self.container.exec(["chown", owner, FORGEJO_DATA_DIR])

    def _on_generate_runner_secret(self, event: ops.ActionEvent) -> None:
        """Generate a new runner secret and return it as action output."""
        # SECRET=$(forgejo forgejo-cli actions generate-secret)
        # forgejo forgejo-cli actions register --secret $SECRET --labels "docker"
        params = event.params
        name = params.get("name", "runner")
        labels = params.get("labels", "docker")
        scope = params.get("scope", None)
        add_scope = ""
        if scope:
            add_scope = f"--scope {shlex.quote(scope)}"
        # generate the secret
        cmd = f"{FORGEJO_CLI} forgejo-cli actions generate-secret"
        cmd_parts: list[str] = cast(list[str], cmd.split())
        secret, _ = self.container.exec(cmd_parts).wait_output()
        # register the runner with the generated secret
        register_cmd = (
            f"{shlex.quote(FORGEJO_CLI)} --config=/etc/forgejo/config.ini"
            f" forgejo-cli actions register "
            f"--secret {shlex.quote(secret)} "
            f"--labels {shlex.quote(labels)} "
            f"--name {shlex.quote(name)} "
            f"{add_scope}".strip()
        )
        argv = ["su", "git", "-c", register_cmd]
        self.container.exec(argv).wait_output()
        # send the secret back as action output
        event.set_results({"runner-secret": secret})

    def _on_create_admin_user(self, event: ops.ActionEvent) -> None:
        """Create an admin user in Forgejo."""
        params = event.params
        username = params.get("username")
        email = params.get("email")
        if not username or not email:
            event.fail("username, password, and email parameters are required")
            return
        cmd = (
            f"{shlex.quote(FORGEJO_CLI)} --config=/etc/forgejo/config.ini admin user create "
            f"--username {shlex.quote(username)} "
            f"--email {shlex.quote(email)} "
            f"--admin "
            f"--random-password"
        )
        argv = ["su", "git", "-c", cmd]
        output, _ = self.container.exec(argv).wait_output()
        event.set_results({"output": output})

    def _on_generate_user_token(self, event: ops.ActionEvent) -> None:
        """Generate an API access token for the specified Forgejo user."""
        params = event.params
        username = params.get("username")
        token_name = params.get("token-name", "charm-token")
        scopes = params.get("scopes", "all")
        if not username:
            event.fail("username parameter is required")
            return
        cmd = (
            f"{shlex.quote(FORGEJO_CLI)} --config=/etc/forgejo/config.ini"
            f" admin user generate-access-token "
            f"--username {shlex.quote(username)} "
            f"--token-name {shlex.quote(token_name)} "
            f"--scopes {shlex.quote(scopes)} "
            f"--raw"
        )
        argv = ["su", "git", "-c", cmd]
        try:
            output, _ = self.container.exec(argv).wait_output()
        except ops.pebble.ExecError as e:
            event.fail(f"Failed to generate token: {e.stderr}")
            return
        event.set_results({"token": output.strip()})

    def _on_reset_user_password(self, event: ops.ActionEvent) -> None:
        """Reset a Forgejo user's password to a new random value."""
        username = event.params.get("username")
        password = event.params.get("password")
        if not username:
            event.fail("username parameter is required")
            return
        if not password:
            event.fail("password parameter is required")
            return
        cmd = (
            f"{shlex.quote(FORGEJO_CLI)} --config=/etc/forgejo/config.ini"
            f" admin user change-password "
            f"--username {shlex.quote(username)} "
            f"--password {shlex.quote(password)}"
        )
        argv = ["su", "git", "-c", cmd]
        try:
            output, _ = self.container.exec(argv).wait_output()
        except ops.pebble.ExecError as e:
            event.fail(f"Failed to reset password: {e.stderr}")
            return
        event.set_results({"output": output.strip()})


if __name__ == "__main__":  # pragma: nocover
    ops.main(ForgejoK8SOperatorCharm)
