#!/usr/bin/env python3
# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

"""Forgejo K8s Charm."""

import dataclasses
from io import StringIO
import logging
import ops
import re
from typing import Optional
from urllib.parse import urlparse

from charms.data_platform_libs.v0.data_interfaces import DatabaseCreatedEvent, DatabaseRequires
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v1.ingress_per_unit import (
    IngressPerUnitReadyForUnitEvent,
    IngressPerUnitRequirer,
    IngressPerUnitRevokedForUnitEvent,
)
from forgejo_handler import generate_config

logger = logging.getLogger(__name__)

SERVICE_NAME = "forgejo"  # Name of Pebble service that runs in the workload container.
FORGEJO_CLI = "/usr/local/bin/forgejo"
CUSTOM_FORGEJO_CONFIG = "/etc/forgejo.ini"
PORT = 3000

@dataclasses.dataclass(frozen=True, kw_only=True)
class ForgejoConfig:
    """Configuration for the Forgejo k8s charm."""

    log_level: str = "info"
    domain: str = "forgejo.internal"

    def __post_init__(self):
        """Configuration calidation."""
        if self.log_level not in ['trace', 'debug', 'info', 'warn', 'error', 'fatal']:
            raise ValueError('Invalid log level number, should be one of trace, debug, info, warn, error, or fatal')

