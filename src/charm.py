#!/usr/bin/env python3

"""Forgejo K8s Charm."""

import logging
import re
from typing import Optional

import ops
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.data_platform_libs.v0.s3 import S3Requirer
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer

from actions import (
    on_create_admin_user,
    on_generate_runner_secret,
    on_generate_user_token,
    on_reset_user_password,
)
from certificates import CertHandler
from config import (
    ForgejoConfig,
    ForgejoStorageConfig,
    TraefikSSHConfig,
    map_config_to_env_vars,
)
from constants import (
    CUSTOM_FORGEJO_CONFIG_FILE,
    ENVIRONMENT_TO_INI,
    FORGEJO_CLI,
    FORGEJO_DATA_DIR,
    FORGEJO_SYSTEM_GROUP,
    FORGEJO_SYSTEM_GROUP_ID,
    FORGEJO_SYSTEM_USER,
    FORGEJO_SYSTEM_USER_ID,
    PORT,
    SERVICE_NAME,
)
from ingress import get_ssh_static_config, get_traefik_route_config

logger = logging.getLogger(__name__)


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
            refresh_event=self.on.config_changed,
        )
        self._logging = LogForwarder(self, relation_name="logging")
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.forgejo_pebble_ready, self.reconcile)
        framework.observe(self.on.forgejo_pebble_check_failed, self._on_pebble_check_changed)
        framework.observe(self.on.forgejo_pebble_check_recovered, self._on_pebble_check_changed)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)
        framework.observe(getattr(self.on, "data_storage_attached"), self._on_storage_attached)
        framework.observe(self.on.secret_changed, self._on_secret_changed)

        # actions - actions.py handlers
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

        # ingress support
        framework.observe(self.on["ingress"].relation_changed, self.reconcile)
        framework.observe(self.on["ingress"].relation_departed, self.reconcile)
        framework.observe(self.on["ingress"].relation_broken, self.reconcile)

        # S3 storage support
        self.s3_client = S3Requirer(self, relation_name="s3-credentials", bucket_name="forgejo")
        framework.observe(self.s3_client.on.credentials_changed, self.reconcile)
        framework.observe(self.s3_client.on.credentials_gone, self.reconcile)

        self.set_ports()

    @property
    def database_name(self):
        """Return the database name scoped to this model and app."""
        return f"{self.model.name}-{self.app.name}"

    def _on_pebble_check_changed(self, _: ops.EventBase) -> None:
        """No-op: ops emits collect_unit_status automatically after every event."""

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
                return
            checks = self.container.get_checks("forgejo-ready")
            if checks and checks["forgejo-ready"].status != ops.pebble.CheckStatus.UP:
                event.add_status(ops.MaintenanceStatus("Waiting for Forgejo to be ready"))

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
            "checks": {
                "forgejo-ready": {
                    "override": "replace",
                    "level": "ready",
                    "http": {"url": f"http://localhost:{PORT}/api/healthz"},
                }
            },
        }
        return ops.pebble.Layer(pebble_layer)

    def _build_additional_env(
        self,
        domain: str,
        protocol: str,
        tls_ready: bool,
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
            **self._fetch_postgres_relation_data(),
            **self._fetch_s3_relation_data(),
        }
        # SSH_DOMAIN and ROOT_URL are computed from protocol+domain unless the user
        # has explicitly set them via Juju config (empty string = use computed value).
        if not self.config.get("forgejo__server__root_url", ""):
            env["FORGEJO__SERVER__ROOT_URL"] = f"{protocol}://{domain}/"

        ingress_cfg = TraefikSSHConfig.from_charm_config(self.config)
        if ingress_cfg.ssh_enabled:
            env["FORGEJO__SERVER__START_SSH_SERVER"] = "true"
        if tls_ready:
            env["FORGEJO__SERVER__PROTOCOL"] = "https"
            env["FORGEJO__SERVER__CERT_FILE"] = self.cert_handler.cert_path
            env["FORGEJO__SERVER__KEY_FILE"] = self.cert_handler.key_path
        return env

    def _on_config_changed(self, e: ops.ConfigChangedEvent):
        self.reconcile(e)

    def _on_secret_changed(self, e: ops.SecretChangedEvent) -> None:
        self.reconcile(e)

    def _configure_prometheus(self, env_vars: dict):
        job: dict = {"static_configs": [{"targets": [f"*:{PORT}"]}]}
        if token := env_vars.get("FORGEJO__METRICS__TOKEN"):
            job["authorization"] = {"credentials": token}

        self._prometheus_scraping.update_scrape_job_spec([job])

    def reconcile(self, _: ops.EventBase) -> None:
        """Reconcile charm state: build env vars, update Pebble layer, and replan."""
        if not self.container.can_connect():
            logger.warning("Pebble not ready yet, deferring reconcile")
            return

        logger.info("Reconciling workload state")
        self._init_config_file()
        try:
            config = self.load_config(ForgejoConfig)
        except ValueError as e:
            logger.error("Configuration error: %s", e)
            return

        try:
            tls_ready = self.cert_handler.configure_certs()
            domain = config.forgejo__server__domain
            protocol = "https" if tls_ready else "http"

            self.set_ports()
            self._configure_ingress(domain, tls_ready)

            additional_env = self._build_additional_env(domain, protocol, tls_ready)
            env_vars = map_config_to_env_vars(self, **additional_env)
            self._configure_ingress(domain, tls_ready)
            self._configure_prometheus(env_vars)

            self._apply_pebble_layer(env_vars)

        # @TODO: Extend exception handling
        except (ops.pebble.APIError, ops.pebble.ConnectionError) as e:
            logger.info("Unable to connect to Pebble: %s", e)

    def _configure_ingress(self, domain: str, tls_ready: bool) -> None:
        """Submit the Traefik route configuration if ingress is ready."""
        if not (self.ingress.is_ready() and self.unit.is_leader()):
            return
        if not domain:
            logger.error("No domain set in charm")
            return
        ingress_cfg = TraefikSSHConfig.from_charm_config(self.config)
        logger.info(f"Config domain {domain} is valid, submitting traefik route")
        self.ingress.submit_to_traefik(
            get_traefik_route_config(
                self.model.name,
                self.app.name,
                domain,
                PORT,
                tls_ready,
                ingress_cfg.ssh_enabled,
                ingress_cfg.ssh_port,
                ingress_cfg.ssh_listen_port,
            ),
            static=get_ssh_static_config(ingress_cfg.ssh_port)
            if ingress_cfg.ssh_enabled
            else None,
        )

    def _apply_pebble_layer(self, env_vars: dict) -> None:
        """Update the Pebble layer and replan the service."""
        self.container.add_layer("forgejo", self._get_pebble_layer(env_vars), combine=True)
        logger.info("Added updated layer 'forgejo' to Pebble plan")

        self.container.replan()
        logger.info(f"Replanned with '{self.pebble_service_name}' service")

        if version := self._forgejo_version:
            self.unit.set_workload_version(version)
        else:
            logger.debug(
                "Cannot set workload version at this time: could not get Forgejo version."
            )

    @property
    def _tls_enabled(self) -> bool:
        """Return True if TLS certificates are provisioned in the relation data."""
        if not self.model.get_relation("certificates"):
            return False
        return self.cert_handler._certificate_is_available()

    def set_ports(self):
        """Open necessary (and close no longer needed) workload ports."""
        ingress_cfg = TraefikSSHConfig.from_charm_config(self.config)
        planned_ports = {ops.model.OpenedPort("tcp", PORT)}
        if ingress_cfg.ssh_enabled:
            planned_ports.add(ops.model.OpenedPort("tcp", ingress_cfg.ssh_listen_port))
        actual_ports = self.unit.opened_ports()

        # Ports may change across an upgrade, so need to sync
        ports_to_close = actual_ports.difference(planned_ports)
        for p in ports_to_close:
            self.unit.close_port(p.protocol, p.port)

        new_ports_to_open = planned_ports.difference(actual_ports)
        for p in new_ports_to_open:
            self.unit.open_port(p.protocol, p.port)

    def _fetch_postgres_relation_data(self) -> dict[str, str]:
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
            db_data = {
                "FORGEJO__DATABASE__DB_TYPE": "postgres",
                "FORGEJO__DATABASE__HOST": data["endpoints"],
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
        return ForgejoStorageConfig.from_s3_info(s3_info).model_dump(by_alias=True)

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

    # action wrappers

    def _on_generate_runner_secret(self, event: ops.ActionEvent) -> None:
        on_generate_runner_secret(event, self.container)

    def _on_create_admin_user(self, event: ops.ActionEvent) -> None:
        on_create_admin_user(event, self.container)

    def _on_generate_user_token(self, event: ops.ActionEvent) -> None:
        on_generate_user_token(event, self.container)

    def _on_reset_user_password(self, event: ops.ActionEvent) -> None:
        on_reset_user_password(event, self.container)


if __name__ == "__main__":  # pragma: nocover
    ops.main(ForgejoK8SOperatorCharm)
