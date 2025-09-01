#!/usr/bin/env python3
# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

"""Forgejo K8s Charm."""

import dataclasses
from io import StringIO
import logging
import ops
import re
import socket
from typing import Optional
from urllib.parse import urlparse

from charms.data_platform_libs.v0.data_interfaces import DatabaseCreatedEvent, DatabaseRequires
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer
from charms.traefik_k8s.v2.ingress import (
    IngressPerAppReadyEvent,
    IngressPerAppRequirer,
    IngressPerAppRevokedEvent,
)
from forgejo_handler import generate_config

logger = logging.getLogger(__name__)

SERVICE_NAME = "forgejo"  # Name of Pebble service that runs in the workload container.
FORGEJO_CLI = "/usr/local/bin/forgejo"
CUSTOM_FORGEJO_CONFIG = "/etc/forgejo.ini"
PORT = 3000
FORGEJO_DATA_DIR = "/data"
FORGEJO_SYSTEM_USER_ID = 1000
FORGEJO_SYSTEM_USER = "git"
FORGEJO_SYSTEM_GROUP_ID = 1000
FORGEJO_SYSTEM_GROUP = "git"


@dataclasses.dataclass(frozen=True, kw_only=True)
class ForgejoConfig:
    """Configuration for the Forgejo k8s charm."""

    log_level: str = "info"
    domain: str = ""

    def __post_init__(self):
        """Configuration validation."""
        if self.log_level not in ['trace', 'debug', 'info', 'warn', 'error', 'fatal']:
            raise ValueError('Invalid log level number, should be one of trace, debug, info, warn, error, or fatal')