class CharmForgejoCharm(ops.CharmBase):
    """Forgejo K8s Charm."""

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)
        self._port = PORT
        self.set_ports()

        # traefik ingress support
        self.ingress = IngressPerUnitRequirer(
            self,
            relation_name="ingress",
            port=self._port,
            strip_prefix=True,
            redirect_https=True,
            scheme=lambda: "http",
        )

        # observability endpoint support
        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name='metrics-endpoint',
            jobs=[{'static_configs': [{'targets': [f'*:{PORT}']}]}],
            refresh_event=self.on.config_changed,
        )
        self._logging = LogForwarder(self, relation_name='logging')
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name='grafana-dashboard'
        )

        framework.observe(self.on.forgejo_pebble_ready, self._on_pebble_ready)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)

        # database support
        self.database = DatabaseRequires(self, relation_name='database', database_name='forgejo')
        framework.observe(self.database.on.database_created, self._on_database_created)
        framework.observe(self.database.on.endpoints_changed, self._on_database_created)

        # ingress events
        self.framework.observe(self.ingress.on.ready_for_unit, self._on_ingress_ready)
        self.framework.observe(self.ingress.on.revoked_for_unit, self._on_ingress_revoked)

        self._name = "forgejo"
        self.container = self.unit.get_container(self._name)
        self.pebble_service_name = SERVICE_NAME


    def _on_collect_status(self, event: ops.CollectStatusEvent) -> None:
        try:
            self.load_config(ForgejoConfig)
        except ValueError as e:
            event.add_status(ops.BlockedStatus(str(e)))
        if not self.model.get_relation('database'):
            # We need the user to do 'juju integrate'.
            event.add_status(ops.BlockedStatus('Waiting for database relation'))
        elif not self.database.fetch_relation_data():
            # We need the Forgejo <-> Postgresql relation to finish integrating.
            event.add_status(ops.WaitingStatus('Waiting for database relation'))
        if not self.ingress.url:
            # We need the Forgejo <-> Traefik relation to finish integrating.
            event.add_status(ops.WaitingStatus('Waiting for traefik relation'))
        try:
            status = self.container.get_service(self.pebble_service_name)
        except (ops.pebble.APIError, ops.pebble.ConnectionError, ops.ModelError):
            event.add_status(ops.MaintenanceStatus('Waiting for Pebble in workload container'))
        else:
            if not status.is_running():
                event.add_status(ops.MaintenanceStatus('Waiting for the service to start up'))
        # If nothing is wrong, then the status is active.
        event.add_status(ops.ActiveStatus())

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

    def _on_pebble_ready(self, _: ops.PebbleReadyEvent) -> None:
        """Handle pebble-ready event."""
        self._update_layer_and_restart()

    def _on_config_changed(self, _: ops.ConfigChangedEvent) -> None:
        self._update_layer_and_restart()

    def _on_database_created(self, _: DatabaseCreatedEvent) -> None:
        """Event is fired when postgres database is created."""
        self._update_layer_and_restart()

    def _get_pebble_layer(self) -> ops.pebble.Layer:
        """A Pebble layer for the Forgejo service."""
        command = [FORGEJO_CLI, 'web', f'--config={CUSTOM_FORGEJO_CONFIG}'] 
        pebble_layer: ops.pebble.LayerDict = {
            'summary': 'Forgejo service',
            'description': 'pebble config layer for the Forgejo server',
            'services': {
                self.pebble_service_name: {
                    'override': 'replace',
                    'summary': 'Forgejo service',
                    'command': ' '.join(command),
                    'startup': 'enabled',
                    'user-id': 1000,
                    'group-id': 1000,
                    "working-dir": "/data",
                }
            },
        }
        return ops.pebble.Layer(pebble_layer)

    def _update_layer_and_restart(self) -> None:
        self.unit.status = ops.MaintenanceStatus("starting workload")
        try:
            config = self.load_config(ForgejoConfig)
        except ValueError as e:
            logger.error('Configuration error: %s', e)
            self.unit.status = ops.BlockedStatus(str(e))
            return

        try:
            db_data = self.fetch_postgres_relation_data()
            # url from traefik relation takes precendence over charm config
            traefik_domain = self.fetch_traefik_relation_data()
            if traefik_domain:
                use_port_in_domain = False
                final_domain = traefik_domain
            else:
                use_port_in_domain = True
                final_domain = config.domain
            # write the config file to the forgejo container's filesystem
            cfg = generate_config(
                domain=final_domain,
                log_level=config.log_level,
                database_info=db_data,
                use_port_in_domain=use_port_in_domain,
            )
            buf = StringIO()
            cfg.write(buf)
            self.container.push(
                CUSTOM_FORGEJO_CONFIG,
                buf.getvalue(),
                user_id=1000,
                user='git',
                group_id=1000
            )

            self.container.add_layer('forgejo', self._get_pebble_layer(), combine=True)
            logger.info("Added updated layer 'forgejo' to Pebble plan")

            # Tell Pebble to incorporate the changes, including restarting the
            # service if required.
            self.container.replan()
            logger.info(f"Replanned with '{self.pebble_service_name}' service")

            if version := self._forgejo_version:
                self.unit.set_workload_version(version)
            else:
                logger.debug("Cannot set workload version at this time: could not get Forgejo version.")
        except (ops.pebble.APIError, ops.pebble.ConnectionError) as e:
            logger.info('Unable to connect to Pebble: %s', e)

    def set_ports(self):
        """Open necessary (and close no longer needed) workload ports."""
        planned_ports = {
            ops.model.OpenedPort("tcp", self._port),
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
        a dictionary. If no data is retrieved, the unit is set to waiting status and
        the program exits with a zero status code.
        """
        relations = self.database.fetch_relation_data()
        logger.debug('Got following database data: %s', relations)
        for data in relations.values():
            if not data:
                continue
            logger.info('New database endpoint is %s', data['endpoints'])
            host, port = data['endpoints'].split(':')
            db_data = {
                "DB_TYPE": "postgres",
                'HOST': host,
                'PORT': port,
                'NAME': "forgejo", 
                'USER': data['username'],
                'PASSWD': data['password'],
                "SCHEMA": "",
                "SSL_MODE": "disable",
                "LOG_SQL": "false",
            }
            return db_data
        return {}

    def _on_ingress_ready(self, event: IngressPerUnitReadyForUnitEvent):
        logger.info("Ingress for unit ready on '%s'", event.url)
        self._update_layer_and_restart()

    def _on_ingress_revoked(self, event: IngressPerUnitRevokedForUnitEvent):
        logger.info("Ingress for unit revoked.")
        self._update_layer_and_restart()

    def fetch_traefik_relation_data(self) -> str | None:
        """Fetch traefik relation data.

        We need to get the url that traefik will use and set Forgejo's ROOT url to that, this will override and ignore
        the domain set in the charm config.
        """
        domain = None
        traefik_url = self.ingress.url
        logger.debug('Got following url from ingress data: %s', traefik_url)
        try:
            domain = urlparse(traefik_url).netloc
        except Exception as e:
            logger.error('%s, could not parse domain from url %s', e, traefik_url)
        return domain


if __name__ == "__main__":  # pragma: nocover
    ops.main(CharmForgejoCharm)