class ForgejoK8SOperatorCharm(ops.CharmBase):
    """Forgejo K8s Charm."""

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)
        self._port = PORT
        self.set_ports()

        # ingress support
        # self.ingress = IngressPerAppRequirer(
        #     self,
        #     relation_name="ingress",
        #     port=PORT,
        #     strip_prefix=True,
        #     redirect_https=True,
        #     host=self.app.name,
        # )

        self.traefik_route = TraefikRouteRequirer(
            charm, self.model.get_relation("ingress"), "ingress", raw=True
        )
        # we may submit a route later on to traefik if the domain charm config is set 
        self.traefik_route.submit_to_traefik(
            self.get_traefik_route_configuration(self.app.name)
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

        framework.observe(self.on.forgejo_pebble_ready, self.reconcile)
        framework.observe(self.on.config_changed, self.reconcile)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)
        framework.observe(getattr(self.on, "data_storage_attached"), self._on_storage_attached)

        # database support
        self.database = DatabaseRequires(
            self,
            relation_name='database',
            database_name=self.database_name,
        )
        framework.observe(self.database.on.database_created, self.reconcile)
        framework.observe(self.database.on.endpoints_changed, self.reconcile)

        # ingress events
        self.framework.observe(self.ingress.on.ready, self.reconcile)
        self.framework.observe(self.ingress.on.revoked, self.reconcile)

        self._name = "forgejo"
        self.container = self.unit.get_container(self._name)
        self.pebble_service_name = SERVICE_NAME


    @property
    def database_name(self):
        return f"{self.model.name}-{self.app.name}"


    @property
    def hostname(self) -> str:
        return socket.getfqdn()


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
            # We need the Forgejo <-> Ingress relation to finish integrating.
            event.add_status(ops.WaitingStatus('Waiting for ingress relation'))
        try:
            status = self.container.get_service(self.pebble_service_name)
        except (ops.pebble.APIError, ops.pebble.ConnectionError, ops.ModelError):
            event.add_status(ops.MaintenanceStatus('Waiting for Pebble in workload container'))
        else:
            if not status.is_running():
                event.add_status(ops.MaintenanceStatus('Waiting for Forgejo to start up'))
        # If nothing is wrong, then the status is active.
        event.add_status(ops.ActiveStatus(self.serving_message))


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


    def _get_pebble_layer(self) -> ops.pebble.Layer:
        """A Pebble layer for the Forgejo service."""
        pebble_layer: ops.pebble.LayerDict = {
            'summary': 'Forgejo service',
            'description': 'pebble config layer for the Forgejo server',
            'services': {
                self.pebble_service_name: {
                    'override': 'replace',
                    'summary': 'Forgejo service',
                    'command': f"{FORGEJO_CLI} web --config={CUSTOM_FORGEJO_CONFIG}",
                    'startup': 'enabled',
                    'user-id': FORGEJO_SYSTEM_USER_ID,
                    'group-id': FORGEJO_SYSTEM_GROUP_ID,
                    "working-dir": FORGEJO_DATA_DIR,
                }
            },
        }
        return ops.pebble.Layer(pebble_layer)


    def reconcile(self, _: ops.HookEvent) -> None:
        self.unit.status = ops.MaintenanceStatus("starting workload")
        try:
            config = self.load_config(ForgejoConfig)
        except ValueError as e:
            logger.error('Configuration error: %s', e)
            self.unit.status = ops.BlockedStatus(str(e))
            return

        try:
            db_data = self.fetch_postgres_relation_data()
            ingress_url_domain = self.fetch_ingress_relation_data()

            if config.domain and config.domain != ingress_url_domain:
                logger.info(
                    f"Config domain {config.domain} is valid and different from ingress {ingress_url_domain}, submitting traefik route"
                )
                self.traefik_route.submit_to_traefik(
                    self.get_traefik_route_configuration(config.domain)
                )
                
            # write the config file to the forgejo container's filesystem
            cfg = generate_config(
                domain=config.domain if config.domain else self.hostname,
                log_level=config.log_level,
                database_info=db_data,
                use_port_in_domain=False,
            )
            buf = StringIO()
            cfg.write(buf)
            self.container.push(
                CUSTOM_FORGEJO_CONFIG,
                buf.getvalue(),
                user_id=FORGEJO_SYSTEM_USER_ID,
                user=FORGEJO_SYSTEM_USER,
                group_id=FORGEJO_SYSTEM_GROUP_ID
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
        # @TODO: Extend exception handling
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
                'NAME': self.database_name,
                'USER': data['username'],
                'PASSWD': data['password'],
                "SCHEMA": "",
                "SSL_MODE": "disable",
                "LOG_SQL": "false",
            }
            return db_data
        return {}


    def get_traefik_route_configuration(self, domain: str) -> dict:
        """Configure a route from traefik to forgejo.

        WIP
        """
        return {
            "http": {
                "routers": {
                    f"{self.model.name}-{self.model.app.name}-router": {
                        "rule": f"Host(`{domain}`)", # "ClientIP(`0.0.0.0/0`)"
                        "service": self.traefik_service_name,
                    }
                }
                "services": {
                    self.traefik_service_name: {
                        "loadBalancer": {
                            "servers": [f"http://{self.hostname}:{PORT}"],
                            "terminationDelay": -1,
                        }
                    }
                }
            }
        }


    def fetch_ingress_relation_data(self) -> str | None:
        """Fetch ingress relation data.

        We need to get the url that ingress will use and set Forgejo's ROOT url to this.
        """
        # domain = None
        # ingress_url = self.ingress.url
        # logger.debug('Got following url from ingress data: %s', ingress_url)
        # try:
        #     domain = urlparse(ingress_url).netloc
        # except Exception as e:
        #     logger.error('%s, could not parse domain from url %s', e, ingress_url)
        # return domain
        traefik_route_relation = self.model.get_relation("ingress")
        if traefik_route_relation:
            return traefik_route_relation.data[traefik_route_relation.app].get("external_host")


    def _on_storage_attached(self, _: ops.StorageAttachedEvent) -> None:
        self.container.exec(["chown", f"{FORGEJO_SYSTEM_USER}:{FORGEJO_SYSTEM_GROUP}", FORGEJO_DATA_DIR])


    @property
    def traefik_service_name(self):
        return f"{self.model.name}-{self.model.app.name}-service"


    @property
    def serving_message(self) -> str:
        if domain := self.fetch_ingress_relation_data:
            return f"Serving at {domain}"
        else
            return ""



if __name__ == "__main__":  # pragma: nocover
    ops.main(ForgejoK8SOperatorCharm)
